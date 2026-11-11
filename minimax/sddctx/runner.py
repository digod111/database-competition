from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from . import config


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def run_pytest(project_root: Path, args: list[str], log_name: str) -> dict:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args]
    display_command = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *args]
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = (time.perf_counter() - started) * 1000
    log_text = (
        f"$ {' '.join(display_command)}\n"
        f"cwd: {_display_path(project_root)}\n"
        f"exit_code: {result.returncode}\n"
        f"elapsed_ms: {elapsed:.1f}\n"
        "--- stdout ---\n"
        f"{result.stdout}\n"
        "--- stderr ---\n"
        f"{result.stderr}\n"
    )
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RUNS_DIR / log_name).write_text(log_text, encoding="utf-8")
    return {
        "command": " ".join(display_command),
        "cwd": _display_path(project_root),
        "exit_code": result.returncode,
        "elapsed_ms": round(elapsed, 1),
        "log": _display_path(config.RUNS_DIR / log_name),
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }


def expect_failure(result: dict, reason_fragment: str) -> None:
    if result["exit_code"] == 0:
        raise RuntimeError(f"目标测试在改前意外通过：{result['log']}")
    combined = result["stdout_tail"] + result["stderr_tail"]
    if reason_fragment not in combined:
        raise RuntimeError(
            f"目标测试失败原因不是预期的功能缺失（未找到 {reason_fragment!r}）：{result['log']}"
        )


def expect_success(result: dict) -> None:
    if result["exit_code"] != 0:
        raise RuntimeError(f"测试未通过：{result['log']}")
