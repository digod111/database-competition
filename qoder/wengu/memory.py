from __future__ import annotations

import json
import re
from typing import Any

from . import config
from . import powercontext_client as pc
from .text import append_jsonl, now_iso, read_json, sha256_text, write_json

PROFILE_PREFIX = "PROFILE"
WEAK_POINT_PREFIX = "WEAK_POINT"
SESSION_PREFIX = "SESSION"
HANDOFF_PREFIX = "HANDOFF"

SCOPES_FILE = config.RUNTIME_DIR / "scopes.json"
MEMORY_LOG = config.RUNS_DIR / "powercontext_memory.jsonl"


def _log(action: str, user_id: str, scope_id: str | None, **fields: Any) -> None:
    append_jsonl(
        MEMORY_LOG,
        {"time": now_iso(), "action": action, "user_id": user_id, "scope_id": scope_id, **fields},
    )


def _scope_key(user_id: str, generation: int) -> str:
    digest = sha256_text(user_id)[:10]
    return f"{config.POWERCONTEXT_SCOPE_PREFIX}-u{digest}-g{generation}"


def ensure_scope(user_id: str) -> str:
    scopes = read_json(SCOPES_FILE, {}) or {}
    record = scopes.get(user_id) or {}
    generation = int(record.get("generation", 1))
    scope_key = _scope_key(user_id, generation)
    response = pc.create_scope(
        title=f"温故复习教练 / {user_id}",
        summary="温故复习教练的学习者记忆：偏好、薄弱点、会话摘要与未完成交接。",
        idempotency_key=scope_key,
    )
    scope_id = response.get("scope_id") or response.get("id")
    if not scope_id:
        raise pc.PowerContextError(f"创建 Scope 的响应缺少 scope_id：{response}")
    scopes[user_id] = {
        "scope_id": scope_id,
        "generation": generation,
        "scope_key": scope_key,
        "updated_at": now_iso(),
    }
    write_json(SCOPES_FILE, scopes)
    _log("ensure_scope", user_id, scope_id, generation=generation, scope_key=scope_key)
    return str(scope_id)


def reset_scope(user_id: str) -> str:
    scopes = read_json(SCOPES_FILE, {}) or {}
    record = scopes.get(user_id) or {}
    generation = int(record.get("generation", 1)) + 1
    scopes[user_id] = {**record, "generation": generation, "updated_at": now_iso()}
    write_json(SCOPES_FILE, scopes)
    _log("reset_scope", user_id, record.get("scope_id"), new_generation=generation)
    return ensure_scope(user_id)


def _remember(user_id: str, scope_id: str, kind: str, text: str, reason: str) -> dict[str, Any]:
    response = pc.remember(scope_id, kind=kind, text=text, reason=reason)
    artifact_id = None
    records = response.get("records") or []
    if records:
        artifact_id = ((records[0].get("artifact") or {}).get("artifact_id"))
    _log(
        "remember",
        user_id,
        scope_id,
        kind=kind,
        text=text,
        reason=reason,
        artifact_id=artifact_id,
        response_status="ok",
    )
    return {"artifact_id": artifact_id, "response": response}


