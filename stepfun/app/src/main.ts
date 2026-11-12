import { health, kbAdd, kbSearch, loadJson, type AddItem } from "./api";
import { chunkText, docId } from "./chunk";
import { embed, loadModel, modelState, probeWebGpu, webgpuAvailable, type Backend, type DType } from "./embed";
import { runEval, type CorpusDoc, type EvalQuery } from "./eval";
import { runPerf } from "./perfbench";
import { downloadJson } from "./perf";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const ui = {
  backend: $<HTMLSelectElement>("backend"),
  dtype: $<HTMLSelectElement>("dtype"),
  kbName: $<HTMLInputElement>("kb-name"),
  files: $<HTMLInputElement>("files"),
  importBtn: $<HTMLButtonElement>("btn-import"),
  corpusBtn: $<HTMLButtonElement>("btn-corpus"),
  bar: $<HTMLDivElement>("bar"),
  importLog: $<HTMLDivElement>("import-log"),
  query: $<HTMLInputElement>("query"),
  k: $<HTMLInputElement>("k"),
  mode: $<HTMLSelectElement>("mode"),
  searchBtn: $<HTMLButtonElement>("btn-search"),
  searchTiming: $<HTMLDivElement>("search-timing"),
  results: $<HTMLDivElement>("results"),
  evalKb: $<HTMLInputElement>("eval-kb"),
  evalBtn: $<HTMLButtonElement>("btn-eval"),
  perfBtn: $<HTMLButtonElement>("btn-perf"),
  perfBatch: $<HTMLSelectElement>("perf-batch"),
  evalLog: $<HTMLDivElement>("eval-log"),
  evalTable: $<HTMLDivElement>("eval-table"),
  exportBtn: $<HTMLButtonElement>("btn-export"),
  chipBackend: $<HTMLSpanElement>("chip-backend"),
  chipDtype: $<HTMLSpanElement>("chip-dtype"),
  chipModel: $<HTMLSpanElement>("chip-model"),
  chipDb: $<HTMLSpanElement>("chip-db"),
  chipKb: $<HTMLSpanElement>("chip-kb"),
};

const state = {
  dtype: ui.dtype.value as DType,
  backend: ui.backend.value as Backend,
  loaded: null as null | { dtype: DType; backend: Backend },
  lastEval: null as unknown,
  evalResults: [] as { config: Record<string, unknown>; summary: Record<string, unknown> }[],
};

ui.dtype.addEventListener("change", () => {
  state.dtype = ui.dtype.value as DType;
});
ui.backend.addEventListener("change", () => {
  state.backend = ui.backend.value as Backend;
});

function setBar(pct: number) {
  ui.bar.style.width = `${Math.min(100, pct)}%`;
}

function log(el: HTMLDivElement, line: string) {
  el.textContent += `[${new Date().toLocaleTimeString()}] ${line}\n`;
  el.scrollTop = el.scrollHeight;
}

