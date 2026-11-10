from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from markupsafe import Markup

DB_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_dt() -> datetime:
    return datetime.now()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def to_db_time(value: datetime) -> str:
    return value.strftime(DB_TIME_FORMAT)


def parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, DB_TIME_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def meta_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,，;；、]", value) if item.strip()]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    text = normalize_text(text)
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        meta[key.strip()] = value
    body = "\n".join(lines[end + 1 :]).strip()
    return meta, body


def chunk_markdown(body: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            content = "\n".join(buffer).strip()
            if content:
                sections.append({"heading": heading or "概述", "content": content})
            heading = line[3:].strip() or "概述"
            buffer = []
        else:
            buffer.append(line)
    content = "\n".join(buffer).strip()
    if content:
        sections.append({"heading": heading or "概述", "content": content})
    return sections


def split_query_terms(query: str) -> list[str]:
    raw = re.split(r"[\s,，。;；、:：!?！？()（）\[\]【】<>《》/\\|\"']+", query)
    terms: list[str] = []
    for token in raw:
        token = token.strip("-*_`~")
        if len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _window_around(content: str, terms: Iterable[str], max_len: int) -> tuple[str, bool, bool]:
    lowered = content.lower()
    position = -1
    for term in terms:
        found = lowered.find(term.lower())
        if found >= 0 and (position < 0 or found < position):
            position = found
    if position < 0:
        window = content[:max_len]
        return window, False, len(content) > max_len
    start = max(0, position - max_len // 3)
    end = min(len(content), start + max_len)
    start = max(0, end - max_len)
    return content[start:end], start > 0, end < len(content)


def highlight_html(content: str, query: str, max_len: int = 260) -> Markup:
    terms = split_query_terms(query)
    window, has_prefix, has_suffix = _window_around(content, terms, max_len)
    escaped = html.escape(window)
    if terms:
        escaped_terms = sorted({html.escape(term) for term in terms if term}, key=len, reverse=True)
        if escaped_terms:
            pattern = re.compile("|".join(re.escape(term) for term in escaped_terms), re.IGNORECASE)
            escaped = pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", escaped)
    prefix = "…" if has_prefix else ""
    suffix = "…" if has_suffix else ""
    return Markup(f"{prefix}{escaped}{suffix}")


def excerpt(text: str, length: int = 120) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed if len(collapsed) <= length else collapsed[: length - 1] + "…"


def human_due(value: str | None) -> str:
    due = parse_db_time(value)
    if due is None:
        return "未安排"
    now = datetime.now()
    delta_days = (due.date() - now.date()).days
    if delta_days < 0:
        return f"已逾期 {abs(delta_days)} 天"
    if delta_days == 0:
        return "今天到期"
    if delta_days == 1:
        return "明天到期"
    return f"{delta_days} 天后到期"


def days_ago_iso(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def read_jsonl_tail(path: Path, limit: int = 10) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit * 3 :]
    items: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(items) >= limit:
            break
    return items
