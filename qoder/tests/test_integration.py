from __future__ import annotations

import os
import uuid

import pytest

from wengu import db, mastery, memory
from wengu import powercontext_client as pc

pytestmark = pytest.mark.integration
requires_services = pytest.mark.skipif(
    os.getenv("WENGU_INTEGRATION") != "1",
    reason="设置 WENGU_INTEGRATION=1 后运行；需要本地 seekdb 与 PowerContext",
)


@requires_services
def test_seekdb_fulltext_and_question_state():
    db.init_schema()
    db.import_seed()
    rows, meta = db.search_materials("B+树 插入 分裂", course="数据结构", limit=3)
    assert rows
    assert "B+树" in rows[0]["title"]
    assert "MATCH" in meta["sql"].upper()
    question = db.get_question("Q-DS-006")
    assert question is not None
    user_id = f"pytest-int-{uuid.uuid4().hex[:8]}"
    db.ensure_user(user_id, "pytest")
    wrong = (int(question["answer"]) + 1) % len(question["options"])
    result = mastery.apply_answer(user_id, question, "ses_pytest", selected=wrong)
    assert result["correct"] is False
    state = db.get_state(user_id, question["id"])
    assert state is not None
    assert float(state["mastery"]) < 0.6
    db.clear_demo_users("pytest-int-")


@requires_services
def test_powercontext_memory_roundtrip():
    if not pc.health():
        pytest.skip("PowerContext 未运行")
    user_id = f"pytest-pc-{uuid.uuid4().hex[:8]}"
    memory.reset_scope(user_id)
    memory.remember_profile(user_id, {
        "display_name": "pytest",
        "daily_goal": 6,
        "explanation_style": "concise",
        "updated_at": memory.now_iso(),
    })
    memory.remember_weak_points(user_id, [{
        "knowledge_point": "B+树插入与分裂",
        "course": "数据结构",
        "wrong": 2,
        "total": 3,
        "mastery": 0.3,
        "question_ids": ["Q-DS-006"],
    }])
    memory.remember_session(user_id, {
        "session_id": "ses_pytest",
        "answered": 3,
        "correct": 1,
        "accuracy": 1 / 3,
        "weak_points": ["B+树插入与分裂"],
        "summary": "pytest roundtrip",
    })
    memory.remember_handoff(user_id, {
        "session_id": "ses_pytest",
        "remaining": ["Q-DS-005"],
        "next_focus": "B+树插入与分裂",
        "status": "open",
    })
    recall = memory.recall(user_id)
    assert recall["counts"]["profile"] >= 1
    assert recall["counts"]["weak_point"] >= 1
    assert recall["counts"]["session_summary"] >= 1
    assert recall["counts"]["handoff"] >= 1
    assert recall["prepared"]["status"] in ("ready", "empty")


@requires_services
def test_api_health_and_welcome():
    from fastapi.testclient import TestClient

    from wengu.web import app

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["seekdb"]["ok"] is True
        welcome = client.get("/welcome")
        assert welcome.status_code == 200
        assert "温故" in welcome.text
