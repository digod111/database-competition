from __future__ import annotations

from datetime import datetime

import pytest

from wengu.mastery import grade_mcq, grade_short, initial_state, update_state


NOW = datetime(2026, 1, 1, 12, 0, 0)


def test_initial_state():
    state = initial_state()
    assert state["mastery"] == 0.5
    assert state["ease"] == 2.5
    assert state["streak"] == 0


def test_first_correct_answer_schedules_tomorrow():
    state = update_state(None, quality=4, now=NOW)
    assert state["mastery"] == pytest.approx(0.605)
    assert state["interval_days"] == 1.0
    assert state["due_at"] == "2026-01-02 12:00:00"
    assert state["correct_count"] == 1
    assert state["streak"] == 1
    assert state["last_result"] == "correct"


def test_first_wrong_answer_marks_weak():
    state = update_state(None, quality=0, now=NOW)
    assert state["mastery"] == pytest.approx(0.325)
    assert state["interval_days"] == 1.0
    assert state["wrong_count"] == 1
    assert state["streak"] == 0
    assert state["last_result"] == "wrong"


def test_second_correct_uses_three_day_interval():
    first = update_state(None, quality=4, now=NOW)
    second = update_state(first, quality=4, now=NOW)
    assert second["streak"] == 2
    assert second["interval_days"] == 3.0
    assert second["mastery"] > first["mastery"]


def test_mastery_recovers_after_a_wrong_answer():
    wrong = update_state(None, quality=0, now=NOW)
    partial = update_state(wrong, quality=3, now=NOW)
    assert partial["mastery"] > wrong["mastery"]
    assert partial["last_result"] == "partial"


def test_grade_mcq():
    question = {"type": "mcq", "answer": 2}
    assert grade_mcq(question, 2) == (True, 4)
    assert grade_mcq(question, 1) == (False, 0)
    assert grade_mcq(question, "bad") == (False, 0)


def test_grade_short_ratings():
    assert grade_short("correct") == (True, 4)
    assert grade_short("partial") == (False, 3)
    assert grade_short("wrong") == (False, 0)
