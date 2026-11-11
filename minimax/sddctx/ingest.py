from __future__ import annotations

from pathlib import Path

from . import config, db
from .chunker import collect_chunks
from .text import now_iso, write_json


def ingest(root: Path | None = None, reset: bool = True) -> dict:
    root = (root or config.WORKSPACE_DIR).resolve()
    config.ensure_dirs()
    schema = db.init_schema()
    chunks = collect_chunks(root)
    if not chunks:
        raise RuntimeError(f"没有在 {root} 找到可导入的规格、代码或测试")

    if reset:
        db.clear_materials()
    inserted = db.replace_materials(chunks)
    counts = db.count_by_kind()
    summary = {
        "time": now_iso(),
        "root": str(root),
        "reset": reset,
        "chunks": len(chunks),
        "by_kind": counts,
        "inserted": inserted,
        "schema_query_id": schema["query_id"],
        "files": sorted({chunk["path"] for chunk in chunks}),
        "chunk_ids": [chunk["id"] for chunk in chunks],
    }
    write_json(config.RUNS_DIR / "ingest.json", summary)
    write_json(
        config.RUNS_DIR / "materials.json",
        [
            {
                "id": chunk["id"],
                "kind": chunk["kind"],
                "path": chunk["path"],
                "symbol": chunk["symbol"],
                "content_hash": chunk["content_hash"],
            }
            for chunk in chunks
        ],
    )
    return summary
