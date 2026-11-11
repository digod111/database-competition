from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config, db, state as state_module
from .retrieve import load_task, retrieve
from .text import new_id, now_iso, write_json

PACK_SCHEMA = "sdd-context-pack.v1"


def _source_summary(items: list[dict]) -> list[dict]:
    return [
        {
            "kind": item["kind"],
            "path": item["path"],
            "symbol": item.get("symbol"),
            "selected_because": item.get("selected_because", "retrieved"),
            "score": item.get("score", 0),
        }
        for item in items
    ]


def build_context(
    task_path: str | Path,
    state_result: dict[str, Any] | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    task = load_task(task_path)
    if state_result is None:
        state_result = state_module.load_state(task["id"])
    retrieved = retrieve(task)
    status = state_module.status_from_state(state_result)
    counts = db.count_by_kind()
    pack_id = new_id("ctx_")
    recall = state_result.get("recall") or {}
    pack = {
        "schema": PACK_SCHEMA,
        "pack_id": pack_id,
        "generated_at": now_iso(),
        "task": task,
        "selection": {
            "query_terms": retrieved["terms"],
            "required": retrieved["required"],
            "strategy": "seekdb lexical retrieval + task-required anchors",
        },
        "specs": retrieved["specs"],
        "code": retrieved["code"],
        "tests": retrieved["tests"],
        "status": status,
        "database": {
            "engine": "seekdb",
            "mode": config.SEEKDB_MODE,
            "host": config.SEEKDB_HOST if config.SEEKDB_MODE == "server" else "embedded",
            "port": config.SEEKDB_PORT if config.SEEKDB_MODE == "server" else None,
            "database": config.SEEKDB_DATABASE,
            "table": "sdd_materials",
            "counts": counts,
            "queries": retrieved["queries"],
        },
        "powercontext": {
            "url": config.POWERCONTEXT_URL,
            "scope_id": state_result.get("scope_id"),
            "state_found": bool(state_result.get("found")),
            "state_stage": (state_result.get("state") or {}).get("stage"),
            "state_saved_at": (state_result.get("state") or {}).get("saved_at"),
            "history": state_result.get("history", []),
            "recall_status": recall.get("status"),
            "recall_content": recall.get("content"),
        },
    }
    pack_path = Path(out_path) if out_path else config.RUNS_DIR / "context_pack.json"
    if not pack_path.is_absolute():
        pack_path = Path.cwd() / pack_path
    write_json(pack_path, pack)
    write_json(
        config.RUNS_DIR / "context_pack_summary.json",
        {
            "pack_id": pack_id,
            "task_id": task.get("id"),
            "generated_at": pack["generated_at"],
            "specs": _source_summary(pack["specs"]),
            "code": _source_summary(pack["code"]),
            "tests": _source_summary(pack["tests"]),
            "status": status,
            "db_query_ids": [query.get("query_id") for query in retrieved["queries"]],
            "powercontext": pack["powercontext"],
        },
    )
    db.save_context_pack(task["id"], pack_id, json.dumps(pack, ensure_ascii=False))
    return pack


def render_for_prompt(pack: dict[str, Any]) -> str:
    lines: list[str] = []
    task = pack["task"]
    lines.append("# Development task")
    lines.append(f"- id: {task.get('id')}")
    lines.append(f"- title: {task.get('title')}")
    lines.append(f"- description: {task.get('description')}")
    lines.append(f"- acceptance: {', '.join(task.get('acceptance', []))}")
    lines.append(f"- must_keep: {', '.join(task.get('must_keep', []))}")
    lines.append("")
    lines.append("# Applicable specifications (source: seekdb)")
    for item in pack["specs"]:
        lines.append(f"## {item.get('symbol')} [{item['path']}]")
        lines.append(item["content"])
        lines.append("")
    lines.append("# Code to inspect (source: seekdb)")
    for item in pack["code"]:
        lines.append(f"## {item.get('symbol')} [{item['path']}]")
        lines.append(item["content"])
        lines.append("")
    lines.append("# Verification tests (source: seekdb)")
    for item in pack["tests"]:
        lines.append(f"## {item.get('symbol')} [{item['path']}]")
        lines.append(item["content"])
        lines.append("")
    lines.append("# Recalled task status (source: PowerContext)")
    lines.append(json.dumps(pack["status"], ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("# Constraints")
    lines.append("- Modify only src/taskboard/service.py.")
    lines.append("- Do not modify tests, specs, models.py, or pytest.ini.")
    lines.append("- Preserve backward compatibility described in the specifications.")
    return "\n".join(lines)


def summary(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "pack_id": pack["pack_id"],
        "task_id": pack["task"]["id"],
        "specs": [item.get("symbol") for item in pack["specs"]],
        "code": [item.get("symbol") for item in pack["code"]],
        "tests": [item.get("symbol") for item in pack["tests"]],
        "status_source": pack["status"].get("source"),
        "db_query_ids": [query.get("query_id") for query in pack["database"]["queries"]],
        "powercontext_scope": pack["powercontext"].get("scope_id"),
    }
