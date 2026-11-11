from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config, contextpack, db, demo, ingest, patch, server
from . import state as state_module


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_ingest(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else None
    _print(ingest.ingest(root, reset=not args.no_reset))


def cmd_build(args: argparse.Namespace) -> None:
    pack = contextpack.build_context(args.task, out_path=args.out)
    _print(contextpack.summary(pack))


def cmd_state_show(args: argparse.Namespace) -> None:
    result = state_module.load_state(args.task)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        _print(result)


def cmd_state_save(args: argparse.Namespace) -> None:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    _print(state_module.save_state(args.task, args.stage, data))


def cmd_demo(args: argparse.Namespace) -> None:
    _print(demo.run_demo(args.live_minimax))


def cmd_db_check(_: argparse.Namespace) -> None:
    _print(db.check())


def cmd_server(args: argparse.Namespace) -> None:
    if args.action == "start":
        _print(server.start())
    elif args.action == "status":
        _print(server.status())
    else:
        _print(server.stop())


def cmd_reset(args: argparse.Namespace) -> None:
    patch.reset_from_before()
    db.init_schema()
    db.clear_materials()
    if args.hard:
        shutil.rmtree(config.RUNTIME_DIR, ignore_errors=True)
        shutil.rmtree(config.POWERCONTEXT_HOME, ignore_errors=True)
        (config.ARTIFACTS_DIR / "minimax_edits.json").unlink(missing_ok=True)
    if args.clean_logs:
        patterns = [
            "db_queries.jsonl",
            "powercontext_state.jsonl",
            "minimax_call.json",
            "minimax_request*.json",
            "minimax_response*.json",
            "*context_pack*.json",
            "demo_summary.json",
            "ingest.json",
            "materials.json",
            "sample_state.json",
            "[0-9][0-9]_*.log",
            "[0-9][0-9]_*.json",
        ]
        for pattern in patterns:
            for path in config.RUNS_DIR.glob(pattern):
                path.unlink(missing_ok=True)
        (config.DOCS_DIR / "minimax_dev_record.md").unlink(missing_ok=True)
        (config.ARTIFACTS_DIR / "minimax_edits.json").unlink(missing_ok=True)
    _print(
        {
            "reset": True,
            "hard": args.hard,
            "clean_logs": args.clean_logs,
            "workspace": str(config.WORKSPACE_DIR),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sddctx",
        description="MiniMax 赛道的 SDD 工程上下文模块",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="导入规格、代码和测试到 seekdb")
    ingest_parser.add_argument("--root", help="工程根目录，默认 workspace/taskboard")
    ingest_parser.add_argument("--no-reset", action="store_true", help="增量写入，不先清空材料表")
    ingest_parser.set_defaults(func=cmd_ingest)

    build_parser = subparsers.add_parser("build", help="构造任务上下文包")
    build_parser.add_argument("--task", default=str(config.TASKS_DIR / "add_min_priority.json"))
    build_parser.add_argument("--out", help="上下文包输出路径")
    build_parser.set_defaults(func=cmd_build)

    state_parser = subparsers.add_parser("state", help="读写 PowerContext 任务状态")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)
    state_show = state_sub.add_parser("show", help="读取最新任务状态")
    state_show.add_argument("--task", required=True)
    state_show.add_argument("--json", action="store_true", help="只输出 JSON")
    state_show.set_defaults(func=cmd_state_show)
    state_save = state_sub.add_parser("save", help="保存任务状态")
    state_save.add_argument("--task", required=True)
    state_save.add_argument("--stage", required=True)
    state_save.add_argument("--file", required=True, help="包含状态数据的 JSON 文件")
    state_save.set_defaults(func=cmd_state_save)

    demo_parser = subparsers.add_parser("demo", help="执行完整验证样例")
    demo_parser.add_argument(
        "--live-minimax",
        action="store_true",
        help="真实调用 MiniMax；默认回放 artifacts/minimax_edits.json",
    )
    demo_parser.set_defaults(func=cmd_demo)

    db_parser = subparsers.add_parser("db", help="seekdb 检查")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    db_check = db_sub.add_parser("check", help="检查连接、版本和材料数量")
    db_check.set_defaults(func=cmd_db_check)

    server_parser = subparsers.add_parser("server", help="PowerContext Server 管理")
    server_parser.add_argument("action", choices=["start", "status", "stop"])
    server_parser.set_defaults(func=cmd_server)

    reset_parser = subparsers.add_parser("reset", help="恢复改前快照并清空材料表")
    reset_parser.add_argument("--hard", action="store_true", help="同时删除本地 PowerContext 数据和运行状态")
    reset_parser.add_argument("--clean-logs", action="store_true", help="清理旧运行证据，便于重新生成")
    reset_parser.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> None:
    config.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
