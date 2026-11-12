// Browser-side embedding: Transformers.js with explicit backend selection.
// WebGPU is preferred and configured explicitly; WASM is the automatic fallback.
import { env, pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";

env.allowLocalModels = true;

export const MODEL_URL = "/models/all-MiniLM-L6-v2";
export type DType = "fp32" | "fp16" | "q8";
export type Backend = "webgpu" | "wasm";

let extractor: FeatureExtractionPipeline | null = null;
let currentDtype: DType | null = null;
let currentBackend: Backend | null = null;

export function webgpuAvailable(): boolean {
  return typeof navigator !== "undefined" && !!(navigator as Navigator & { gpu?: unknown }).gpu;
}

/**
 * Pre-flight probe: navigator.gpu can exist while requestAdapter() returns null
 * (GPU disabled, driver issues, headless without GPU). In that case onnxruntime's
 * WebGPU init can hang instead of throwing, so we probe explicitly with a timeout
 * and skip WebGPU when no adapter materializes.
 */
export async function probeWebGpu(timeoutMs = 3000): Promise<boolean> {
  if (!webgpuAvailable()) return false;
  try {
    const adapter = await Promise.race([
      (navigator as Navigator & { gpu: { requestAdapter(): Promise<unknown> } }).gpu.requestAdapter(),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ]);
    return !!adapter;
  } catch {
    return false;
  }
}

export interface LoadResult {
  backend: Backend;       // backend actually used
  fallback: boolean;      // true when WebGPU was requested but fell back to WASM
  elapsedMs: number;      // model load time (excluding download if cached)
  downloadedBytes: number;
}

/**
 * Load the model with an explicit backend preference.
 * dtype: fp32 | fp16 | q8 (q8 = official INT8 dynamic-quantized weights).
 * Returns which backend was actually used; WebGPU failures fall back to WASM.
 */
export async function loadModel(
  dtype: DType,
  preferred: Backend,
  onProgress?: (done: number, total: number) => void,
): Promise<LoadResult> {
  const t0 = performance.now();
  let downloadedBytes = 0;
  const tryDevice = async (device: Backend): Promise<Backend> => {
    if (device === "webgpu" && !webgpuAvailable()) throw new Error("navigator.gpu unavailable");
    if (extractor) await extractor.dispose?.();
    extractor = await pipeline("feature-extraction", MODEL_URL, {
      dtype,
      device,
      progress_callback: (p: { loaded?: number; total?: number }) => {
        downloadedBytes = Math.max(downloadedBytes, p.loaded ?? 0);
        if (p.total) onProgress?.(p.loaded ?? 0, p.total);
      },
    } as never);
    return device;
  };

  let backend = preferred;
  let fallback = false;
  if (preferred === "webgpu" && !(await probeWebGpu())) {
    console.warn("[embed] WebGPU adapter unavailable, falling back to wasm");
    fallback = true;
    backend = "wasm";
  }
  try {
    backend = await tryDevice(backend);
  } catch (e) {
    console.warn(`[embed] ${preferred} failed (${e instanceof Error ? e.message : e}), falling back to wasm`);
    fallback = true;
    backend = await tryDevice("wasm");
  }
  currentDtype = dtype;
  currentBackend = backend;
  return { backend, fallback, elapsedMs: performance.now() - t0, downloadedBytes };
}

export function modelState() {
  return { loaded: !!extractor, dtype: currentDtype, backend: currentBackend };
}

/** Embed texts in one batch (mean pooling + L2 normalization -> cosine-ready vectors). */
export async function embed(texts: string[]): Promise<number[][]> {
  if (!extractor) throw new Error("model not loaded");
  const out = await extractor(texts, { pooling: "mean", normalize: true });
  const list = out.tolist() as number[][];
  if (texts.length === 1 && !Array.isArray(list[0]?.[0] ?? false) && list.length === 1) return list;
  return list;
}

export async function embedOne(text: string): Promise<number[]> {
  const [v] = await embed([text]);
  return v;
}
