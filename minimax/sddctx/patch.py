from __future__ import annotations

import difflib
import shutil
from pathlib import Path

from . import config
from .text import digest, write_json

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}


def _normalize_relative(path: str) -> Path:
    value = path.replace("\\", "/").lstrip("/")
    while value.startswith("./"):
        value = value[2:]
    for prefix in ("workspace/taskboard/", "taskboard/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return Path(value)


def apply_edits(root: Path, edits: list[dict]) -> list[str]:
    root = root.resolve()
    changed: list[str] = []
    for edit in edits:
        relative = _normalize_relative(edit["path"])
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError(f"拒绝修改工程目录之外的路径：{edit['path']}") from error
        if not target.exists():
            raise ValueError(f"目标文件不存在：{edit['path']}")
        text = target.read_text(encoding="utf-8")
        search = edit["search"]
        replace = edit["replace"]
        occurrences = text.count(search)
        if occurrences != 1:
            raise ValueError(
                f"{edit['path']} 中 search 片段出现 {occurrences} 次，必须恰好 1 次"
            )
        target.write_text(text.replace(search, replace, 1), encoding="utf-8")
        changed.append(relative.as_posix())
    return changed


def copy_tree(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*SKIP_DIRS))


def reset_from_before() -> None:
    if not config.BEFORE_DIR.exists():
        raise FileNotFoundError(f"改前快照不存在：{config.BEFORE_DIR}")
    copy_tree(config.BEFORE_DIR, config.WORKSPACE_DIR)


def _iter_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files[path.relative_to(root).as_posix()] = path
    return files


def generate_diff(before_root: Path, after_root: Path, out_path: Path) -> dict:
    before_files = _iter_files(before_root)
    after_files = _iter_files(after_root)
    all_paths = sorted(set(before_files) | set(after_files))
    diff_lines: list[str] = []
    changed: list[str] = []
    manifest: list[dict] = []
    for relative in all_paths:
        before_path = before_files.get(relative)
        after_path = after_files.get(relative)
        before_text = before_path.read_text(encoding="utf-8") if before_path else ""
        after_text = after_path.read_text(encoding="utf-8") if after_path else ""
        if before_text != after_text:
            changed.append(relative)
            diff_lines.extend(
                difflib.unified_diff(
                    before_text.splitlines(keepends=True),
                    after_text.splitlines(keepends=True),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        manifest.append(
            {
                "path": relative,
                "before_sha256": digest(before_text) if before_path else None,
                "after_sha256": digest(after_text) if after_path else None,
                "changed": before_text != after_text,
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(diff_lines), encoding="utf-8")
    write_json(config.ARTIFACTS_DIR / "file_manifest.json", manifest)
    return {"changed_files": changed, "diff_path": str(out_path), "files": len(all_paths)}
