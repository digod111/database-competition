from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from . import coach, config, db, memory, server
from .text import now_compact, now_iso, write_json

DISPLAY_NAME = "演示学习者"


def _write(name: str, payload: Any) -> Path:
    path = config.RUNS_DIR / name
    write_json(path, payload)
    return path


def _archive_logs() -> Path | None:
    names = [
        "db_queries.jsonl",
        "powercontext_memory.jsonl",
        "coach_plans.jsonl",
        "coach_answers.jsonl",
        "coach_sessions.jsonl",
        "powercontext_restart.jsonl",
        "powercontext_server.log",
        "demo_summary.json",
    ]
    numbered = sorted(config.RUNS_DIR.glob("[0-9][0-9]_*"))
    candidates = [config.RUNS_DIR / name for name in names] + numbered
    archive_dir = config.RUNS_DIR / f"archive_{now_compact()}"
    moved = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(archive_dir / path.name))
            moved.append(path.name)
        except OSError:
            continue
    if not moved:
        try:
            archive_dir.rmdir()
        except OSError:
            pass
        return None
    write_json(archive_dir / "MOVED.json", {"moved": moved, "time": now_iso()})
    return archive_dir


def _recall_subprocess(user_id: str, json_path: Path, log_path: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(config.BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        sys.executable,
        "-m",
        "wengu",
        "recall",
        "--user",
        user_id,
        "--json",
        str(json_path),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(config.BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout, stderr = process.communicate(timeout=180)
    log = [
        f"command: {' '.join(command)}",
        f"cwd: {config.BASE_DIR}",
        f"subprocess_pid: {process.pid}",
        f"return_code: {process.returncode}",
        f"time: {now_iso()}",
        "----- stdout -----",
        (stdout or "").strip(),
        "----- stderr -----",
        (stderr or "").strip(),
    ]
    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
    if process.returncode != 0:
        raise RuntimeError(f"新进程召回失败，见 {log_path}")
    return json.loads(json_path.read_text(encoding="utf-8"))


def _answer(session_id: str, question_id: str, selected: int) -> dict[str, Any]:
    result = coach.submit_answer(session_id, question_id, selected=selected)
    return {
        "question_id": question_id,
        "selected": selected,
        "correct": result["correct"],
        "quality": result["quality"],
        "quality_label": result["quality_label"],
        "mastery": round(result["state"]["mastery"], 4),
        "next_due": result["next_due"],
        "evidence_query_id": result["evidence_query_id"],
        "evidence": [
            {"id": item["id"], "title": item["title"], "score": item["score"]}
            for item in result["explanation"]["evidence"]
        ],
    }


def _question(question_id: str) -> dict[str, Any]:
    question = db.get_question(question_id)
    if not question:
        raise RuntimeError(f"题目不存在：{question_id}")
    return question


def _plan_snapshot(started: dict[str, Any]) -> dict[str, Any]:
    plan = started["plan"]
    items = plan["items"]
    return {
        "generated_at": plan.get("generated_at"),
        "session_id": started["session_id"],
        "user_id": plan["user_id"],
        "goal": plan["goal"],
        "focus_course": plan["focus_course"],
        "profile": plan["profile"],
        "memory_counts": started["memory"]["counts"],
        "memory_prepared": {
            "status": started["memory"].get("prepared_status"),
            "citations": started["memory"].get("prepared_citations", []),
        },
        "candidate_counts": plan["candidate_counts"],
        "items": [
            {
                "id": item["id"],
                "course": item["course"],
                "chapter": item["chapter"],
                "knowledge_point": item["knowledge_point"],
                "score": item["score"],
                "sources": item["sources"],
                "reasons": item["reasons"],
                "memory_ids": item["memory_ids"],
            }
            for item in items
        ],
        "influences": plan["influences"],
        "bplus_items": [item["id"] for item in items if item["knowledge_point"].startswith("B+树")],
    }


def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def run_demo() -> dict[str, Any]:
    config.ensure_dirs()
    started_at = now_iso()
    checks: list[dict[str, Any]] = []
    archive_dir = None
    try:
        print("[0/8] 准备干净的证据环境…", flush=True)
        try:
            server.stop()
        except Exception:
            pass
        archive_dir = _archive_logs()
        server.start()
        db.init_schema()
        imported = db.import_seed()
        db.clear_demo_users("demo-")
        user_id = f"demo-{now_compact()}"
        profile = coach.set_preferences(
            user_id,
            daily_goal=6,
            explanation_style="concise",
            display_name=DISPLAY_NAME,
        )
        print(f"      用户 {user_id}，材料 {imported['materials']} 段，题目 {imported['questions']} 道", flush=True)

        print("[1/8] 第一轮：无历史记忆，按课程重点出题…", flush=True)
        started1 = coach.start_session(user_id, DISPLAY_NAME, focus_course="数据结构")
        plan1 = started1["plan"]
        snapshot1 = _plan_snapshot(started1)
        _write("01_session1_plan.json", snapshot1)
        bplus1 = [item for item in plan1["items"] if item["knowledge_point"].startswith("B+树")]
        target_item = next(
            (item for item in plan1["items"] if item["knowledge_point"] == "B+树插入与分裂"),
            bplus1[0] if bplus1 else None,
        )
        skip_item = next(
            (item for item in plan1["items"] if not item["knowledge_point"].startswith("B+树")),
            None,
        )
        if not target_item or not skip_item:
            raise RuntimeError("第一轮计划没有同时包含 B+树题和可跳过的普通题，demo 无法构造薄弱点与交接。")
        _check(checks, "session1_has_bplus_target", True, f"目标题 {target_item['id']}，B+树题 {len(bplus1)} 道")
        _check(checks, "session1_has_handoff_candidate", True, f"跳过 {skip_item['id']}")

        answers1 = []
        for item in plan1["items"]:
            if item["id"] == skip_item["id"]:
                continue
            question = _question(item["id"])
            if item["id"] == target_item["id"]:
                wrong_index = (int(question["answer"]) + 1) % len(question["options"])
                answers1.append(_answer(started1["session_id"], item["id"], wrong_index))
            else:
                answers1.append(_answer(started1["session_id"], item["id"], int(question["answer"])))
        _write("02_session1_answers.json", {"session_id": started1["session_id"], "skipped": skip_item["id"], "answers": answers1})
        finish1 = coach.finish_session(started1["session_id"])
        _write("03_session1_finish.json", finish1)
        weak_names = [item["knowledge_point"] for item in finish1["weak_points"] if item["mastery"] < 0.6]
        _check(checks, "session1_weak_point_written", target_item["knowledge_point"] in weak_names, f"薄弱点：{weak_names}")
        _check(checks, "session1_handoff_written", skip_item["id"] in finish1["remaining"], f"未完成：{finish1['remaining']}")
        print(f"      答对 {finish1['correct']}/{finish1['answered']}，薄弱点 {weak_names}，交接 {finish1['remaining']}", flush=True)

        print("[2/8] 重启 PowerContext Server，再用新进程召回记忆…", flush=True)
        restart = server.restart()
        _write("04_powercontext_restart.json", restart)
        _check(checks, "powercontext_restart_healthy", bool(restart.get("health") and (restart.get("after") or {}).get("healthy")), "重启前后健康检查")
        recall1 = _recall_subprocess(user_id, config.RUNS_DIR / "05_recall_after_restart.json", config.RUNS_DIR / "05_recall_after_restart.log")
        counts1 = recall1.get("counts", {})
        _check(checks, "recall_after_restart_weak", counts1.get("weak_point", 0) >= 1, f"weak_point={counts1.get('weak_point')}")
        _check(checks, "recall_after_restart_handoff", counts1.get("handoff", 0) >= 1, f"handoff={counts1.get('handoff')}")
        _check(checks, "recall_after_restart_context", counts1.get("prepared_citations", 0) >= 1, f"citations={counts1.get('prepared_citations')}")
        print(f"      新进程召回：{counts1}", flush=True)

        print("[3/8] 第二轮：同一课程，观察记忆如何改变计划…", flush=True)
        started2 = coach.start_session(user_id, DISPLAY_NAME, focus_course="数据结构")
        plan2 = started2["plan"]
        snapshot2 = _plan_snapshot(started2)
        _write("06_session2_plan.json", snapshot2)
        bplus2 = [item for item in plan2["items"] if item["knowledge_point"].startswith("B+树")]
        plan2_ids = [item["id"] for item in plan2["items"]]
        _check(checks, "session2_bplus_expanded", len(bplus2) > len(bplus1), f"第一轮 {len(bplus1)} 道 → 第二轮 {len(bplus2)} 道")
        _check(checks, "session2_handoff_included", skip_item["id"] in plan2_ids, f"补做 {skip_item['id']}")
        kinds2 = [influence["memory_kind"] for influence in plan2["influences"]]
        _check(checks, "session2_weak_influence_visible", "weak_point" in kinds2, f"影响类型：{kinds2}")
        _check(checks, "session2_handoff_influence_visible", "handoff" in kinds2, f"影响类型：{kinds2}")
        print(f"      第二轮 B+树 {len(bplus2)} 道，计划 {plan2_ids}", flush=True)

        answers2 = []
        for item in plan2["items"]:
            question = _question(item["id"])
            answers2.append(_answer(started2["session_id"], item["id"], int(question["answer"])))
        _write("07_session2_answers.json", {"session_id": started2["session_id"], "answers": answers2})
        finish2 = coach.finish_session(started2["session_id"])
        _write("08_session2_finish.json", finish2)
        _check(checks, "session2_completed", not finish2["remaining"], f"剩余 {finish2['remaining']}")
        print(f"      第二轮答对 {finish2['correct']}/{finish2['answered']}", flush=True)

        print("[4/8] 再次用新进程召回，确认薄弱点掌握度变化与交接完成…", flush=True)
        recall2 = _recall_subprocess(user_id, config.RUNS_DIR / "09_recall_after_session2.json", config.RUNS_DIR / "09_recall_after_session2.log")
        target_before = next((hit for hit in recall1.get("weak_points", []) if hit.get("payload", {}).get("knowledge_point") == target_item["knowledge_point"]), None)
        target_after = next((hit for hit in recall2.get("weak_points", []) if hit.get("payload", {}).get("knowledge_point") == target_item["knowledge_point"]), None)
        before_mastery = float((target_before or {}).get("payload", {}).get("mastery", 0))
        after_mastery = float((target_after or {}).get("payload", {}).get("mastery", 0))
        _check(checks, "mastery_improved_after_review", after_mastery > before_mastery, f"{before_mastery:.3f} → {after_mastery:.3f}")
        handoff2 = (recall2.get("handoff") or {}).get("payload") or {}
        _check(checks, "handoff_completed", handoff2.get("status") == "completed", f"status={handoff2.get('status')}")

        print("[5/8] 检索样例：B+树 插入 分裂…", flush=True)
        search = coach.library_search(user_id, "B+树 插入 分裂", course="数据结构")
        _write("10_search_demo.json", search)
        top_title = search["results"][0]["title"] if search["results"] else ""
        top_score = search["results"][0]["score"] if search["results"] else 0
        _check(checks, "search_hit_with_source", bool(search["results"]) and "B+树" in top_title, f"{top_title} / score={top_score}")
        query_record = None
        queries_path = config.RUNS_DIR / "db_queries.jsonl"
        if queries_path.exists():
            for line in reversed(queries_path.read_text(encoding="utf-8", errors="replace").splitlines()):
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if candidate.get("query_id") == search["query_id"]:
                    query_record = candidate
                    break
        _write("11_search_query_record.json", query_record or {"missing": search["query_id"]})
        _check(checks, "search_query_logged", query_record is not None, f"query_id={search['query_id']}")
        _check(checks, "seekdb_fulltext_used", bool(query_record and "MATCH" in (query_record.get("sql") or "").upper()), "SQL 包含 MATCH ... AGAINST")

        print("[6/8] 汇总真实运行统计…", flush=True)
        stats = db.stats_overview(user_id)
        _write("12_usage_stats.json", stats)
        _check(checks, "attempts_recorded", stats["attempts"] >= 11, f"attempts={stats['attempts']}")
        _check(checks, "accuracy_recorded", 0 < stats["accuracy"] <= 1, f"accuracy={stats['accuracy']}")

        print("[7/8] 校验证据文件…", flush=True)
        db_queries = config.RUNS_DIR / "db_queries.jsonl"
        memory_log = config.RUNS_DIR / "powercontext_memory.jsonl"
        _check(checks, "db_query_log_exists", db_queries.exists() and db_queries.stat().st_size > 0, str(db_queries))
        _check(checks, "memory_log_exists", memory_log.exists() and memory_log.stat().st_size > 0, str(memory_log))
        _check(checks, "restart_log_exists", (config.RUNS_DIR / "powercontext_restart.jsonl").exists(), "powercontext_restart.jsonl")

        print("[8/8] 写入 demo 总结…", flush=True)
        check_counts = db.check()
        summary = {
            "demo_user": user_id,
            "display_name": DISPLAY_NAME,
            "started_at": started_at,
            "finished_at": now_iso(),
            "scope_id": memory.ensure_scope(user_id),
            "seekdb": {"version": check_counts["version"], "counts": check_counts["counts"]},
            "profile": profile,
            "session1": {
                "session_id": started1["session_id"],
                "plan_items": [item["id"] for item in plan1["items"]],
                "bplus_count": len(bplus1),
                "skipped": skip_item["id"],
                "answered": finish1["answered"],
                "correct": finish1["correct"],
                "weak_points": finish1["weak_points"],
                "memory": finish1["memory"],
            },
            "session2": {
                "session_id": started2["session_id"],
                "plan_items": plan2_ids,
                "bplus_count": len(bplus2),
                "influences": plan2["influences"],
                "answered": finish2["answered"],
                "correct": finish2["correct"],
                "weak_points": finish2["weak_points"],
                "memory": finish2["memory"],
            },
            "restart": restart,
            "recall_after_restart": {
                "counts": recall1.get("counts"),
                "weak_points": recall1.get("weak_points"),
                "handoff": recall1.get("handoff"),
                "prepared": recall1.get("prepared"),
            },
            "recall_after_session2": {
                "counts": recall2.get("counts"),
                "weak_points": recall2.get("weak_points"),
                "handoff": recall2.get("handoff"),
            },
            "search": {
                "query": search["query"],
                "query_id": search["query_id"],
                "top_results": [
                    {"title": item["title"], "score": item["score"], "source_path": item["source_path"]}
                    for item in search["results"][:3]
                ],
            },
            "stats": stats,
            "checks": checks,
            "archive": str(archive_dir) if archive_dir else None,
            "files": [
                "01_session1_plan.json",
                "02_session1_answers.json",
                "03_session1_finish.json",
                "04_powercontext_restart.json",
                "05_recall_after_restart.json",
                "05_recall_after_restart.log",
                "06_session2_plan.json",
                "07_session2_answers.json",
                "08_session2_finish.json",
                "09_recall_after_session2.json",
                "10_search_demo.json",
                "11_search_query_record.json",
                "12_usage_stats.json",
                "demo_summary.json",
                "db_queries.jsonl",
                "powercontext_memory.jsonl",
                "coach_plans.jsonl",
                "coach_answers.jsonl",
                "powercontext_restart.jsonl",
            ],
        }
        _write("demo_summary.json", summary)
        failed = [item for item in checks if not item["passed"]]
        if failed:
            raise RuntimeError("demo 校验未通过：" + "; ".join(item["name"] for item in failed))
        print("", flush=True)
        print("=== 温故 demo 完成 ===", flush=True)
        print(f"用户：{user_id}", flush=True)
        print(f"PowerContext Scope：{summary['scope_id']}", flush=True)
        print(f"第一轮：{summary['session1']['plan_items']}，B+树 {len(bplus1)} 道", flush=True)
        print(f"第二轮：{plan2_ids}，B+树 {len(bplus2)} 道", flush=True)
        print(f"重启：before={restart['before']['healthy']} → after={restart['after']['healthy']}", flush=True)
        print(f"检索：{top_title}（score={top_score}，query_id={search['query_id']}）", flush=True)
        print(f"证据目录：{config.RUNS_DIR}", flush=True)
        print(f"校验：{len(checks) - len(failed)}/{len(checks)} 通过", flush=True)
        return summary
    except Exception as error:
        write_json(
            config.RUNS_DIR / "demo_error.json",
            {"time": now_iso(), "error": str(error), "traceback": traceback.format_exc()},
        )
        raise


def main() -> int:
    try:
        run_demo()
    except Exception as error:
        print(f"demo 失败：{error}", file=sys.stderr)
        return 1
    return 0
