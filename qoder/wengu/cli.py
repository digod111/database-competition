from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, db, demo, memory, server
from . import powercontext_client as pc
from .text import write_json


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _recall_summary(recall_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope_id": recall_result["scope_id"],
        "recalled_at": recall_result["recalled_at"],
        "counts": recall_result["counts"],
        "profile": recall_result["profile"]["payload"] if recall_result.get("profile") else None,
        "weak_points": [hit["payload"] for hit in recall_result.get("weak_points", [])],
        "latest_session": recall_result["sessions"][0]["payload"] if recall_result.get("sessions") else None,
        "handoff": recall_result["handoff"]["payload"] if recall_result.get("handoff") else None,
        "prepared": {
            "query": recall_result["prepared"]["query"],
            "status": recall_result["prepared"]["status"],
            "content_bytes": recall_result["prepared"]["content_bytes"],
            "citations": [
                {"artifact_id": item["artifact_id"], "kind": item["kind"], "content": item["content"]}
                for item in recall_result["prepared"]["citations"]
            ],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wengu", description="温故 · 复习教练")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化 seekdb 表并导入种子笔记/题库")

    sub.add_parser("db-check", help="查看 seekdb 连接与各表数量")

    pc_parser = sub.add_parser("pc", help="管理 PowerContext Server")
    pc_parser.add_argument("action", choices=["start", "stop", "status", "restart"])

    recall_parser = sub.add_parser("recall", help="以新进程召回某个用户的 PowerContext 记忆")
    recall_parser.add_argument("--user", required=True)
    recall_parser.add_argument("--json", dest="json_path")

    search_parser = sub.add_parser("search", help="直接检索 seekdb 笔记")
    search_parser.add_argument("query")
    search_parser.add_argument("--course")
    search_parser.add_argument("--limit", type=int, default=8)

    stats_parser = sub.add_parser("stats", help="导出使用统计")
    stats_parser.add_argument("--user")
    stats_parser.add_argument("--json", dest="json_path")

    sub.add_parser("demo", help="运行两轮复习 + 重启召回 + 检索的完整验证")

    server_parser = sub.add_parser("server", help="启动 Web 应用")
    server_parser.add_argument("--host", default=config.WENGU_HOST)
    server_parser.add_argument("--port", type=int, default=config.WENGU_PORT)

    reset_parser = sub.add_parser("reset", help="清空某个用户的学习数据并切换记忆 Scope")
    reset_parser.add_argument("--user", required=True)

    sub.add_parser("users", help="列出本机学习者")

    scope_parser = sub.add_parser("scope", help="查看/创建某个用户的 PowerContext Scope")
    scope_parser.add_argument("--user", required=True)

    sub.add_parser("health", help="检查 seekdb 与 PowerContext")

    args = parser.parse_args(argv)
    config.ensure_dirs()

    if args.command == "init":
        db.init_schema()
        result = db.import_seed()
        _print({"ok": True, **result, "db_check": db.check()})
        return 0

    if args.command == "db-check":
        _print(db.check())
        return 0

    if args.command == "pc":
        if args.action == "start":
            _print(server.start())
        elif args.action == "stop":
            _print(server.stop())
        elif args.action == "status":
            _print(server.status())
        else:
            _print(server.restart())
        return 0

    if args.command == "recall":
        recall_result = memory.recall(args.user)
        summary = _recall_summary(recall_result)
        if args.json_path:
            write_json(Path(args.json_path), recall_result)
        _print(summary)
        return 0

    if args.command == "search":
        rows, meta = db.search_materials(
            args.query, course=args.course, limit=args.limit, purpose="cli_search"
        )
        _print(
            {
                "query": args.query,
                "query_id": meta.get("query_id"),
                "elapsed_ms": meta.get("elapsed_ms"),
                "results": [
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "course": row["course"],
                        "chapter": row["chapter"],
                        "source_path": row["source_path"],
                        "score": round(float(row.get("score") or 0), 4),
                    }
                    for row in rows
                ],
            }
        )
        return 0

    if args.command == "stats":
        result = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "scope": "all" if not args.user else f"user:{args.user}",
            "stats": db.stats_overview(args.user),
            "usage_events": db.usage_event_counts(),
            "feedback": [
                {"rating": row["rating"], "comment": row["comment"], "created_at": row["created_at"]}
                for row in db.list_feedback(50)
            ],
        }
        if args.json_path:
            write_json(Path(args.json_path), result)
        _print(result)
        return 0

    if args.command == "demo":
        return demo.main()

    if args.command == "server":
        import uvicorn

        db.init_schema()
        counts = db.check()["counts"]
        if counts.get("materials", 0) == 0:
            db.import_seed()
        server.start()
        uvicorn.run("wengu.web:app", host=args.host, port=args.port, reload=False)
        return 0

    if args.command == "reset":
        db.clear_user_data(args.user)
        scope_id = memory.reset_scope(args.user)
        _print({"ok": True, "user": args.user, "new_scope_id": scope_id})
        return 0

    if args.command == "users":
        _print({"users": db.list_users(100)})
        return 0

    if args.command == "scope":
        _print({"user": args.user, "scope_id": memory.ensure_scope(args.user)})
        return 0

    if args.command == "health":
        _print(
            {
                "seekdb": db.check(),
                "powercontext": {"healthy": pc.health(), "url": config.POWERCONTEXT_URL, "status": server.status()},
            }
        )
        return 0

    print(f"未知命令：{args.command}", file=sys.stderr)
    return 1
