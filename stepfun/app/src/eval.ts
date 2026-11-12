// Quality evaluation harness: imports the labeled corpus into a dedicated seekdb
// collection using the browser's local embedding pipeline, then runs every query
// through the real hybrid/vector search and computes Recall@K and nDCG@K.
// Ground truth comes from data/queries.json (relevant doc ids per query).
import { kbAdd, kbReset, kbSearch, type SearchRow } from "./api";
import { chunkText, docId } from "./chunk";
import { embed, type DType, type Backend } from "./embed";
import { median } from "./perf";

export interface CorpusDoc { id: string; topic: string; lang: string; title: string; content: string; }
export interface EvalQuery { id: string; topic: string; text: string; lang: string; relevant: string[]; }
export interface EvalResult {
  config: { dtype: DType; backend: Backend; kb: string; corpusSize: number; queryCount: number; k: number; batch: number };
  docEmbed: { totalChunks: number; totalMs: number; docsPerSec: number; batchMs: number[] };
  dbWrite: { totalMs: number; addMs: number[] };
  queries: {
    id: string; text: string; backend: Backend; dtype: DType;
    hybridRecall: Record<number, number>; vectorRecall: Record<number, number>;
    hybridNdcg: number; vectorNdcg: number;
    exactRecall: Record<number, number>; exactNdcg: number;
    queryEmbedMs: number; hybridSearchMs: number; vectorSearchMs: number; e2eMs: number;
    rows: { hybrid: SearchRow[]; vector: SearchRow[] };
  }[];
  summary: {
    hybrid: { recall: Record<number, number>; ndcg: number };
    vector: { recall: Record<number, number>; ndcg: number };
    exact: { recall: Record<number, number>; ndcg: number };
    medianQueryEmbedMs: number; medianHybridSearchMs: number; medianVectorSearchMs: number; medianE2eMs: number;
  };
}

const KS = [1, 3, 5, 10];

function recallAtK(retrieved: string[], relevant: Set<string>, k: number): number {
  const top = retrieved.slice(0, k);
  const hits = top.filter((id) => relevant.has(id)).length;
  return relevant.size > 0 ? hits / Math.min(relevant.size, k) : 0;
}

