// Thin forwarding layer between the browser app and seekdb.
// The browser computes all embeddings locally; this server only:
//   - inserts (vector, text, metadata) rows into seekdb via seekdb-js
//   - runs the single hybrid-search SQL inside seekdb and returns rows
// No retrieval, ranking, or embedding logic lives here.
import { mkdirSync, writeFileSync } from "node:fs";
import cors from "cors";
import express from "express";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { SeekdbClient } from "seekdb";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STEPFUN_ROOT = path.resolve(__dirname, "../..");

const HOST = process.env.SEEKDB_HOST || "127.0.0.1";
const PORT = Number(process.env.SEEKDB_PORT || 2881);
const USER = process.env.SEEKDB_USER || "root";
const PASSWORD = process.env.SEEKDB_PASSWORD || "";
const DATABASE = process.env.SEEKDB_DATABASE || "stepfun_app";
const HTTP_PORT = Number(process.env.PORT || 8080);
const KB_RE = /^[a-zA-Z0-9_-]{1,64}$/;

const client = new SeekdbClient({ host: HOST, port: PORT, user: USER, password: PASSWORD, database: DATABASE });

const app = express();
app.use(cors());
app.use(express.json({ limit: "64mb" }));

// Static assets served locally: model weights (browser loads them, inference stays on-device)
// and the reproducible corpus/query data used by import & evaluation.
app.use("/models", express.static(path.join(STEPFUN_ROOT, "models"), { maxAge: "1h" }));
app.use("/data", express.static(path.join(STEPFUN_ROOT, "data"), { maxAge: 0 }));

// Production mode: serve the built frontend (npm run build in app/).
app.use(express.static(path.join(STEPFUN_ROOT, "app", "dist"), { maxAge: 0 }));

const timings = {};

app.get("/api/health", async (_req, res) => {
  try {
    const [v] = await client.execute("SELECT version() AS v");
    const rows = await client.execute("SELECT kb_name, doc_count, chunk_count, model_dtype, infer_backend FROM kb_state");
    res.json({ ok: true, version: v?.v, host: HOST, port: PORT, database: DATABASE, kbStates: rows ?? [] });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e?.message || e) });
  }
});

// Insert/upsert embedded chunks (batch). Embeddings were computed in the browser.
app.post("/api/kb/add", async (req, res) => {
  const t0 = performance.now();
  const { kb, items, docCount = 0 } = req.body ?? {};
  if (!kb || !KB_RE.test(kb)) return res.status(400).json({ error: "invalid kb name" });
  if (!Array.isArray(items) || items.length === 0) return res.status(400).json({ error: "items required" });

  try {
    let inserted = 0;
    const BATCH = 64;
    for (let i = 0; i < items.length; i += BATCH) {
      const slice = items.slice(i, i + BATCH);
      const values = [];
      const params = [];
      for (const it of slice) {
        values.push("(?, ?, ?, ?, ?, ?)");
        const emb = Array.isArray(it.embedding) ? `[${it.embedding.join(",")}]` : String(it.embedding);
        params.push(kb, String(it.id), String(it.doc_name ?? ""), it.chunk_idx ?? 0, String(it.content), emb);
      }
      const sql =
        `INSERT INTO docs (kb, id, doc_name, chunk_idx, content, embedding) VALUES ${values.join(",")} ` +
        `ON DUPLICATE KEY UPDATE doc_name=VALUES(doc_name), chunk_idx=VALUES(chunk_idx), content=VALUES(content), embedding=VALUES(embedding)`;
      await client.execute(sql, params);
      inserted += slice.length;
    }
    await client.execute(
      `INSERT INTO kb_state (kb_name, doc_count, chunk_count) VALUES (?, ?, ?) ` +
        `ON DUPLICATE KEY UPDATE doc_count = doc_count + ?, chunk_count = chunk_count + ?`,
      [kb, docCount, inserted, docCount, inserted],
    );
    const elapsedMs = Math.round((performance.now() - t0) * 10) / 10;
    (timings.add ??= []).push(elapsedMs);
    res.json({ ok: true, inserted, elapsedMs });
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) });
  }
});

// Hybrid search: one SQL statement executed by seekdb (vector ANN + IK fulltext).
app.post("/api/kb/search", async (req, res) => {
  const t0 = performance.now();
  const { kb, queryText, vector, k = 5, mode = "hybrid" } = req.body ?? {};
  if (!kb || !KB_RE.test(kb)) return res.status(400).json({ error: "invalid kb name" });
  if (!vector || typeof queryText !== "string") return res.status(400).json({ error: "vector and queryText required" });

  try {
    const sql =
      mode === "vector"
        ? "SELECT id, doc_name, chunk_idx, content, cosine_distance(embedding, ?) AS dist FROM docs WHERE kb = ? ORDER BY dist APPROXIMATE LIMIT ?"
        : "SELECT id, doc_name, chunk_idx, content, cosine_distance(embedding, ?) AS dist FROM docs WHERE kb = ? AND MATCH(content) AGAINST(?) ORDER BY dist APPROXIMATE LIMIT ?";
    const params = mode === "vector" ? [vector, kb, Number(k)] : [vector, kb, String(queryText), Number(k)];
    const rows = await client.execute(sql, params);
    const elapsedMs = Math.round((performance.now() - t0) * 10) / 10;
    (timings.search ??= []).push(elapsedMs);
    res.json({
      ok: true,
      mode,
      rows: (rows ?? []).map((r) => ({ id: r.id, doc_name: r.doc_name, chunk_idx: r.chunk_idx, content: r.content, dist: r.dist })),
      elapsedMs,
    });
  } catch (e) {
    console.error("[search]", e?.message || e);
    res.status(500).json({ error: String(e?.message || e) });
  }
});

// Drop all chunks of a knowledge base (used by eval runs and reset).
app.post("/api/kb/reset", async (req, res) => {
  const { kb } = req.body ?? {};
  if (!kb || !KB_RE.test(kb)) return res.status(400).json({ error: "invalid kb name" });
  try {
    await client.execute("DELETE FROM docs WHERE kb = ?", [kb]);
    await client.execute("DELETE FROM kb_state WHERE kb_name = ?", [kb]);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) });
  }
});

// Persist a client-computed eval/perf result as raw data for the report.
// The payload is produced entirely by the browser app (no inference here).
app.post("/api/kb/evallog", async (req, res) => {
  const { kb, payload } = req.body ?? {};
  if (!kb || !KB_RE.test(kb)) return res.status(400).json({ error: "invalid kb name" });
  try {
    const dir = path.join(STEPFUN_ROOT, "data", "eval-results");
    mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${kb}-${Date.now()}.json`);
    writeFileSync(file, JSON.stringify({ savedAt: new Date().toISOString(), kb, ...payload }, null, 2));
    res.json({ ok: true, file: path.relative(STEPFUN_ROOT, file).replaceAll("\\", "/") });
  } catch (e) {
    res.status(500).json({ error: String(e?.message || e) });
  }
});

app.listen(HTTP_PORT, () => {
  console.log(`[stepfun-server] listening on http://127.0.0.1:${HTTP_PORT}, forwarding to seekdb at ${HOST}:${PORT}/${DATABASE}`);
});
