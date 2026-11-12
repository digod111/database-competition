// Performance benchmark: fixed corpus + fixed query, repeated sampling, raw data kept.
// Compares batch sizes (embedding batching) and reports per-stage timings,
// first-run vs warm (median of repeats 2..n) for query-stage latencies.
import { kbAdd, kbReset, kbSearch } from "./api";
import { chunkText, docId } from "./chunk";
import { embed, type DType, type Backend } from "./embed";
import { median } from "./perf";

export interface PerfResult {
  config: {
    dtype: DType; backend: Backend; kb: string;
    corpusSize: number; batch: number; repeats: number; queryText: string;
  };
  docEmbed: { totalChunks: number; totalMs: number; docsPerSec: number; batchMs: number[] };
  dbWrite: { totalMs: number; addMs: number[] };
  queries: {
    run: number;
    queryEmbedMs: number;
    searchMs: number;
    e2eMs: number;
    rowCount: number;
  }[];
  summary: {
    queryEmbed: { firstMs: number; warmMedianMs: number };
    seekdbSearch: { firstMs: number; warmMedianMs: number };
    e2e: { firstMs: number; warmMedianMs: number };
  };
}

export async function runPerf(args: {
  kb: string;
  dtype: DType;
  backend: Backend;
  corpus: { id: string; content: string }[];
  queryText: string;
  batch: number;
  repeats: number;
  log: (line: string) => void;
}): Promise<PerfResult> {
  const { kb, dtype, backend, corpus, queryText, batch, repeats, log } = args;
  await kbReset(kb);

  const items: { id: string; doc_name: string; chunk_idx: number; content: string; embedding: number[] }[] = [];
  const batchMs: number[] = [];
  let totalEmbedMs = 0;
  log(`性能测试: ${dtype}/${backend}, batch=${batch}, 语料 ${corpus.length} 篇`);
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
  }

  const addMs: number[] = [];
  let totalWriteMs = 0;
  for (let i = 0; i < items.length; i += 64) {
    const r = await kbAdd(kb, items.slice(i, i + 64), i === 0 ? corpus.length : 0);
    addMs.push(r.elapsedMs);
    totalWriteMs += r.elapsedMs;
  }
  log(`  文档向量化 ${totalEmbedMs.toFixed(0)} ms (${((corpus.length * 1000) / totalEmbedMs).toFixed(1)} 篇/s) | 写入 ${totalWriteMs.toFixed(0)} ms`);

  const qvec = (await embed([queryText]))[0];
  const queries: PerfResult["queries"] = [];
  for (let r = 1; r <= repeats; r++) {
    const t0 = performance.now();
    await embed([queryText]);
    const queryEmbedMs = performance.now() - t0;
    const t1 = performance.now();
    const res = await kbSearch(kb, queryText, qvec, 10, "hybrid");
    const searchMs = performance.now() - t1;
    const e2eMs = performance.now() - t0;
    queries.push({ run: r, queryEmbedMs, searchMs: res.elapsedMs, e2eMs, rowCount: res.rows.length });
    log(`  run ${r}/${repeats}: 查询向量化 ${queryEmbedMs.toFixed(1)} ms | seekdb ${res.elapsedMs.toFixed(1)} ms | e2e ${e2eMs.toFixed(1)} ms`);
  }

  const warm = (arr: number[]) => median(arr.slice(1));
  return {
    config: { dtype, backend, kb, corpusSize: corpus.length, batch, repeats, queryText },
    docEmbed: { totalChunks: items.length, totalMs: totalEmbedMs, docsPerSec: (corpus.length * 1000) / totalEmbedMs, batchMs },
    dbWrite: { totalMs: totalWriteMs, addMs },
    queries,
    summary: {
      queryEmbed: { firstMs: queries[0].queryEmbedMs, warmMedianMs: warm(queries.map((q) => q.queryEmbedMs)) },
      seekdbSearch: { firstMs: queries[0].searchMs, warmMedianMs: warm(queries.map((q) => q.searchMs)) },
      e2e: { firstMs: queries[0].e2eMs, warmMedianMs: warm(queries.map((q) => q.e2eMs)) },
    },
  };
}
