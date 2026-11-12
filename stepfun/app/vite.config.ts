import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root,
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/models": "http://127.0.0.1:8080",
      "/data": "http://127.0.0.1:8080",
    },
  },
  build: {
    outDir: "dist",
    target: "esnext",
  },
  optimizeDeps: {
    exclude: ["@huggingface/transformers"],
  },
});
