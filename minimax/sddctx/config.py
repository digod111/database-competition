from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional at import time
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _env_path(name: str, default: str) -> Path:
    value = os.getenv(name)
    path = Path(value).expanduser() if value else BASE_DIR / default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


WORKSPACE_DIR = _env_path("SDD_WORKSPACE", "workspace/taskboard")
BEFORE_DIR = _env_path("SDD_BEFORE", "workspace/taskboard_before")
TASKS_DIR = _env_path("SDD_TASKS", "tasks")
DOCS_DIR = _env_path("SDD_DOCS", "docs")
RUNS_DIR = DOCS_DIR / "runs"
ARTIFACTS_DIR = _env_path("SDD_ARTIFACTS", "artifacts")
RUNTIME_DIR = _env_path("SDD_RUNTIME", ".runtime")

SEEKDB_MODE = os.getenv("SEEKDB_MODE", "server").strip().lower()
SEEKDB_HOST = os.getenv("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.getenv("SEEKDB_PASSWORD", "")
SEEKDB_DATABASE = os.getenv("SEEKDB_DATABASE", "test")
SEEKDB_DATA_DIR = _env_path("SEEKDB_DATA_DIR", ".seekdb_data")

POWERCONTEXT_URL = os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:18080").rstrip("/")
POWERCONTEXT_SCOPE_TITLE = os.getenv("POWERCONTEXT_SCOPE_TITLE", "minimax-sdd-context-demo")
POWERCONTEXT_SCOPE_KEY = os.getenv("POWERCONTEXT_SCOPE_KEY", "minimax-sdd-context-demo-v1")
POWERCONTEXT_HOME = _env_path("POWERCONTEXT_HOME", ".powercontext")

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "").strip()
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_TIMEOUT = int(os.getenv("MINIMAX_TIMEOUT", "180"))


def ensure_dirs() -> None:
    for path in (WORKSPACE_DIR, BEFORE_DIR, TASKS_DIR, DOCS_DIR, RUNS_DIR, ARTIFACTS_DIR, RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)
