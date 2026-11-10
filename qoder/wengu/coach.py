from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import db, mastery, memory
from .text import append_jsonl, excerpt, highlight_html, now_iso

DEFAULT_PROFILE: dict[str, Any] = {
    "explanation_style": "concise",
    "daily_goal": 6,
    "preferred_types": ["mcq"],
}

STYLE_LABELS = {"concise": "简洁", "detailed": "详细"}


class CoachError(RuntimeError):
    pass


def _profile_with_defaults(payload: dict[str, Any] | None, display_name: str | None = None) -> dict[str, Any]:
    profile = {**DEFAULT_PROFILE, **(payload or {})}
    if display_name:
        profile["display_name"] = display_name
    profile["daily_goal"] = max(3, min(12, int(profile.get("daily_goal", 6))))
    if profile.get("explanation_style") not in STYLE_LABELS:
        profile["explanation_style"] = "concise"
    return profile


def ensure_profile(
    user_id: str,
    display_name: str | None = None,
    recall_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    recall = recall_result or memory.recall(user_id)
    if recall.get("profile"):
        profile = _profile_with_defaults(recall["profile"]["payload"], display_name)
        return profile, recall, recall["profile"].get("artifact_id")
    profile = _profile_with_defaults({"display_name": display_name or user_id, "updated_at": now_iso()})
    result = memory.remember_profile(user_id, profile)
    refreshed = memory.recall(user_id)
    return profile, refreshed, result.get("artifact_id")


def set_preferences(
    user_id: str,
    daily_goal: int | None = None,
    explanation_style: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    db.ensure_user(user_id, display_name)
    recall = memory.recall(user_id)
    if recall.get("profile"):
        profile = _profile_with_defaults(recall["profile"]["payload"], display_name)
    else:
        profile = _profile_with_defaults({"display_name": display_name or user_id})
    if daily_goal is not None:
        profile["daily_goal"] = max(3, min(12, int(daily_goal)))
    if explanation_style in STYLE_LABELS:
        profile["explanation_style"] = explanation_style
    if display_name:
        profile["display_name"] = display_name
    profile["updated_at"] = now_iso()
    memory.remember_profile(user_id, profile)
    db.usage_event(user_id, "preferences", {
        "daily_goal": profile["daily_goal"],
        "explanation_style": profile["explanation_style"],
    })
    return profile


def _add_candidate(
    candidates: dict[str, dict[str, Any]],
    question: dict[str, Any],
    source: str,
    score: float,
    reason: str,
    memory_id: str | None = None,
) -> None:
    candidate = candidates.setdefault(
        question["id"],
        {"question": question, "score": 0.0, "sources": [], "reasons": [], "memory_ids": []},
    )
    candidate["score"] += score
    if source not in candidate["sources"]:
        candidate["sources"].append(source)
    candidate["reasons"].append(reason)
    if memory_id and memory_id not in candidate["memory_ids"]:
        candidate["memory_ids"].append(memory_id)


def build_plan(
    user_id: str,
    profile: dict[str, Any],
    recall_result: dict[str, Any],
    focus_course: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    goal = max(3, min(12, int(limit or profile.get("daily_goal", 6))))
    due = db.list_due_questions(user_id, 50)
    weak = db.list_weak_questions(user_id, 50, 0.6)
    new = db.list_new_questions(user_id, 200)
    if focus_course:
        due = [question for question in due if question["course"] == focus_course]
        weak = [question for question in weak if question["course"] == focus_course]
        new = [question for question in new if question["course"] == focus_course]

    handoff = recall_result.get("handoff") or {}
    handoff_payload = handoff.get("payload") or {}
    handoff_ids = set(handoff_payload.get("remaining") or []) if handoff_payload.get("status", "open") == "open" else set()

    weak_memory: dict[str, dict[str, Any]] = {}
    for hit in recall_result.get("weak_points", []):
        payload = hit.get("payload") or {}
        if float(payload.get("mastery", 0)) < 0.6:
            weak_memory[payload["knowledge_point"]] = hit
    weak_chapters: dict[str, dict[str, Any]] = {}
    for question in weak:
        if question["knowledge_point"] in weak_memory and question.get("chapter"):
            weak_chapters.setdefault(question["chapter"], weak_memory[question["knowledge_point"]])

    candidates: dict[str, dict[str, Any]] = {}
    for question in due:
        _add_candidate(candidates, question, "due", 6.0, f"到期复习（{str(question.get('due_at'))[:10]}）")
    for question in weak:
        mastery_value = float(question.get("mastery", 0))
        _add_candidate(
            candidates, question, "weak", 5.0 + max(0.0, 0.6 - mastery_value) * 10,
            f"掌握度偏低（{mastery_value:.2f}）",
        )
    for question in new:
        _add_candidate(candidates, question, "new", 2.0, "新题")
    handoff_added: set[str] = set()
    for question in due + weak + new:
        if question["id"] in handoff_ids and question["id"] not in handoff_added:
            _add_candidate(candidates, question, "handoff", 7.0, "上次会话未完成")
            handoff_added.add(question["id"])
    for candidate in candidates.values():
        question = candidate["question"]
        hit = weak_memory.get(question["knowledge_point"])
        if hit:
            wrong = int((hit.get("payload") or {}).get("wrong", 0))
            _add_candidate(
                candidates, question, "memory_weak_point", 9.0 + min(4, wrong),
                f"PowerContext 薄弱点「{question['knowledge_point']}」（错 {wrong} 次）",
                memory_id=hit.get("artifact_id"),
            )
        elif question.get("chapter") in weak_chapters:
            hit = weak_chapters[question["chapter"]]
            _add_candidate(
                candidates, question, "memory_weak_chapter", 4.0,
                f"同章节「{question['chapter']}」近期薄弱",
                memory_id=hit.get("artifact_id"),
            )
        if focus_course and question["course"] == focus_course:
            _add_candidate(candidates, question, "focus", 4.0, "本轮课程重点")

    pool = list(candidates.values())
    course_counts: Counter[str] = Counter()
    chapter_counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    course_penalty = 0.0 if focus_course else 0.7
    chapter_penalty = 0.0 if focus_course else 0.35
    while pool and len(selected) < goal:
        best = max(
            pool,
            key=lambda item: item["score"]
            - course_penalty * course_counts[item["question"]["course"]]
            - chapter_penalty * chapter_counts[item["question"]["chapter"]],
        )
        pool.remove(best)
        selected.append(best)
        course_counts[best["question"]["course"]] += 1
        chapter_counts[best["question"]["chapter"]] += 1

    if len(selected) < goal:
        states = {state["question_id"]: state for state in db.list_states(user_id)}
        selected_ids = {item["question"]["id"] for item in selected}
        all_questions = db.list_questions()
        extras_focus = [
            question for question in all_questions
            if question["id"] not in selected_ids
            and focus_course and question["course"] == focus_course
        ]
        extras_all = [
            question for question in all_questions
            if question["id"] not in selected_ids
            and question not in extras_focus
        ]
        for group in (extras_focus, extras_all):
            group.sort(key=lambda q: (float(states.get(q["id"], {}).get("mastery", 0)), q["id"]))
            for question in group:
                if len(selected) >= goal:
                    break
                state = states.get(question["id"])
                if state:
                    reason = f"巩固已学（掌握度 {float(state.get('mastery', 0)):.2f}）"
                else:
                    reason = "补足练习（自由复习）"
                candidate = {
                    "question": question, "score": 0.0, "sources": ["review"],
                    "reasons": [reason], "memory_ids": [],
                }
                selected.append(candidate)
                candidates[question["id"]] = candidate

    items = []
    for index, candidate in enumerate(selected, start=1):
        question = candidate["question"]
        items.append(
            {
                "number": index,
                "id": question["id"],
                "course": question["course"],
                "chapter": question["chapter"],
                "knowledge_point": question["knowledge_point"],
                "type": question["type"],
                "difficulty": question["difficulty"],
                "stem": question["stem"],
                "options": question.get("options", []),
                "score": round(candidate["score"], 2),
                "sources": candidate["sources"],
                "reasons": candidate["reasons"],
                "memory_ids": candidate["memory_ids"],
            }
        )

    influences: list[dict[str, Any]] = []
    profile_hit = recall_result.get("profile")
    if profile_hit:
        influences.append(
            {
                "memory_kind": "profile",
                "memory_id": profile_hit.get("artifact_id"),
                "title": "学习偏好",
                "text": excerpt(str(profile_hit.get("payload")), 160),
                "effect": f"每日目标 {goal} 题；解析风格「{STYLE_LABELS.get(profile.get('explanation_style'), '简洁')}」",
                "affected_question_ids": [],
            }
        )
    weak_groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for memory_id in item["memory_ids"]:
            if memory_id:
                weak_groups.setdefault(memory_id, []).append(item)
    for memory_id, affected in weak_groups.items():
        hit = next(
            (h for h in recall_result.get("weak_points", []) if h.get("artifact_id") == memory_id),
            None,
        )
        if not hit:
            continue
        payload = hit.get("payload") or {}
        point = payload.get("knowledge_point")
        exact = [item for item in affected if item.get("knowledge_point") == point]
        chapter = [item for item in affected if item.get("knowledge_point") != point]
        if exact:
            influences.append(
                {
                    "memory_kind": "weak_point",
                    "memory_id": memory_id,
                    "title": f"薄弱点：{point}",
                    "text": excerpt(str(payload), 160),
                    "effect": f"知识点加权：抽中 {len(exact)} 题（错 {payload.get('wrong', 0)} 次，掌握度 {float(payload.get('mastery', 0)):.2f}）",
                    "affected_question_ids": [item["id"] for item in exact],
                }
            )
        if chapter:
            influences.append(
                {
                    "memory_kind": "weak_chapter",
                    "memory_id": memory_id,
                    "title": f"同章节扩展：{chapter[0].get('chapter', '')}",
                    "text": excerpt(str(payload), 160),
                    "effect": f"同章节加权：抽中 {len(chapter)} 题",
                    "affected_question_ids": [item["id"] for item in chapter],
                }
            )
    if handoff_ids:
        affected = [item["id"] for item in items if item["id"] in handoff_ids]
        if affected:
            influences.append(
                {
                    "memory_kind": "handoff",
                    "memory_id": handoff.get("artifact_id"),
                    "title": "上次未完成",
                    "text": excerpt(str(handoff_payload), 160),
                    "effect": f"补做上次未完成 {len(affected)} 题",
                    "affected_question_ids": affected,
                }
            )
    prepared = recall_result.get("prepared") or {}
    if prepared.get("citations"):
        influences.append(
            {
                "memory_kind": "prepared_context",
                "memory_id": prepared["citations"][0].get("artifact_id"),
                "title": "PowerContext 上下文包",
                "text": excerpt(prepared.get("content_excerpt", ""), 160),
                "effect": f"context/prepare 组装了 {len(prepared['citations'])} 条历史记忆作为计划依据",
                "affected_question_ids": [],
            }
        )

    counts = {
        "due": len(due),
        "weak": len(weak),
        "new": len(new),
        "handoff": len(handoff_ids),
        "candidates": len(candidates),
    }
    plan = {
        "user_id": user_id,
        "generated_at": now_iso(),
        "goal": goal,
        "focus_course": focus_course,
        "profile": profile,
        "items": items,
        "influences": influences,
        "memory_summary": {
            "profile": profile,
            "weak_points": [hit.get("payload") for hit in recall_result.get("weak_points", [])],
            "handoff": handoff_payload,
            "prepared_status": prepared.get("status"),
            "prepared_citations": len(prepared.get("citations", [])),
        },
        "candidate_counts": counts,
    }
    append_jsonl(
        config_path("coach_plans.jsonl"),
        {
            "time": plan["generated_at"],
            "user_id": user_id,
            "goal": goal,
            "focus_course": focus_course,
            "items": [
                {"id": item["id"], "kp": item["knowledge_point"], "score": item["score"], "sources": item["sources"]}
                for item in items
            ],
            "influences": influences,
            "candidate_counts": counts,
            "memory_counts": (recall_result.get("counts") or {}),
        },
    )
    return plan


def config_path(name: str):
    from . import config

    return config.RUNS_DIR / name


def start_session(
    user_id: str,
    display_name: str | None = None,
    focus_course: str | None = None,
) -> dict[str, Any]:
    db.ensure_user(user_id, display_name)
    recall_initial = memory.recall(user_id)
    profile, recall_result, _ = ensure_profile(user_id, display_name, recall_initial)
    plan = build_plan(user_id, profile, recall_result, focus_course=focus_course)
    scope_evidence = memory.evidence_summary(recall_result)
    session_id = db.create_session(user_id, plan, scope_evidence)
    db.usage_event(user_id, "session_start", {
        "session_id": session_id,
        "focus_course": focus_course,
        "goal": plan["goal"],
    })
    plan["session_id"] = session_id
    append_jsonl(
        config_path("coach_sessions.jsonl"),
        {
            "time": now_iso(),
            "action": "session_start",
            "session_id": session_id,
            "user_id": user_id,
            "goal": plan["goal"],
            "focus_course": focus_course,
            "items": [item["id"] for item in plan["items"]],
            "memory_counts": scope_evidence.get("counts"),
        },
    )
    return {
        "session_id": session_id,
        "plan": plan,
        "profile": profile,
        "memory": scope_evidence,
    }


def get_session_detail(session_id: str) -> dict[str, Any]:
    session = db.get_session(session_id)
    if not session:
        raise CoachError(f"会话不存在：{session_id}")
    attempts = db.session_attempts(session_id)
    answered_ids = {attempt["question_id"] for attempt in attempts}
    plan_items = (session.get("plan") or {}).get("items", [])
    next_item = next((item for item in plan_items if item["id"] not in answered_ids), None)
    states = {state["question_id"]: state for state in db.list_states(session["user_id"])}
    return {
        "session": session,
        "plan": session.get("plan"),
        "attempts": attempts,
        "answered_ids": answered_ids,
        "next_item": next_item,
        "progress": {
            "answered": len(attempts),
            "planned": len(plan_items),
            "correct": sum(1 for attempt in attempts if attempt["correct"]),
        },
        "states": states,
    }


def compose_explanation(
    question: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    style: str,
) -> dict[str, Any]:
    detailed = style == "detailed"
    explanation = question["explanation"]
    if not detailed:
        first_sentence = explanation.split("。")[0]
        explanation = first_sentence + "。"
    limit = len(evidence_rows) if detailed else min(1, len(evidence_rows))
    snippet_length = 240 if detailed else 120
    evidence = []
    for row in evidence_rows[:limit]:
        evidence.append(
            {
                "id": row["id"],
                "title": row["title"],
                "course": row["course"],
                "chapter": row["chapter"],
                "source_path": row["source_path"],
                "score": round(float(row.get("score") or 0), 4),
                "snippet_html": str(highlight_html(row["content"], question["knowledge_point"], snippet_length)),
            }
        )
    return {
        "style": style,
        "style_label": STYLE_LABELS.get(style, "简洁"),
        "text": explanation,
        "evidence": evidence,
    }


def submit_answer(
    session_id: str,
    question_id: str,
    selected: Any = None,
    rating: str | None = None,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    session = db.get_session(session_id)
    if not session:
        raise CoachError(f"会话不存在：{session_id}")
    if session["status"] != "active":
        raise CoachError("会话已结束，请开始新一轮复习。")
    plan_ids = [item["id"] for item in (session.get("plan") or {}).get("items", [])]
    if question_id not in plan_ids:
        raise CoachError("该题目不在本次复习计划中。")
    if db.session_has_attempt(session_id, question_id):
        raise CoachError("这道题本次会话已经作答过了。")
    question = db.get_question(question_id)
    if not question:
        raise CoachError(f"题目不存在：{question_id}")

    result = mastery.apply_answer(
        user_id=session["user_id"],
        question=question,
        session_id=session_id,
        selected=selected,
        rating=rating,
        elapsed_ms=elapsed_ms,
    )
    evidence_rows, evidence_meta = db.search_for_question(question, limit=2)
    style = (session.get("plan") or {}).get("profile", {}).get("explanation_style", "concise")
    explanation = compose_explanation(question, evidence_rows, style)
    db.usage_event(session["user_id"], "answer", {
        "session_id": session_id,
        "question_id": question_id,
        "correct": result["correct"],
        "quality": result["quality"],
    })
    append_jsonl(
        config_path("coach_answers.jsonl"),
        {
            "time": now_iso(),
            "session_id": session_id,
            "user_id": session["user_id"],
            "question_id": question_id,
            "knowledge_point": question["knowledge_point"],
            "correct": result["correct"],
            "quality": result["quality"],
            "mastery": round(result["state"]["mastery"], 4),
            "next_due": result["next_due"],
            "evidence_query_id": evidence_meta.get("query_id"),
            "evidence": [
                {"id": row["id"], "title": row["title"], "score": round(float(row.get("score") or 0), 4)}
                for row in evidence_rows
            ],
            "explanation_style": style,
        },
    )
    return {
        "attempt_id": result["attempt_id"],
        "question": {
            "id": question["id"],
            "stem": question["stem"],
            "type": question["type"],
            "options": question["options"],
            "answer": question["answer"],
            "explanation": question["explanation"],
            "knowledge_point": question["knowledge_point"],
        },
        "correct": result["correct"],
        "quality": result["quality"],
        "quality_label": result["quality_label"],
        "state": result["state"],
        "next_due": result["next_due"],
        "due_human": mastery.due_human(result["state"]),
        "explanation": explanation,
        "evidence_query_id": evidence_meta.get("query_id"),
    }


def finish_session(session_id: str) -> dict[str, Any]:
    session = db.get_session(session_id)
    if not session:
        raise CoachError(f"会话不存在：{session_id}")
    if session["status"] != "active":
        raise CoachError("会话已经结束。")
    user_id = session["user_id"]
    plan = session.get("plan") or {}
    plan_items = plan.get("items", [])
    attempts = db.session_attempts(session_id)
    answered_ids = {attempt["question_id"] for attempt in attempts}
    remaining = [item["id"] for item in plan_items if item["id"] not in answered_ids]
    correct = sum(1 for attempt in attempts if attempt["correct"])
    total = len(attempts)

    kp_stats: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        point = attempt["knowledge_point"]
        entry = kp_stats.setdefault(
            point,
            {
                "knowledge_point": point,
                "course": attempt.get("course", ""),
                "chapter": attempt.get("chapter", ""),
                "session_total": 0,
                "session_wrong": 0,
                "question_ids": [],
            },
        )
        entry["session_total"] += 1
        entry["session_wrong"] += 0 if attempt["correct"] else 1
        if attempt["question_id"] not in entry["question_ids"]:
            entry["question_ids"].append(attempt["question_id"])

    states = {state["question_id"]: state for state in db.list_states(user_id)}
    weak_points = []
    for point, entry in kp_stats.items():
        related_states = [states[qid] for qid in entry["question_ids"] if qid in states]
        if not related_states:
            continue
        point_mastery = min(float(state.get("mastery", 0)) for state in related_states)
        cumulative_wrong = sum(int(state.get("wrong_count", 0)) for state in related_states)
        cumulative_total = cumulative_wrong + sum(int(state.get("correct_count", 0)) for state in related_states)
        weak_points.append(
            {
                "knowledge_point": point,
                "course": entry.get("course", ""),
                "chapter": entry.get("chapter", ""),
                "wrong": cumulative_wrong,
                "total": cumulative_total,
                "mastery": point_mastery,
                "question_ids": entry["question_ids"],
            }
        )

    weak_names = [item["knowledge_point"] for item in weak_points if item["mastery"] < 0.6]
    summary_text = f"本轮完成 {total} 题，答对 {correct} 题。"
    if weak_names:
        summary_text += "仍需关注：" + "、".join(weak_names[:4]) + "。"
    else:
        summary_text += "本轮没有新增薄弱点。"
    if remaining:
        summary_text += f" 还有 {len(remaining)} 题未完成。"

    memory_session = memory.remember_session(
        user_id,
        {
            "session_id": session_id,
            "finished_at": now_iso(),
            "answered": total,
            "correct": correct,
            "accuracy": (correct / total) if total else 0,
            "weak_points": weak_names,
            "summary": summary_text,
        },
    )

    previous_recall = memory.recall(user_id)
    previous_weak = {
        (hit.get("payload") or {}).get("knowledge_point")
        for hit in previous_recall.get("weak_points", [])
        if float((hit.get("payload") or {}).get("mastery", 0)) < 0.6
    }
    to_remember = [
        item for item in weak_points
        if item["wrong"] > 0 or item["knowledge_point"] in previous_weak
    ]
    memory_weak = memory.remember_weak_points(user_id, to_remember)

    previous_handoff = previous_recall.get("handoff") or {}
    handoff_status = "open" if remaining else "completed"
    memory_handoff = None
    if remaining or previous_handoff:
        remaining_questions = [db.get_question(qid) for qid in remaining]
        next_focus = next((q["knowledge_point"] for q in remaining_questions if q), "")
        memory_handoff = memory.remember_handoff(
            user_id,
            {
                "session_id": session_id,
                "remaining": remaining,
                "next_focus": next_focus,
                "status": handoff_status,
            },
        )

    db.finish_session(session_id)
    db.usage_event(user_id, "session_end", {
        "session_id": session_id,
        "answered": total,
        "correct": correct,
        "remaining": len(remaining),
    })
    result = {
        "session_id": session_id,
        "answered": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0,
        "weak_points": weak_points,
        "remaining": remaining,
        "summary": summary_text,
        "memory": {
            "session": memory_session,
            "weak_points": memory_weak,
            "handoff": memory_handoff,
        },
        "finished_at": now_iso(),
    }
    db.save_session_summary(session_id, user_id, result)
    append_jsonl(
        config_path("coach_sessions.jsonl"),
        {
            "time": result["finished_at"],
            "action": "session_end",
            "session_id": session_id,
            "user_id": user_id,
            "answered": total,
            "correct": correct,
            "remaining": remaining,
            "weak_points": weak_points,
            "memory_artifacts": {
                "session": memory_session.get("artifact_id"),
                "weak_points": [item.get("artifact_id") for item in memory_weak],
                "handoff": (memory_handoff or {}).get("artifact_id"),
            },
        },
    )
    return result


def library_search(
    user_id: str,
    query: str,
    course: str | None = None,
    chapter: str | None = None,
    knowledge_point: str | None = None,
) -> dict[str, Any]:
    rows, meta = db.search_materials(
        query, course=course, chapter=chapter, knowledge_point=knowledge_point,
        limit=10, purpose="library_search",
    )
    results = []
    for row in rows:
        results.append(
            {
                "id": row["id"],
                "title": row["title"],
                "course": row["course"],
                "chapter": row["chapter"],
                "source_path": row["source_path"],
                "score": round(float(row.get("score") or 0), 4),
                "snippet_html": str(highlight_html(row["content"], query or row["title"], 280)),
            }
        )
    db.usage_event(user_id, "search", {
        "query": query,
        "course": course,
        "results": len(results),
        "query_id": meta.get("query_id"),
    })
    return {"query": query, "results": results, "query_id": meta.get("query_id"), "meta": meta}


def dashboard(user_id: str) -> dict[str, Any]:
    user = db.ensure_user(user_id)
    recall = memory.recall(user_id)
    profile, recall, _ = ensure_profile(user_id, user["display_name"], recall)
    return {
        "user": user,
        "profile": profile,
        "memory": memory.evidence_summary(recall),
        "recall_full": recall,
        "progress": db.user_progress(user_id),
        "stats": db.stats_overview(user_id),
        "latest_session": db.latest_session(user_id),
        "active_session": db.latest_session(user_id, status="active"),
        "recent_queries": db.recent_queries(80),
        "feedback": db.list_feedback(5),
    }


def memory_evidence(user_id: str) -> dict[str, Any]:
    recall = memory.recall(user_id)
    return {
        "user_id": user_id,
        "evidence": memory.evidence_summary(recall),
        "recall": recall,
        "log": memory.recent_memory_log(30, user_id=user_id, actions=("remember", "reset_scope")),
        "scope": memory.scope_record(user_id),
    }


def overview() -> dict[str, Any]:
    counts = db.check()["counts"]
    users = db.list_users(50)
    return {
        "counts": counts,
        "users": users,
        "stats": db.stats_overview(None),
        "feedback": db.list_feedback(20),
        "recent_queries": db.recent_queries(12),
    }