def remember_profile(user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    scope_id = ensure_scope(user_id)
    text = f"{PROFILE_PREFIX} {json.dumps(profile, ensure_ascii=False)}"
    return _remember(user_id, scope_id, "profile", text, "保存学习偏好和每日目标")


def remember_weak_points(user_id: str, weak_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not weak_points:
        return []
    scope_id = ensure_scope(user_id)
    results = []
    for weak in weak_points[:6]:
        payload = {
            "knowledge_point": weak["knowledge_point"],
            "course": weak.get("course", ""),
            "wrong": int(weak.get("wrong", 0)),
            "total": int(weak.get("total", 0)),
            "mastery": round(float(weak.get("mastery", 0)), 3),
            "question_ids": weak.get("question_ids", [])[:12],
            "updated_at": now_iso(),
        }
        text = f"{WEAK_POINT_PREFIX} {json.dumps(payload, ensure_ascii=False)}"
        result = _remember(
            user_id, scope_id, "weak_point", text,
            f"本轮复习在知识点「{payload['knowledge_point']}」上出错，记录为薄弱点",
        )
        results.append({"knowledge_point": payload["knowledge_point"], "artifact_id": result["artifact_id"]})
    return results


def remember_session(user_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    scope_id = ensure_scope(user_id)
    payload = {
        "session_id": summary["session_id"],
        "finished_at": summary.get("finished_at", now_iso()),
        "answered": int(summary.get("answered", 0)),
        "correct": int(summary.get("correct", 0)),
        "accuracy": round(float(summary.get("accuracy", 0)), 3),
        "weak_points": summary.get("weak_points", [])[:8],
        "summary": str(summary.get("summary", ""))[:240],
    }
    text = f"{SESSION_PREFIX} {json.dumps(payload, ensure_ascii=False)}"
    return _remember(user_id, scope_id, "session_summary", text, "保存本轮复习摘要，供下次制定计划时召回")


def remember_handoff(user_id: str, handoff: dict[str, Any]) -> dict[str, Any]:
    scope_id = ensure_scope(user_id)
    payload = {
        "session_id": handoff["session_id"],
        "remaining": handoff.get("remaining", [])[:20],
        "next_focus": handoff.get("next_focus", ""),
        "status": handoff.get("status", "open"),
        "created_at": now_iso(),
    }
    text = f"{HANDOFF_PREFIX} {json.dumps(payload, ensure_ascii=False)}"
    return _remember(user_id, scope_id, "handoff", text, "保存未完成题目，提醒下次继续")


def _decode(prefix: str, text: str) -> dict[str, Any] | None:
    if not text.startswith(prefix):
        return None
    try:
        value = json.loads(text[len(prefix) :].strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _active_hits(response: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    parsed = []
    for hit in response.get("hits", []):
        memory = hit.get("memory") or {}
        if memory.get("state") not in (None, "active"):
            continue
        payload = _decode(prefix, memory.get("text", ""))
        if payload is None:
            continue
        artifact = memory.get("artifact") or {}
        parsed.append(
            {
                "payload": payload,
                "artifact_id": artifact.get("artifact_id"),
                "kind": memory.get("kind"),
                "score": hit.get("score"),
                "matched_by": hit.get("matched_by", []),
            }
        )
    return parsed


def _extract_prepared(content: str | None) -> list[dict[str, Any]]:
    if not content:
        return []
    match = re.search(
        r"BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\s*(\{.*?\})\s*END_POWERCONTEXT_PREPARED_CONTEXT_V1",
        content,
        re.S,
    )
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    items = []
    for item in payload.get("items", []):
        citation = item.get("citation") or {}
        artifact = citation.get("artifact_ref") or {}
        items.append(
            {
                "artifact_id": artifact.get("artifact_id"),
                "kind": item.get("kind"),
                "content": item.get("content", ""),
            }
        )
    return items


def _prepare(scope_id: str, query: str) -> dict[str, Any]:
    response = pc.prepare_context(scope_id, query=query, max_bytes=4000)
    content = response.get("content")
    return {
        "query": query,
        "status": response.get("status"),
        "content_bytes": response.get("content_bytes", 0),
        "content_excerpt": (content or "")[:400],
        "citations": _extract_prepared(content),
        "receipt_id": (response.get("receipt") or {}).get("receipt_id"),
    }


def recall(user_id: str, plan_query: str | None = None) -> dict[str, Any]:
    scope_id = ensure_scope(user_id)
    profile_hits = _active_hits(pc.search(scope_id, PROFILE_PREFIX, limit=10), PROFILE_PREFIX)
    weak_hits = _active_hits(pc.search(scope_id, WEAK_POINT_PREFIX, limit=50), WEAK_POINT_PREFIX)
    session_hits = _active_hits(pc.search(scope_id, SESSION_PREFIX, limit=10), SESSION_PREFIX)
    handoff_hits = _active_hits(pc.search(scope_id, HANDOFF_PREFIX, limit=10), HANDOFF_PREFIX)

    profile_hits.sort(key=lambda item: item["payload"].get("updated_at", ""))
    profile = profile_hits[-1] if profile_hits else None

    weak_by_point: dict[str, dict[str, Any]] = {}
    for hit in weak_hits:
        point = hit["payload"].get("knowledge_point")
        if not point:
            continue
        current = weak_by_point.get(point)
        if current is None or hit["payload"].get("updated_at", "") >= current["payload"].get("updated_at", ""):
            weak_by_point[point] = hit
    weak_points = sorted(
        weak_by_point.values(),
        key=lambda item: (int(item["payload"].get("wrong", 0)), -float(item["payload"].get("mastery", 0))),
        reverse=True,
    )

    session_hits.sort(key=lambda item: item["payload"].get("finished_at", ""), reverse=True)
    sessions = session_hits[:5]

    handoff_hits.sort(key=lambda item: item["payload"].get("created_at", ""), reverse=True)
    handoff = handoff_hits[0] if handoff_hits else None

    top_points = [hit["payload"]["knowledge_point"] for hit in weak_points[:2]]
    if plan_query:
        query = plan_query
    elif top_points:
        query = f"{' '.join(top_points)} 薄弱点 复习计划"
    else:
        query = "PROFILE 复习计划"
    prepared = _prepare(scope_id, query)
    if prepared["status"] != "ready" and top_points:
        prepared = _prepare(scope_id, f"{top_points[0]}")
    if prepared["status"] != "ready" and sessions:
        prepared = _prepare(scope_id, "SESSION 复习")

    result = {
        "scope_id": scope_id,
        "recalled_at": now_iso(),
        "profile": profile,
        "weak_points": weak_points,
        "sessions": sessions,
        "handoff": handoff,
        "prepared": prepared,
        "counts": {
            "profile": len(profile_hits),
            "weak_point": len(weak_hits),
            "session_summary": len(session_hits),
            "handoff": len(handoff_hits),
            "prepared_citations": len(prepared["citations"]),
        },
    }
    _log(
        "recall",
        user_id,
        scope_id,
        counts=result["counts"],
        profile=profile["payload"] if profile else None,
        weak_points=[hit["payload"] for hit in weak_points[:6]],
        latest_session=sessions[0]["payload"] if sessions else None,
        handoff=handoff["payload"] if handoff else None,
        prepared={k: prepared[k] for k in ("query", "status", "content_bytes", "receipt_id")},
        prepared_citations=[
            {"artifact_id": item["artifact_id"], "content": item["content"][:160]}
            for item in prepared["citations"]
        ],
    )
    return result


def evidence_summary(recall_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope_id": recall_result["scope_id"],
        "counts": recall_result["counts"],
        "profile": recall_result["profile"]["payload"] if recall_result["profile"] else None,
        "weak_points": [hit["payload"] for hit in recall_result["weak_points"]],
        "latest_session": recall_result["sessions"][0]["payload"] if recall_result["sessions"] else None,
        "handoff": recall_result["handoff"]["payload"] if recall_result["handoff"] else None,
        "prepared_status": recall_result["prepared"]["status"],
        "prepared_citations": recall_result["prepared"]["citations"],
    }


def recent_memory_log(
    limit: int = 30,
    user_id: str | None = None,
    actions: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if not MEMORY_LOG.exists():
        return []
    lines = MEMORY_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-(limit * 12) :]
    items = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if user_id and item.get("user_id") != user_id:
            continue
        if actions and item.get("action") not in actions:
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


def scope_record(user_id: str) -> dict[str, Any] | None:
    scopes = read_json(SCOPES_FILE, {}) or {}
    return scopes.get(user_id)
