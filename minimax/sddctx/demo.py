from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config, contextpack, db, ingest, minimax_client, patch, runner, server
from . import state as state_module
from .retrieve import load_task
from .text import now_iso, write_json

TASK_FILE = config.TASKS_DIR / "add_min_priority.json"


def _run_cli(args: list[str], log_name: str) -> dict:
    command = [sys.executable, "-m", "sddctx", *args]
    display_command = ["python", "-m", "sddctx", *args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        command,
        cwd=config.BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    log = (
        f"$ {' '.join(display_command)}\n"
        "cwd: minimax\n"
        f"exit_code: {result.returncode}\n"
        "--- stdout ---\n"
        f"{result.stdout}\n"
        "--- stderr ---\n"
        f"{result.stderr}\n"
    )
    (config.RUNS_DIR / log_name).write_text(log, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"子进程失败（{log_name}）：\n{log[-3000:]}")
    return {"stdout": result.stdout, "stderr": result.stderr, "log": f"docs/runs/{log_name}"}


def _verification_entry(name: str, result: dict) -> dict:
    return {
        "name": name,
        "command": result["command"],
        "exit_code": result["exit_code"],
        "log": result["log"],
    }


def spec_update_check(task_id: str) -> dict:
    spec_path = config.WORKSPACE_DIR / "specs/task_filter.md"
    original = spec_path.read_text(encoding="utf-8")
    addition = (
        "\n\n## REQ-FILTER-005：最低优先级的比较字段\n\n"
        "当调用方传入 `min_priority` 时，实现必须使用 `priority` 字段进行比较，"
        "不得根据 `title` 或其他字段推断优先级。该要求属于任务列表筛选的一部分。\n"
    )
    checked: dict[str, Any] = {"new_requirement": "REQ-FILTER-005", "found": False}
    try:
        spec_path.write_text(original + addition, encoding="utf-8")
        ingest.ingest(config.WORKSPACE_DIR, reset=False)
        state_result = state_module.load_state(task_id)
        pack = contextpack.build_context(
            TASK_FILE,
            state_result,
            out_path=config.RUNS_DIR / "09_spec_update_context.json",
        )
        symbols = [item.get("symbol") for item in pack["specs"]]
        checked = {
            "new_requirement": "REQ-FILTER-005",
            "found": "REQ-FILTER-005" in symbols,
            "spec_symbols": symbols,
            "context_pack_id": pack["pack_id"],
            "query_terms": pack["selection"]["query_terms"],
        }
        if not checked["found"]:
            raise RuntimeError("规格更新并重新导入后，上下文没有使用新增的 REQ-FILTER-005")
    finally:
        spec_path.write_text(original, encoding="utf-8")
        ingest.ingest(config.WORKSPACE_DIR, reset=True)
    (config.RUNS_DIR / "09_spec_update_check.log").write_text(
        json.dumps(checked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return checked


def run_demo(live_minimax: bool) -> dict:
    config.ensure_dirs()
    task = load_task(TASK_FILE)
    task_id = task["id"]
    server_info = server.start()
    schema = db.init_schema()
    patch.reset_from_before()

    before_target = runner.run_pytest(
        config.BEFORE_DIR, ["tests/test_task_filter.py"], "01_before_target_tests.log"
    )
    runner.expect_failure(before_target, "unexpected keyword argument")
    before_regression = runner.run_pytest(
        config.BEFORE_DIR, ["tests/test_task_service_regression.py"], "02_before_regression_tests.log"
    )
    runner.expect_success(before_regression)

    ingest_info = ingest.ingest(config.WORKSPACE_DIR, reset=True)
    state_empty = state_module.load_state(task_id)
    context_empty = contextpack.build_context(
        TASK_FILE, state_empty, out_path=config.RUNS_DIR / "03_context_pack_empty_state.json"
    )
    state_module.save_state(
        task_id,
        "context_built",
        {
            "completed": ["ingest_materials", "build_context"],
            "todo": ["call_minimax", "apply_patch", "run_target_tests", "run_regression_tests"],
            "verification": [
                _verification_entry("before_target_tests", before_target),
                _verification_entry("before_regression_tests", before_regression),
            ],
            "context_pack_id": context_empty["pack_id"],
            "sources": {
                "specs": [item["symbol"] for item in context_empty["specs"]],
                "code": [item["symbol"] for item in context_empty["code"]],
                "tests": [item["symbol"] for item in context_empty["tests"]],
            },
        },
    )

    _run_cli(
        ["state", "show", "--task", task_id, "--json"],
        "04_state_after_restart.log",
    )
    _run_cli(
        [
            "build",
            "--task",
            str(TASK_FILE),
            "--out",
            str(config.RUNS_DIR / "05_context_pack_resumed.json"),
        ],
        "05_build_after_restart.log",
    )
    context_resumed = json.loads(
        (config.RUNS_DIR / "05_context_pack_resumed.json").read_text(encoding="utf-8")
    )
    if not context_resumed["powercontext"]["state_found"]:
        raise RuntimeError("模块重启后没有从 PowerContext 读到任务状态")
    if "ingest_materials" not in context_resumed["status"]["completed"]:
        raise RuntimeError("重启后读到的状态没有被用于后续上下文")

    if live_minimax:
        modification = minimax_client.modify_with_minimax(task, context_resumed)
    else:
        modification = minimax_client.apply_recorded(task, context_resumed)

    state_module.save_state(
        task_id,
        "patch_applied",
        {
            "completed": ["ingest_materials", "build_context", "call_minimax", "apply_patch"],
            "todo": ["run_target_tests", "run_regression_tests"],
            "verification": [
                _verification_entry("before_target_tests", before_target),
                _verification_entry("before_regression_tests", before_regression),
            ],
            "context_pack_id": context_resumed["pack_id"],
            "patch_summary": modification["plan"].get("summary", ""),
            "changed_files": modification["changed_files"],
            "modification_mode": modification["mode"],
        },
    )

    after_target = runner.run_pytest(
        config.WORKSPACE_DIR, ["tests/test_task_filter.py"], "06_after_target_tests.log"
    )
    runner.expect_success(after_target)
    after_regression = runner.run_pytest(
        config.WORKSPACE_DIR, ["tests/test_task_service_regression.py"], "07_after_regression_tests.log"
    )
    runner.expect_success(after_regression)
    after_all = runner.run_pytest(config.WORKSPACE_DIR, [], "08_after_all_tests.log")
    runner.expect_success(after_all)

    verification = [
        _verification_entry("before_target_tests", before_target),
        _verification_entry("before_regression_tests", before_regression),
        _verification_entry("after_target_tests", after_target),
        _verification_entry("after_regression_tests", after_regression),
        _verification_entry("after_all_tests", after_all),
    ]
    state_module.save_state(
        task_id,
        "tests_passed",
        {
            "completed": [
                "ingest_materials",
                "build_context",
                "call_minimax",
                "apply_patch",
                "run_target_tests",
                "run_regression_tests",
            ],
            "todo": [],
            "verification": verification,
            "context_pack_id": context_resumed["pack_id"],
            "patch_summary": modification["plan"].get("summary", ""),
            "changed_files": modification["changed_files"],
            "modification_mode": modification["mode"],
        },
    )

    diff = patch.generate_diff(
        config.BEFORE_DIR, config.WORKSPACE_DIR, config.ARTIFACTS_DIR / "changes.diff"
    )
    if diff["changed_files"] != ["src/taskboard/service.py"]:
        raise RuntimeError(f"代码差异涉及了未预期文件：{diff['changed_files']}")

    spec_update = spec_update_check(task_id)
    summary = {
        "time": now_iso(),
        "task_id": task_id,
        "mode": "live" if live_minimax else "replay",
        "seekdb_schema": schema,
        "ingest": ingest_info,
        "server": server_info,
        "context_empty_pack_id": context_empty["pack_id"],
        "context_resumed_pack_id": context_resumed["pack_id"],
        "powercontext_state_found_after_restart": context_resumed["powercontext"]["state_found"],
        "powercontext_scope_id": context_resumed["powercontext"]["scope_id"],
        "modification": {
            "mode": modification["mode"],
            "summary": modification["plan"].get("summary", ""),
            "changed_files": modification["changed_files"],
        },
        "tests": verification,
        "diff": diff,
        "spec_update_check": spec_update,
    }
    write_json(config.RUNS_DIR / "demo_summary.json", summary)
    return summary
