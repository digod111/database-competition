from __future__ import annotations

import json
from typing import Any

from . import config, powercontext_client as pc
from .text import append_jsonl, now_iso, write_json

STATE_PREFIX = "SDD_STATE"
STATE_SCHEMA = "sdd-task-state.v1"


def ensure_scope() -> str:
    scope_id = pc.create_scope()
    write_json(
        config.RUNTIME_DIR / "scope.json",
        {
            "url": config.POWERCONTEXT_URL,
            "scope_id": scope_id,
            "title": config.POWERCONTEXT_SCOPE_TITLE,
            "idempotency_key": config.POWERCONTEXT_SCOPE_KEY,
            "updated_at": now_iso(),
        },
    )
    return scope_id


def save_state(task_id: str, stage: str, data: dict[str, Any]) -> dict[str, Any]:
    scope_id = ensure_scope()
    state = {
        "schema": STATE_SCHEMA,
        "task_id": task_id,
        "stage": stage,
        "saved_at": now_iso(),
        "data": data,
    }
    text = f"{STATE_PREFIX} {json.dumps(state, ensure_ascii=False)}"
    response = pc.remember(
        scope_id,
        kind="status",
        text=text,
        reason=f"SDD task {task_id} stage {stage}",
    )
    append_jsonl(
        config.RUNS_DIR / "powercontext_state.jsonl",
        {
            "time": now_iso(),
            "action": "remember",
            "scope_id": scope_id,
            "task_id": task_id,
            "stage": stage,
            "text": text,
            "response": response,
        },
    )
    return {"scope_id": scope_id, "state": state, "response": response}


def _parse_state(text: str) -> dict[str, Any] | None:
    if not text.startswith(STATE_PREFIX):
        return None
    payload = text[len(STATE_PREFIX) :].strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if value.get("schema") != STATE_SCHEMA:
        return None
    return value


def load_state(task_id: str) -> dict[str, Any]:
    scope_id = ensure_scope()
    response = pc.search(scope_id, f"{STATE_PREFIX} {task_id}", limit=50)
    candidates: list[dict[str, Any]] = []
    for hit in response.get("hits", []):
        memory = hit.get("memory", {})
        parsed = _parse_state(memory.get("text", ""))
        if parsed and parsed.get("task_id") == task_id:
            parsed["_artifact"] = memory.get("artifact")
            parsed["_kind"] = memory.get("kind")
            candidates.append(parsed)
    candidates.sort(key=lambda item: item.get("saved_at", ""))
    latest = candidates[-1] if candidates else None
    recall = pc.prepare_context(scope_id, f"{STATE_PREFIX} {task_id}", max_bytes=4000)
    result = {
        "found": latest is not None,
        "scope_id": scope_id,
        "state": latest,
        "hit_count": len(response.get("hits", [])),
        "matched_count": len(candidates),
        "history": [
            {"stage": item.get("stage"), "saved_at": item.get("saved_at")}
            for item in candidates
        ],
        "recall": recall,
        "searched_at": now_iso(),
    }
    append_jsonl(
        config.RUNS_DIR / "powercontext_state.jsonl",
        {
            "time": result["searched_at"],
            "action": "search_and_recall",
            "scope_id": scope_id,
            "task_id": task_id,
            "hit_count": result["hit_count"],
            "matched_count": result["matched_count"],
            "found": result["found"],
            "stage": latest.get("stage") if latest else None,
            "recall_status": recall.get("status"),
        },
    )
    return result


def status_from_state(state: dict[str, Any]) -> dict[str, Any]:
    if not state or not state.get("found"):
        return {
            "source": "empty",
            "completed": [],
            "todo": [
                "call_minimax",
                "apply_patch",
                "run_target_tests",
                "run_regression_tests",
            ],
            "verification": [],
            "last_stage": None,
            "last_saved_at": None,
        }
    payload = state.get("state") or {}
    data = payload.get("data") or {}
    return {
        "source": "powercontext",
        "completed": data.get("completed", []),
        "todo": data.get("todo", []),
        "verification": data.get("verification", []),
        "last_stage": payload.get("stage"),
        "last_saved_at": payload.get("saved_at"),
    }
