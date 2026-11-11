from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config
from . import powercontext_client as pc

SERVER_LOG = "powercontext_server.log"


def _log_tail(lines: int = 40) -> str:
    path = config.RUNS_DIR / SERVER_LOG
    if not path.exists():
        return "(server log not found)"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _command() -> list[str]:
    executable = shutil.which("powercontext")
    if executable:
        return [executable, "server", "run"]
    candidate = Path(sys.executable).with_name("powercontext.exe" if os.name == "nt" else "powercontext")
    if candidate.exists():
        return [str(candidate), "server", "run"]
    return [sys.executable, "-m", "powercontext.cli.app", "server", "run"]


def start() -> dict:
    if pc.health():
        return {"started": False, "url": config.POWERCONTEXT_URL, "status": "already_running"}
    config.ensure_dirs()
    config.POWERCONTEXT_HOME.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(config.POWERCONTEXT_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18080
    env = os.environ.copy()
    env.update(
        {
            "POWERCONTEXT_HOME": str(config.POWERCONTEXT_HOME),
            "POWERCONTEXT_SERVER_HTTP_HOST": host,
            "POWERCONTEXT_SERVER_HTTP_PORT": str(port),
            "POWERCONTEXT_SERVER_ACCESS_MODE": "disabled",
            "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        }
    )
    log_path = config.RUNS_DIR / SERVER_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    kwargs: dict = {
        "cwd": str(config.BASE_DIR),
        "env": env,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(_command(), **kwargs)
    (config.RUNTIME_DIR / "powercontext.pid").write_text(str(process.pid), encoding="utf-8")
    deadline = time.time() + 90
    while time.time() < deadline:
        if pc.health():
            return {
                "started": True,
                "url": config.POWERCONTEXT_URL,
                "pid": process.pid,
                "home": str(config.POWERCONTEXT_HOME),
                "log": str(log_path),
            }
        if process.poll() is not None:
            raise RuntimeError(f"PowerContext Server 启动后退出了。日志：\n{_log_tail()}")
        time.sleep(1)
    raise RuntimeError(f"PowerContext Server 90 秒内未就绪。日志：\n{_log_tail()}")


def status() -> dict:
    pid_path = config.RUNTIME_DIR / "powercontext.pid"
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else None
    return {
        "url": config.POWERCONTEXT_URL,
        "healthy": pc.health(),
        "pid": pid,
        "home": str(config.POWERCONTEXT_HOME),
        "log": str(config.RUNS_DIR / SERVER_LOG),
    }


def stop() -> dict:
    pid_path = config.RUNTIME_DIR / "powercontext.pid"
    if not pid_path.exists():
        return {"stopped": False, "reason": "no pid file"}
    pid = pid_path.read_text(encoding="utf-8").strip()
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, text=True)
        else:
            os.kill(int(pid), 15)
    finally:
        pid_path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}
