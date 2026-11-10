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
from .text import append_jsonl, now_iso

SERVER_LOG = config.RUNS_DIR / "powercontext_server.log"


def _log_tail(lines: int = 60) -> str:
    if not SERVER_LOG.exists():
        return "(server log not found)"
    return "\n".join(SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _command() -> list[str]:
    executable = shutil.which("powercontext")
    if executable:
        return [executable, "server", "run"]
    candidate = Path(sys.executable).with_name("powercontext.exe" if os.name == "nt" else "powercontext")
    if candidate.exists():
        return [str(candidate), "server", "run"]
    return [sys.executable, "-m", "powercontext.cli.app", "server", "run"]


def _port() -> int:
    return urlparse(config.POWERCONTEXT_URL).port or 18081


def _host() -> str:
    return urlparse(config.POWERCONTEXT_URL).hostname or "127.0.0.1"


def _pid_file() -> Path:
    return config.RUNTIME_DIR / "powercontext.pid"


def _pid_for_port(port: int) -> str | None:
    try:
        if os.name == "nt":
            output = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 5 and f":{port}" in parts[1] and parts[-1].isdigit() and "LISTENING" in line.upper():
                    return parts[-1]
        else:
            output = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True, timeout=10).stdout
            for line in output.splitlines():
                if line.strip().isdigit():
                    return line.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def start() -> dict:
    if pc.health():
        return {"started": False, "url": config.POWERCONTEXT_URL, "status": "already_running"}
    config.ensure_dirs()
    config.POWERCONTEXT_HOME.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "POWERCONTEXT_HOME": str(config.POWERCONTEXT_HOME),
            "POWERCONTEXT_SERVER_HTTP_HOST": _host(),
            "POWERCONTEXT_SERVER_HTTP_PORT": str(_port()),
            "POWERCONTEXT_SERVER_ACCESS_MODE": "disabled",
            "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        }
    )
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = SERVER_LOG.open("a", encoding="utf-8")
    handle.write(f"\n===== start {now_iso()} =====\n")
    handle.flush()
    kwargs: dict = {"cwd": str(config.BASE_DIR), "env": env, "stdout": handle, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(_command(), **kwargs)
    _pid_file().write_text(str(process.pid), encoding="utf-8")
    deadline = time.time() + 90
    while time.time() < deadline:
        if pc.health():
            result = {
                "started": True,
                "url": config.POWERCONTEXT_URL,
                "pid": process.pid,
                "home": str(config.POWERCONTEXT_HOME),
                "log": str(SERVER_LOG),
            }
            append_jsonl(config.RUNS_DIR / "powercontext_restart.jsonl", {"time": now_iso(), "action": "start", **result})
            return result
        if process.poll() is not None:
            raise RuntimeError(f"PowerContext Server 启动后退出了。日志：\n{_log_tail()}")
        time.sleep(1)
    raise RuntimeError(f"PowerContext Server 90 秒内未就绪。日志：\n{_log_tail()}")


def status() -> dict:
    pid = _pid_file().read_text(encoding="utf-8").strip() if _pid_file().exists() else None
    port_pid = _pid_for_port(_port())
    return {
        "url": config.POWERCONTEXT_URL,
        "healthy": pc.health(),
        "pid": pid,
        "port_pid": port_pid,
        "home": str(config.POWERCONTEXT_HOME),
        "log": str(SERVER_LOG),
    }


def stop() -> dict:
    pid = None
    if _pid_file().exists():
        pid = _pid_file().read_text(encoding="utf-8").strip()
    if not pid:
        pid = _pid_for_port(_port())
    if not pid:
        return {"stopped": False, "reason": "no pid file and no listener", "url": config.POWERCONTEXT_URL}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=20)
        else:
            os.kill(int(pid), 15)
    finally:
        _pid_file().unlink(missing_ok=True)
    deadline = time.time() + 20
    while time.time() < deadline and pc.health():
        time.sleep(0.5)
    result = {"stopped": True, "pid": str(pid), "url": config.POWERCONTEXT_URL, "healthy_after": pc.health()}
    append_jsonl(config.RUNS_DIR / "powercontext_restart.jsonl", {"time": now_iso(), "action": "stop", **result})
    return result


def restart() -> dict:
    before = status()
    stopped = stop()
    started = start()
    after = status()
    result = {
        "before": before,
        "stopped": stopped,
        "started": started,
        "after": after,
        "home": str(config.POWERCONTEXT_HOME),
        "health": pc.health(),
        "restarted_at": now_iso(),
    }
    append_jsonl(config.RUNS_DIR / "powercontext_restart.jsonl", {"time": now_iso(), "action": "restart", **result})
    return result
