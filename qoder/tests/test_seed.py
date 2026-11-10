from __future__ import annotations

import json

from wengu import config, db


def test_seed_materials_are_valid():
    rows = db.build_material_rows(config.SEED_DIR)
    assert len(rows) >= 30
    assert all(row["course"] for row in rows)
    assert all(row["content"] for row in rows)
    assert all(row["search_text"] for row in rows)
    assert len({row["id"] for row in rows}) == len(rows)


def test_seed_questions_are_valid_and_sources_resolve():
    rows = db.build_material_rows(config.SEED_DIR)
    material_ids = {(row["source_path"], row["heading"]): row["id"] for row in rows}
    payload = json.loads((config.SEED_DIR / "questions.json").read_text(encoding="utf-8"))
    questions = db._validate_questions(payload)
    assert len(questions) == 27
    for question in questions:
        assert (question["source_path"], question["source_heading"]) in material_ids
        if question["type"] == "mcq":
            options = json.loads(question["options_json"])
            answer = json.loads(question["answer_json"])
            assert len(options) >= 2
            assert 0 <= answer < len(options)


def test_seed_covers_bplus_questions():
    payload = json.loads((config.SEED_DIR / "questions.json").read_text(encoding="utf-8"))
    bplus = [item for item in payload if item["knowledge_point"].startswith("B+树")]
    assert len(bplus) >= 3
