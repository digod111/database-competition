#!/usr/bin/env node
// Download all-MiniLM-L6-v2 model weights (fp32 / fp16 / INT8-q8) for local use.
// Tries Hugging Face first, falls back to hf-mirror.com (HF_ENDPOINT overrides).
// Files land in <repo>/models/all-MiniLM-L6-v2/ (gitignored; see README for cache notes).
import { createWriteStream, existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "models", "all-MiniLM-L6-v2");
const REPO = "Xenova/all-MiniLM-L6-v2";
const FILES = [
  "config.json",
  "tokenizer.json",
  "tokenizer_config.json",
  "special_tokens_map.json",
  "vocab.txt",
  "onnx/model.onnx",            // fp32 baseline
  "onnx/model_fp16.onnx",       // FP16
  "onnx/model_quantized.onnx",  // INT8 dynamic (q8)
];

const ENDPOINTS = [
  process.env.HF_ENDPOINT,
  "https://huggingface.co",
  "https://hf-mirror.com",
].filter(Boolean);

async function download(base, file, dest) {
  const url = `${base}/${REPO}/resolve/main/${file}`;
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status} for ${url}`);
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
}

async function sizeOf(p) { return existsSync(p) ? statSync(p).size : 0; }let ok = true;
for (const file of FILES) {
  const dest = join(OUT, file);
  mkdirSync(dirname(dest), { recursive: true });
  for (const base of ENDPOINTS) {
    try {
      console.log(`fetching ${file} from ${base} ...`);
      await download(base, file, dest);
      console.log(`  ok (${statSync(dest).size} bytes)`);
      ok = ok && true;
      break;
    } catch (e) {
      console.log(`  failed via ${base}: ${e.message}`);
      if (base === ENDPOINTS[ENDPOINTS.length - 1]) { ok = false; console.error(`  FATAL: no mirror worked for ${file}`); }
    }
  }
}

if (ok) {
  console.log(`\ndone. models in ${OUT}`);
  console.log("expected sizes: model.onnx ~90.4MB, model_fp16.onnx ~45.3MB, model_quantized.onnx ~23.0MB");
} else {
  process.exit(1);
}
