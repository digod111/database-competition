// Dev runner: starts the forwarding server (8080) and the Vite app (5173) together.
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const isWin = process.platform === "win32";
const npm = isWin ? "npm.cmd" : "npm";

const procs = [
  spawn(npm, ["--prefix", "server", "start"], { cwd: root, stdio: "inherit", shell: isWin }),
  spawn(npm, ["--prefix", "app", "run", "dev"], { cwd: root, stdio: "inherit", shell: isWin }),
];

for (const p of procs) {
  p.on("exit", (code) => {
    console.log(`[dev] process exited (${code}), shutting down...`);
    for (const q of procs) if (!q.killed) q.kill();
    process.exit(code ?? 0);
  });
}