function ndcgAtK(retrieved: string[], relevant: Set<string>, k: number): number {
  const dcg = retrieved.slice(0, k).reduce((acc, id, i) => (relevant.has(id) ? acc + 1 / Math.log2(i + 2) : acc), 0);
  const idealN = Math.min(relevant.size, k);
  const idcg = Array.from({ length: idealN }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
  return idcg > 0 ? dcg / idcg : 0;
}

const stripChunk = (id: string) => id.replace(/:\d+$/, "");

function dot(a: number[], b: number[]): number {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

export async function runEval(args: {
  kb: string;
  dtype: DType;
  backend: Backend;
  corpus: CorpusDoc[];
  queries: EvalQuery[];
  k: number;
  batch: number;
  log: (line: string) => void;
}): Promise<EvalResult> {
  const { kb, dtype, backend, corpus, queries, k, batch, log } = args;
  log(`重置集合 ${kb} ...`);
  await kbReset(kb);

  // 1) chunk + embed all docs in the browser, write to seekdb
  const items: { id: string; doc_name: string; chunk_idx: number; content: string; embedding: number[] }[] = [];
  const batchMs: number[] = [];
  let totalEmbedMs = 0;

  log(`语料 ${corpus.length} 篇 -> 分块 + 本地向量化（batch=${batch}, ${dtype}, ${backend}）...`);
  for (let i = 0; i < corpus.length; i += batch) {
    const slice = corpus.slice(i, i + batch);
    const texts: string[] = [];
    const metas: { id: string; doc_name: string; chunk_idx: number }[] = [];
    for (const d of slice) {
      const chunks = chunkText(d.content);
      for (const c of chunks) {
        texts.push(c.text);
        metas.push({ id: docId(d.id, c.idx), doc_name: d.id, chunk_idx: c.idx });
      }
    }
    const t0 = performance.now();
    const vecs = await embed(texts);
    const dt = performance.now() - t0;
    batchMs.push(dt);
    totalEmbedMs += dt;
    vecs.forEach((v, j) => items.push({ ...metas[j], content: texts[j], embedding: v }));
    log(`  batch ${i / batch + 1}/${Math.ceil(corpus.length / batch)}: ${slice.length} docs -> ${texts.length} chunks, ${dt.toFixed(0)} ms`);
  }

  // 2) write chunks to seekdb (batched)
  const addMs: number[] = [];
  let totalWriteMs = 0;
  const ADD_BATCH = 64;
  for (let i = 0; i < items.length; i += ADD_BATCH) {
    const r = await kbAdd(kb, items.slice(i, i + ADD_BATCH), i === 0 ? corpus.length : 0);
    addMs.push(r.elapsedMs);
    totalWriteMs += r.elapsedMs;
  }
  log(`写入 seekdb: ${items.length} chunks, 总 ${totalWriteMs.toFixed(0)} ms`);

  // 3) run every query through the real search pipeline
  const results: EvalResult["queries"] = [];
  for (const q of queries) {
    const relevant = new Set(q.relevant);
    const t0 = performance.now();
    const qvec = (await embed([q.text]))[0];
    const queryEmbedMs = performance.now() - t0;

    const hybrid = await kbSearch(kb, q.text, qvec, k, "hybrid");
    const vector = await kbSearch(kb, q.text, qvec, k, "vector");

    const hIds = hybrid.rows.map((r) => stripChunk(r.id));
    const vIds = vector.rows.map((r) => stripChunk(r.id));
    const hybridRecall = Object.fromEntries(KS.map((kk) => [kk, recallAtK(hIds, relevant, kk)]));
    const vectorRecall = Object.fromEntries(KS.map((kk) => [kk, recallAtK(vIds, relevant, kk)]));

    // Exact brute-force ranking over the in-memory embeddings (offline evaluation
    // only; the app's real search always runs inside seekdb). Used to isolate the
    // quantization effect from HNSW approximation noise.
    const sims = items.map((it) => dot(qvec, it.embedding));
    const exactIds = items
      .map((it, j) => ({ id: it.id, sim: sims[j] }))
      .sort((a, b) => b.sim - a.sim)
      .map((x) => stripChunk(x.id));
    const exactRecall = Object.fromEntries(KS.map((kk) => [kk, recallAtK(exactIds, relevant, kk)]));
    const exactNdcg = ndcgAtK(exactIds, relevant, 10);

    results.push({
      id: q.id,
      text: q.text,
      backend,
      dtype,
      hybridRecall,
      vectorRecall,
      hybridNdcg: ndcgAtK(hIds, relevant, 10),
      vectorNdcg: ndcgAtK(vIds, relevant, 10),
      exactRecall,
      exactNdcg,
      queryEmbedMs,
      hybridSearchMs: hybrid.elapsedMs,
      vectorSearchMs: vector.elapsedMs,
      e2eMs: queryEmbedMs + hybrid.elapsedMs,
      rows: { hybrid: hybrid.rows, vector: vector.rows },
    });
    log(`  ${q.id} 混合R@5=${hybridRecall[5].toFixed(2)} nDCG@10=${results[results.length - 1].hybridNdcg.toFixed(3)} | 纯向量R@5=${vectorRecall[5].toFixed(2)}`);
  }

  const avg = (fn: (r: EvalResult["queries"][number]) => number) => median(results.map(fn));
  const summary = {
    hybrid: {
      recall: Object.fromEntries(KS.map((kk) => [kk, median(results.map((r) => r.hybridRecall[kk]))])),
      ndcg: median(results.map((r) => r.hybridNdcg)),
    },
    vector: {
      recall: Object.fromEntries(KS.map((kk) => [kk, median(results.map((r) => r.vectorRecall[kk]))])),
      ndcg: median(results.map((r) => r.vectorNdcg)),
    },
    exact: {
      recall: Object.fromEntries(KS.map((kk) => [kk, median(results.map((r) => r.exactRecall[kk]))])),
      ndcg: median(results.map((r) => r.exactNdcg)),
    },
    medianQueryEmbedMs: avg((r) => r.queryEmbedMs),
    medianHybridSearchMs: avg((r) => r.hybridSearchMs),
    medianVectorSearchMs: avg((r) => r.vectorSearchMs),
    medianE2eMs: avg((r) => r.e2eMs),
  };

  return {
    config: { dtype, backend, kb, corpusSize: corpus.length, queryCount: queries.length, k, batch },
    docEmbed: { totalChunks: items.length, totalMs: totalEmbedMs, docsPerSec: (corpus.length * 1000) / totalEmbedMs, batchMs },
    dbWrite: { totalMs: totalWriteMs, addMs },
    queries: results,
    summary,
  };
}
