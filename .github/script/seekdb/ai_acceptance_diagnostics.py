#!/usr/bin/env python3
"""Attach a bounded failure-log tail to the GitHub check's public annotations."""
import argparse
from pathlib import Path

LOG_NAMES = ("prerequisites.log", "rust.log", "init.log", "configure.log", "build.log", "core-tests.log", "sql-tests.log")


def escape_annotation(value):
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def log_tail(path, byte_limit=12000, line_limit=60, character_limit=6000):
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - byte_limit))
        tail = stream.read(byte_limit).decode("utf-8", "replace")
    return "\n".join(tail.splitlines()[-line_limit:])[-character_limit:]


def last_failure_annotation(directory):
    sql_failure_dir = directory / "sql" / "failures"
    sql_failures = sorted(sql_failure_dir.glob("*/mysqltest.log")) if sql_failure_dir.is_dir() else []
    sql_failures = [path for path in sql_failures if path.is_file() and not path.is_symlink()]
    if sql_failures:
        per_case_limit = max(800, 5800 // len(sql_failures))
        sections = [str(path.parent.name) + "\n" + log_tail(path, character_limit=per_case_limit)
                    for path in sql_failures]
        return ("SQL case failures\n" + "\n\n".join(sections))[-6000:]
    files = [directory / name for name in LOG_NAMES]
    files = [path for path in files if path.is_file() and not path.is_symlink()]
    if not files:
        return "No build or test log was created; inspect the workflow setup step."
    path = max(files, key=lambda item: item.stat().st_mtime_ns)
    return path.name + "\n" + log_tail(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    message = last_failure_annotation(args.directory)
    print("::error title=AI acceptance failure::" + escape_annotation(message))


if __name__ == "__main__":
    main()
