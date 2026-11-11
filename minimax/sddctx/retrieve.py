from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db
from .text import query_terms


class ContextBuildError(RuntimeError):
    pass


def load_task(task_path: str | Path) -> dict[str, Any]:
    path = Path(task_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise FileNotFoundError(f"任务文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _task_text(task: dict[str, Any]) -> str:
    parts = [
        task.get("id", ""),
        task.get("title", ""),
        task.get("description", ""),
        " ".join(task.get("acceptance", [])),
        " ".join(task.get("must_keep", [])),
        " ".join(task.get("tests", [])),
        " ".join(task.get("code_targets", [])),
    ]
    return "\n".join(parts)


def _parse_test_ref(reference: str) -> tuple[str | None, str]:
    if "::" in reference:
        path, name = reference.split("::", 1)
        return path, name
    return None, reference


def _with_reason(rows: list[dict], reason: str) -> list[dict]:
    result = []
    for row in rows:
        item = dict(row)
        item["selected_because"] = reason
        result.append(item)
    return result


def _merge(required: list[dict], retrieved: list[dict], limit: int) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in required:
        merged.setdefault(row["id"], row)
    for row in retrieved:
        if row["id"] not in merged:
            merged[row["id"]] = row
    items = list(merged.values())
    required_items = [item for item in items if item.get("selected_because") == "required"]
    retrieved_items = [item for item in items if item.get("selected_because") != "required"]
    retrieved_items.sort(key=lambda item: (-float(item.get("score") or 0), item.get("path", ""), item["id"]))
    return (required_items + retrieved_items)[:limit]


def retrieve(task: dict[str, Any]) -> dict[str, Any]:
    terms = query_terms(_task_text(task))
    specs, spec_meta = db.search_materials(terms, ["spec"], 12, "retrieve_specs")
    code, code_meta = db.search_materials(terms, ["code"], 12, "retrieve_code")
    tests, test_meta = db.search_materials(terms, ["test"], 12, "retrieve_tests")

    required_spec_ids = list(dict.fromkeys(task.get("acceptance", []) + task.get("must_keep", [])))
    required_specs = db.get_by_symbols(required_spec_ids, kinds=["spec"]) if required_spec_ids else []
    found_specs = {row["symbol"] for row in required_specs}
    missing_specs = [item for item in required_spec_ids if item not in found_specs]
    if missing_specs:
        raise ContextBuildError(f"规格来源不存在：{missing_specs}")

    required_code_symbols = list(dict.fromkeys(task.get("code_targets", [])))
    required_code = db.get_by_symbols(required_code_symbols, kinds=["code"]) if required_code_symbols else []
    found_code = {row["symbol"] for row in required_code}
    missing_code = [item for item in required_code_symbols if item not in found_code]
    if missing_code:
        raise ContextBuildError(f"代码符号不存在：{missing_code}")

    required_tests: list[dict] = []
    missing_tests: list[str] = []
    test_paths: list[str] = []
    for reference in task.get("tests", []):
        path, name = _parse_test_ref(reference)
        rows = []
        if path:
            rows = db.get_by_paths([path], symbol=name, kinds=["test"])
            test_paths.append(path)
        if not rows:
            rows = db.get_by_symbols([name], kinds=["test"])
        if not rows:
            missing_tests.append(reference)
        required_tests.extend(rows)
    if missing_tests:
        raise ContextBuildError(f"测试来源不存在：{missing_tests}")

    headers = db.get_by_paths(sorted(set(test_paths)), symbol="__header__", kinds=["test"])
    required_tests.extend(headers)

    specs = _merge(_with_reason(required_specs, "required"), _with_reason(specs, "retrieved"), 20)
    code = _merge(_with_reason(required_code, "required"), _with_reason(code, "retrieved"), 20)
    tests = _merge(_with_reason(required_tests, "required"), _with_reason(tests, "retrieved"), 20)

    if not specs or not code or not tests:
        raise ContextBuildError("上下文缺少规格、代码或测试，无法构造任务上下文")

    return {
        "terms": terms,
        "specs": specs,
        "code": code,
        "tests": tests,
        "queries": [spec_meta, code_meta, test_meta],
        "required": {
            "specs": required_spec_ids,
            "code": required_code_symbols,
            "tests": task.get("tests", []),
        },
    }
