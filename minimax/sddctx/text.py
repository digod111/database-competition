from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid4().hex[:16]}"


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_IDENT_RE = re.compile(r"REQ-[A-Za-z]+-\d+|[A-Za-z_][A-Za-z0-9_]*")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "with", "must", "should", "can",
    "list", "tasks", "task", "return", "returns", "when", "while", "not", "no",
}

SYNONYMS = {
    "筛选": ("filter", "list_tasks"),
    "过滤": ("filter",),
    "优先级": ("priority",),
    "最低": ("min",),
    "边界": ("boundary", "inclusive"),
    "越界": ("range", "valueerror"),
    "校验": ("validate", "valueerror"),
    "异常": ("raise", "error"),
    "范围": ("range", "value"),
    "回归": ("regression",),
    "兼容": ("compat", "compatibility"),
    "任务": ("task",),
    "列表": ("list",),
    "状态": ("status",),
    "排序": ("order",),
    "顺序": ("order",),
    "副本": ("copy",),
}


def _split_identifier(token: str) -> set[str]:
    pieces: set[str] = set()
    for part in re.split(r"[^A-Za-z0-9]+", token):
        if not part:
            continue
        pieces.add(part.lower())
        for match in re.finditer(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", part):
            piece = match.group(0).lower()
            if piece:
                pieces.add(piece)
    return pieces


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _IDENT_RE.finditer(text):
        token = match.group(0).lower()
        tokens.add(token)
        tokens.update(_split_identifier(token))
    for match in _CJK_RE.finditer(text):
        sequence = match.group(0)
        tokens.add(sequence)
        for index in range(len(sequence) - 1):
            tokens.add(sequence[index : index + 2])
    return tokens


def query_terms(text: str) -> list[str]:
    base = tokenize(text)
    expanded = set(base)
    for token in list(base):
        for chinese, english in SYNONYMS.items():
            if chinese in token:
                expanded.update(english)
    ordered: list[str] = []
    for token in sorted(expanded, key=lambda item: (-len(item), item)):
        if len(token) > 1 and token not in STOPWORDS and token not in ordered:
            ordered.append(token)
    return ordered[:40]


def keywords_for(content: str, path: str, symbol: str | None) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for source in (tokenize(symbol or ""), tokenize(path), tokenize(content)):
        for token in sorted(source, key=lambda item: (-len(item), item)):
            if len(token) < 2 or token in STOPWORDS or token in seen:
                continue
            seen.add(token)
            ordered.append(token)
    return " ".join(ordered)[:1000]
