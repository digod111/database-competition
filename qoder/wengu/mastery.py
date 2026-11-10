from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import db
from .text import now_dt, parse_db_time, to_db_time


def initial_state() -> dict[str, Any]:
    return {
        "mastery": 0.5,
        "ease": 2.5,
        "interval_days": 0.0,
        "due_at": None,
        "correct_count": 0,
        "wrong_count": 0,
        "streak": 0,
        "last_result": "",
    }


def quality_label(quality: int) -> str:
    if quality >= 5:
        return "熟练"
    if quality >= 4:
        return "正确"
    if quality >= 3:
        return "模糊"
    return "错误"


def update_state(state: dict[str, Any] | None, quality: int, now: datetime | None = None) -> dict[str, Any]:
    current = {**initial_state(), **(state or {})}
    now = now or now_dt()
    ease = float(current["ease"])
    interval = float(current["interval_days"])
    streak = int(current["streak"])
    correct_count = int(current["correct_count"])
    wrong_count = int(current["wrong_count"])
    old_mastery = float(current["mastery"])

    if quality < 3:
        streak = 0
        interval = 1.0
        ease = max(1.3, ease - 0.2)
        wrong_count += 1
        target = 0.0
        last_result = "wrong"
    else:
        streak += 1
        correct_count += 1
        if streak == 1:
            interval = 1.0
        elif streak == 2:
            interval = 3.0
        else:
            interval = max(1.0, round(interval * ease))
        ease = max(1.3, ease + (0.1 - (5 - quality) * 0.08))
        target = {3: 0.55, 4: 0.8, 5: 1.0}.get(quality, 0.8)
        last_result = "partial" if quality == 3 else "correct"

    mastery = max(0.0, min(1.0, 0.65 * old_mastery + 0.35 * target))
    due_at = now + timedelta(days=max(1.0, interval))
    return {
        "mastery": mastery,
        "ease": ease,
        "interval_days": interval,
        "due_at": to_db_time(due_at),
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "streak": streak,
        "last_result": last_result,
        "quality": quality,
    }


def grade_mcq(question: dict[str, Any], selected: Any) -> tuple[bool, int]:
    try:
        index = int(selected)
    except (TypeError, ValueError):
        return False, 0
    correct = index == question["answer"]
    return correct, 4 if correct else 0


def grade_short(rating: str) -> tuple[bool, int]:
    rating = (rating or "").strip().lower()
    if rating in ("correct", "good", "对", "正确"):
        return True, 4
    if rating in ("partial", "hard", "模糊", "不确定"):
        return False, 3
    return False, 0


def apply_answer(
    user_id: str,
    question: dict[str, Any],
    session_id: str,
    selected: Any,
    rating: str | None = None,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    if question["type"] == "mcq":
        correct, quality = grade_mcq(question, selected)
        answer_value: Any = selected
    else:
        correct, quality = grade_short(rating or "")
        answer_value = {"self_rating": rating}

    existing = db.get_state(user_id, question["id"])
    updated = update_state(existing, quality)
    db.upsert_state(
        user_id=user_id,
        question_id=question["id"],
        mastery=updated["mastery"],
        ease=updated["ease"],
        interval_days=updated["interval_days"],
        due_at=updated["due_at"],
        correct_count=updated["correct_count"],
        wrong_count=updated["wrong_count"],
        streak=updated["streak"],
        last_result=updated["last_result"],
    )
    attempt_id = db.record_attempt(
        user_id=user_id,
        question_id=question["id"],
        session_id=session_id,
        answer=answer_value,
        correct=correct,
        quality=quality,
        elapsed_ms=elapsed_ms,
    )
    return {
        "attempt_id": attempt_id,
        "correct": correct,
        "quality": quality,
        "quality_label": quality_label(quality),
        "state": updated,
        "next_due": updated["due_at"],
    }


def state_label(state: dict[str, Any] | None) -> str:
    if not state:
        return "未练习"
    mastery = float(state.get("mastery", 0))
    if mastery >= 0.85:
        return "掌握良好"
    if mastery >= 0.6:
        return "基本掌握"
    if mastery > 0:
        return "薄弱"
    return "未练习"


def due_human(state: dict[str, Any] | None) -> str:
    if not state:
        return "未安排"
    due = parse_db_time(state.get("due_at"))
    if due is None:
        return "未安排"
    delta = (due.date() - now_dt().date()).days
    if delta < 0:
        return f"逾期 {abs(delta)} 天"
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    return f"{delta} 天后"
