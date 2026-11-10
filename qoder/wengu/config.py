from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
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


DOCS_DIR = _env_path("WENGU_DOCS_DIR", "docs")
RUNS_DIR = DOCS_DIR / "runs"
RUNTIME_DIR = _env_path("WENGU_RUNTIME_DIR", ".runtime")
SEED_DIR = _env_path("WENGU_SEED_DIR", "seed")
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

SEEKDB_HOST = os.getenv("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.getenv("SEEKDB_PASSWORD", "")
SEEKDB_DATABASE = os.getenv("SEEKDB_DATABASE", "qoder_app")

POWERCONTEXT_URL = os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:18081").rstrip("/")
POWERCONTEXT_HOME = _env_path("POWERCONTEXT_HOME", ".powercontext")
POWERCONTEXT_SCOPE_PREFIX = os.getenv("POWERCONTEXT_SCOPE_PREFIX", "wengu")

WENGU_HOST = os.getenv("WENGU_HOST", "127.0.0.1")
WENGU_PORT = int(os.getenv("WENGU_PORT", "8000"))
WENGU_DEFAULT_USER = os.getenv("WENGU_DEFAULT_USER", "demo")


def ensure_dirs() -> None:
    for path in (RUNS_DIR, RUNTIME_DIR, POWERCONTEXT_HOME):
        path.mkdir(parents=True, exist_ok=True)