function fmtMs(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms.toFixed(1)} ms`;
}

// ---------- model ----------
async function ensureModel(): Promise<{ dtype: DType; backend: Backend }> {
  if (state.backend === "wasm" && state.dtype === "fp16") {
    throw new Error("fp16 精度需要 WebGPU 后端（WASM 不支持 fp16），请改用 fp32 / q8 或切回 WebGPU");
  }
  const needLoad =
    state.loaded === null ||
    state.loaded.dtype !== state.dtype ||
    (state.backend === "webgpu" && state.loaded.backend !== "webgpu") ||
    (state.backend === "wasm" && state.loaded.backend !== "wasm");
  if (needLoad) {
    log(ui.importLog, `加载模型 all-MiniLM-L6-v2 (${state.dtype}, 优先 ${state.backend}) ...`);
    const r = await loadModel(state.dtype, state.backend, (done, total) => {
      if (total) setBar(done / total);
      else setBar(1);
    });
    setBar(1);
    state.loaded = { dtype: state.dtype, backend: r.backend };
    if (r.fallback) {
      log(ui.importLog, `⚠ WebGPU 不可用，已自动降级到 WASM（界面提示生效）`);
    }
    log(ui.importLog, `模型加载完成: 实际后端 ${r.backend}, 耗时 ${fmtMs(r.elapsedMs)}${r.downloadedBytes ? `, 下载 ${(r.downloadedBytes / 1e6).toFixed(1)} MB` : ""}`);
    updateChips();
  }
  return state.loaded!;
}

async function updateChips() {
  const m = modelState();
  const gpuOk = webgpuAvailable() && (await probeWebGpu());
  ui.chipBackend.innerHTML = `推理后端: <b>${gpuOk ? "WebGPU 可用" : "WebGPU 不可用，将用 WASM"}</b>${m.backend ? " / 实际 " + m.backend : ""}`;
  ui.chipBackend.className = `chip ${m.backend === "webgpu" ? "ok" : "warn"}`;
  ui.chipDtype.innerHTML = `精度: <b>${m.dtype ?? "—"}</b>`;
  ui.chipModel.innerHTML = `模型: <b>${m.loaded ? "all-MiniLM-L6-v2 已加载" : "未加载"}</b>`;
}

// ---------- import ----------
async function importTexts(texts: { name: string; content: string }[], kb: string) {
  const tTotal0 = performance.now();
  const chunksAll: { name: string; chunkIdx: number; text: string }[] = [];
  for (const t of texts) {
    for (const c of chunkText(t.content)) chunksAll.push({ name: t.name, chunkIdx: c.idx, text: c.text });
  }
  if (!chunksAll.length) {
    log(ui.importLog, "没有可导入的内容");
    return;
  }
  const { backend } = await ensureModel();

  const tEmbed0 = performance.now();
  const BATCH = 16;
  const items: AddItem[] = [];
  for (let i = 0; i < chunksAll.length; i += BATCH) {
    const slice = chunksAll.slice(i, i + BATCH);
    const vecs = await embed(slice.map((c) => c.text));
    vecs.forEach((v, j) =>
      items.push({ id: docId(slice[j].name, slice[j].chunkIdx), doc_name: slice[j].name, chunk_idx: slice[j].chunkIdx, content: slice[j].text, embedding: v }),
    );
    setBar(((i + slice.length) / chunksAll.length) * 0.9);
  }
  const embedMs = performance.now() - tEmbed0;

  setBar(0.95);
  const tWrite0 = performance.now();
  const ADD_BATCH = 64;
  let writeMs = 0;
  for (let i = 0; i < items.length; i += ADD_BATCH) {
    const r = await kbAdd(kb, items.slice(i, i + ADD_BATCH), i === 0 ? texts.length : 0);
    writeMs += r.elapsedMs;
  }
  const totalMs = performance.now() - tTotal0;
  setBar(1);
  log(ui.importLog, `导入完成: ${texts.length} 文档 -> ${chunksAll.length} 块 | 向量化 ${fmtMs(embedMs)} (${((chunksAll.length * 1000) / embedMs).toFixed(1)} 块/s, ${backend}) | 写入 seekdb ${fmtMs(writeMs)} | 总计 ${fmtMs(totalMs)}`);
  log(ui.importLog, `增量导入完成，新文档已在集合「${kb}」中可检索`);
  setTimeout(() => setBar(0), 1500);
  refreshDbChip();
}

ui.importBtn.addEventListener("click", async () => {
  const files = [...(ui.files.files ?? [])];
  if (!files.length) {
    log(ui.importLog, "请先选择文件");
    return;
  }
  ui.importBtn.disabled = true;
  try {
    const texts = await Promise.all(files.map(async (f) => ({ name: f.name, content: await f.text() })));
    await importTexts(texts, ui.kbName.value.trim() || "main");
  } catch (e) {
    log(ui.importLog, `导入失败: ${e instanceof Error ? e.message : e}`);
  } finally {
    ui.importBtn.disabled = false;
  }
});

ui.corpusBtn.addEventListener("click", async () => {
  ui.corpusBtn.disabled = true;
  try {
    log(ui.importLog, "加载示例语料 (data/corpus.json, 200 篇, 中英混合) ...");
    const corpus = await loadJson<{ docs: CorpusDoc[] }>("/data/corpus.json");
    const texts = corpus.docs.map((d) => ({ name: d.id, content: `${d.title}\n\n${d.content}` }));
    await importTexts(texts, ui.kbName.value.trim() || "main");
  } catch (e) {
    log(ui.importLog, `语料导入失败: ${e instanceof Error ? e.message : e}`);
  } finally {
    ui.corpusBtn.disabled = false;
  }
});

// ---------- search ----------
function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function highlight(text: string, query: string): string {
  const esc = escapeHtml(text);
  const terms = query.split(/[\s，。！？、；：""''（）()\[\]…—]+/).filter((t) => t.length >= 2);
  if (!terms.length) return esc;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return esc.replace(re, "<mark>$1</mark>");
}

ui.searchBtn.addEventListener("click", async () => {
  const q = ui.query.value.trim();
  if (!q) return;
  ui.searchBtn.disabled = true;
  try {
    const t0 = performance.now();
    const { backend, dtype } = await ensureModel();
    const [vec] = await embed([q]);
    const queryEmbedMs = performance.now() - t0;
    const k = Math.max(1, Number(ui.k.value) || 5);
    const mode = ui.mode.value as "hybrid" | "vector";
    const r = await kbSearch(ui.kbName.value.trim() || "main", q, vec, k, mode);
    const e2e = performance.now() - t0;

    ui.searchTiming.textContent =
      `查询向量化(本地, ${backend}/${dtype}): ${fmtMs(queryEmbedMs)} | seekdb 检索(${mode}, 一条 SQL): ${fmtMs(r.elapsedMs)} | 端到端: ${fmtMs(e2e)}`;
    ui.results.innerHTML = r.rows.length
      ? r.rows
          .map(
            (row, i) => `
      <div class="result">
        <div class="meta"><b>#${i + 1}</b><span>${escapeHtml(row.id)}</span><span>文档: ${escapeHtml(row.doc_name)}</span><span>块 ${row.chunk_idx}</span><span>相似度 ${(1 - row.dist).toFixed(4)} (cos_dist ${row.dist.toFixed(4)})</span></div>
        <div class="txt">${highlight(row.content, q)}</div>
      </div>`,
          )
          .join("")
      : `<div class="muted">无结果（${mode === "hybrid" ? "全文过滤后无匹配；可尝试纯向量模式或更换关键词" : "集合为空或未导入语料"}）</div>`;
  } catch (e) {
    ui.results.innerHTML = `<div class="muted">检索失败: ${escapeHtml(e instanceof Error ? e.message : String(e))}</div>`;
  } finally {
    ui.searchBtn.disabled = false;
  }
});

ui.query.addEventListener("keydown", (e) => {
  if (e.key === "Enter") ui.searchBtn.click();
});

// ---------- eval ----------
ui.evalBtn.addEventListener("click", async () => {
  ui.evalBtn.disabled = true;
  try {
    await ensureModel();
    const corpus = (await loadJson<{ docs: CorpusDoc[] }>("/data/corpus.json")).docs;
    const queries = (await loadJson<{ queries: EvalQuery[] }>("/data/queries.json")).queries;
    const kb = ui.evalKb.value.trim() || `eval-${state.dtype}-${state.backend}`;
    const result = await runEval({
      kb,
      dtype: state.dtype,
      backend: state.backend,
      corpus,
      queries,
      k: 10,
      batch: 16,
      log: (line) => log(ui.evalLog, line),
    });
    state.lastEval = result;
    state.evalResults.push({ config: result.config as unknown as Record<string, unknown>, summary: result.summary as unknown as Record<string, unknown> });
    renderEvalTable(result);
    try {
      const saved = await fetch("/api/kb/evallog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kb,
          payload: { userAgent: navigator.userAgent, webgpuDetected: webgpuAvailable(), run: result },
        }),
      }).then((r) => r.json());
      log(ui.evalLog, `原始数据已保存: ${saved.file}`);
    } catch {
      log(ui.evalLog, "原始数据保存失败（不影响评测结果）");
    }
    log(ui.evalLog, `评测完成: 混合 R@5=${result.summary.hybrid.recall[5].toFixed(3)} nDCG@10=${result.summary.hybrid.ndcg.toFixed(3)} | 精确排序 R@5=${result.summary.exact.recall[5].toFixed(3)} nDCG@10=${result.summary.exact.ndcg.toFixed(3)}`);
  } catch (e) {
    log(ui.evalLog, `评测失败: ${e instanceof Error ? e.message : e}`);
  } finally {
    ui.evalBtn.disabled = false;
  }
});

function renderEvalTable(r: { config: { dtype: string; backend: string; kb: string; corpusSize: number; queryCount: number }; summary: { hybrid: { recall: Record<number, number>; ndcg: number }; vector: { recall: Record<number, number>; ndcg: number }; exact: { recall: Record<number, number>; ndcg: number }; medianQueryEmbedMs: number; medianHybridSearchMs: number; medianE2eMs: number }; docEmbed: { totalChunks: number; totalMs: number; docsPerSec: number }; dbWrite: { totalMs: number } }) {
  const s = r.summary;
  ui.evalTable.innerHTML = `
    <table>
      <tr><th>配置</th><td>${r.config.kb} · ${r.config.dtype} · ${r.config.backend} · 语料 ${r.config.corpusSize} 篇 · 查询 ${r.config.queryCount} 条</td></tr>
      <tr><th>文档向量化</th><td>${r.docEmbed.totalChunks} 块 / ${fmtMs(r.docEmbed.totalMs)}（${r.docEmbed.docsPerSec.toFixed(1)} 篇/s）</td></tr>
      <tr><th>写入 seekdb</th><td>${fmtMs(r.dbWrite.totalMs)}</td></tr>
      <tr><th>混合检索</th><td>R@1 ${s.hybrid.recall[1].toFixed(3)} · R@3 ${s.hybrid.recall[3].toFixed(3)} · R@5 ${s.hybrid.recall[5].toFixed(3)} · R@10 ${s.hybrid.recall[10].toFixed(3)} · nDCG@10 ${s.hybrid.ndcg.toFixed(3)}</td></tr>
      <tr><th>纯向量 ANN</th><td>R@1 ${s.vector.recall[1].toFixed(3)} · R@3 ${s.vector.recall[3].toFixed(3)} · R@5 ${s.vector.recall[5].toFixed(3)} · R@10 ${s.vector.recall[10].toFixed(3)} · nDCG@10 ${s.vector.ndcg.toFixed(3)}</td></tr>
      <tr><th>精确排序(量化对照)</th><td>R@1 ${s.exact.recall[1].toFixed(3)} · R@3 ${s.exact.recall[3].toFixed(3)} · R@5 ${s.exact.recall[5].toFixed(3)} · R@10 ${s.exact.recall[10].toFixed(3)} · nDCG@10 ${s.exact.ndcg.toFixed(3)}</td></tr>
      <tr><th>延迟(中位)</th><td>查询向量化 ${fmtMs(s.medianQueryEmbedMs)} · seekdb 混合检索 ${fmtMs(s.medianHybridSearchMs)} · 端到端 ${fmtMs(s.medianE2eMs)}</td></tr>
    </table>`;
}

ui.perfBtn.addEventListener("click", async () => {
  ui.perfBtn.disabled = true;
  try {
    await ensureModel();
    const corpus = (await loadJson<{ docs: CorpusDoc[] }>("/data/corpus.json")).docs;
    const batch = Number(ui.perfBatch.value) || 16;
    const kb = `perf-${state.dtype}-${state.backend}-b${batch}`;
    const result = await runPerf({
      kb,
      dtype: state.dtype,
      backend: state.backend,
      corpus,
      queryText: "HNSW 索引是如何组织向量并完成近邻搜索的？",
      batch,
      repeats: 5,
      log: (line) => log(ui.evalLog, line),
    });
    state.lastEval = result;
    try {
      const saved = await fetch("/api/kb/evallog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kb, payload: { userAgent: navigator.userAgent, webgpuDetected: webgpuAvailable(), run: result } }),
      }).then((r) => r.json());
      log(ui.evalLog, `原始数据已保存: ${saved.file}`);
    } catch {
      log(ui.evalLog, "原始数据保存失败");
    }
    log(ui.evalLog, `性能测试完成: 查询向量化 首次 ${fmtMs(result.summary.queryEmbed.firstMs)} / 预热后中位 ${fmtMs(result.summary.queryEmbed.warmMedianMs)} | seekdb 检索 首次 ${fmtMs(result.summary.seekdbSearch.firstMs)} / 中位 ${fmtMs(result.summary.seekdbSearch.warmMedianMs)}`);
  } catch (e) {
    log(ui.evalLog, `性能测试失败: ${e instanceof Error ? e.message : e}`);
  } finally {
    ui.perfBtn.disabled = false;
  }
});

ui.exportBtn.addEventListener("click", () => {
  if (!state.lastEval) {
    log(ui.evalLog, "请先运行评测");
    return;
  }
  downloadJson(`stepfun-eval-${state.dtype}-${state.backend}-${Date.now()}.json`, {
    exportedAt: new Date().toISOString(),
    userAgent: navigator.userAgent,
    webgpu: webgpuAvailable(),
    run: state.lastEval,
  });
});

// Reproducible console hooks: run the full eval/perf matrix and persist raw data.
// Usage: open DevTools console -> __runEvalMatrix() / __runPerfMatrix()
async function saveEvalLog(kb: string, result: unknown) {
  try {
    const saved = await fetch("/api/kb/evallog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb, payload: { userAgent: navigator.userAgent, webgpuDetected: webgpuAvailable(), run: result } }),
    }).then((r) => r.json());
    log(ui.evalLog, `原始数据已保存: ${saved.file}`);
  } catch {
    log(ui.evalLog, "原始数据保存失败");
  }
}

(window as unknown as { __runEvalMatrix: () => Promise<void> }).__runEvalMatrix = async () => {
  const corpus = (await loadJson<{ docs: CorpusDoc[] }>("/data/corpus.json")).docs;
  const queries = (await loadJson<{ queries: EvalQuery[] }>("/data/queries.json")).queries;
  const cfgs: [DType, Backend][] = [
    ["q8", "webgpu"],
    ["q8", "wasm"],
    ["fp32", "webgpu"],
    ["fp32", "wasm"],
    ["fp16", "webgpu"],
  ];
  for (const [dtype, backend] of cfgs) {
    state.dtype = dtype;
    state.backend = backend;
    ui.dtype.value = dtype;
    ui.backend.value = backend;
    const kb = `eval-${dtype}-${backend}`;
    log(ui.evalLog, `==== 矩阵评测开始 ${dtype}/${backend} ====`);
    await ensureModel();
    const result = await runEval({ kb, dtype, backend, corpus, queries, k: 10, batch: 16, log: (l) => log(ui.evalLog, l) });
    renderEvalTable(result);
    await saveEvalLog(kb, result);
    log(ui.evalLog, `==== 矩阵评测完成 ${dtype}/${backend}：混合 R@5=${result.summary.hybrid.recall[5].toFixed(3)}，精确 R@5=${result.summary.exact.recall[5].toFixed(3)} ====`);
  }
  log(ui.evalLog, "全部矩阵评测完成");
};

(window as unknown as { __runPerfMatrix: () => Promise<void> }).__runPerfMatrix = async () => {
  const corpus = (await loadJson<{ docs: CorpusDoc[] }>("/data/corpus.json")).docs;
  const cfgs: [DType, Backend, number][] = [
    ["q8", "webgpu", 16],
    ["q8", "webgpu", 64],
    ["q8", "wasm", 16],
    ["q8", "wasm", 64],
    ["fp32", "webgpu", 16],
  ];
  for (const [dtype, backend, batch] of cfgs) {
    state.dtype = dtype;
    state.backend = backend;
    ui.dtype.value = dtype;
    ui.backend.value = backend;
    const kb = `perf-${dtype}-${backend}-b${batch}`;
    log(ui.evalLog, `==== 性能测试开始 ${dtype}/${backend} batch=${batch} ====`);
    await ensureModel();
    const result = await runPerf({
      kb,
      dtype,
      backend,
      corpus,
      queryText: "HNSW 索引是如何组织向量并完成近邻搜索的？",
      batch,
      repeats: 5,
      log: (l) => log(ui.evalLog, l),
    });
    await saveEvalLog(kb, result);
    log(ui.evalLog, `==== 性能测试完成 ${dtype}/${backend} b${batch}：查询向量化 首次 ${result.summary.queryEmbed.firstMs.toFixed(1)}ms / 预热中位 ${result.summary.queryEmbed.warmMedianMs.toFixed(1)}ms ====`);
  }
  log(ui.evalLog, "全部性能测试完成");
};

(window as unknown as { __runPerfBatch1: () => Promise<void> }).__runPerfBatch1 = async () => {
  const corpus = (await loadJson<{ docs: CorpusDoc[] }>("/data/corpus.json")).docs;
  const cfgs: [DType, Backend][] = [
    ["q8", "webgpu"],
    ["q8", "wasm"],
  ];
  for (const [dtype, backend] of cfgs) {
    state.dtype = dtype;
    state.backend = backend;
    ui.dtype.value = dtype;
    ui.backend.value = backend;
    const kb = `perf-${dtype}-${backend}-b1`;
    log(ui.evalLog, `==== 性能测试(逐条对照) ${dtype}/${backend} batch=1 ====`);
    await ensureModel();
    const result = await runPerf({
      kb,
      dtype,
      backend,
      corpus,
      queryText: "HNSW 索引是如何组织向量并完成近邻搜索的？",
      batch: 1,
      repeats: 5,
      log: (l) => log(ui.evalLog, l),
    });
    await saveEvalLog(kb, result);
    log(ui.evalLog, `==== 逐条对照完成 ${dtype}/${backend} ====`);
  }
  log(ui.evalLog, "逐条对照全部完成");
};

// ---------- boot ----------
async function refreshDbChip() {  try {
    const h = await health();
    if (h.ok) {
      ui.chipDb.innerHTML = `seekdb: <b>${h.version}</b> @ ${h.host}:${h.port}/${h.database}`;
      ui.chipDb.className = "chip ok";
      const kb = ui.kbName.value.trim() || "main";
      const st = (h.kbStates ?? []).find((s: { kb_name: string }) => s.kb_name === kb);
      ui.chipKb.innerHTML = `集合: <b>${kb}</b>${st ? ` (${st.doc_count} 文档 / ${st.chunk_count} 块)` : " (空)"}`;
    } else {
      ui.chipDb.innerHTML = `seekdb: <b>连接失败</b> ${h.error ?? ""}`;
      ui.chipDb.className = "chip err";
    }
  } catch (e) {
    ui.chipDb.innerHTML = `seekdb: <b>连接失败</b> ${e instanceof Error ? e.message : e}`;
    ui.chipDb.className = "chip err";
  }
}

updateChips();
refreshDbChip();
ui.kbName.addEventListener("change", refreshDbChip);
setInterval(refreshDbChip, 5000);
