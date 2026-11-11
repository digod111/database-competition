from __future__ import annotations

import ast
import re
from pathlib import Path

from .text import digest, keywords_for

REQ_RE = re.compile(r"REQ-[A-Za-z]+-\d+")


def _make_chunk(kind: str, path: str, symbol: str, content: str) -> dict:
    content = content.strip("\n")
    identity = digest(f"{kind}|{path}|{symbol}")[:16]
    return {
        "id": identity,
        "kind": kind,
        "path": path,
        "symbol": symbol,
        "content": content,
        "keywords": keywords_for(content, path, symbol),
        "content_hash": digest(content),
    }


def _markdown_chunks(root: Path, path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(root).as_posix()
    lines = text.splitlines()
    heading_indexes = [index for index, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]

    sections: list[tuple[str, str]] = []
    intro = "\n".join(lines[: heading_indexes[0]]) if heading_indexes else text
    if intro.strip():
        sections.append(("overview", intro))
    for position, start in enumerate(heading_indexes):
        end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
        heading = lines[start].lstrip("#").strip()
        body = "\n".join(lines[start:end])
        sections.append((heading, body))

    chunks: list[dict] = []
    for heading, body in sections:
        requirement_ids = REQ_RE.findall(body)
        if not requirement_ids:
            chunks.append(_make_chunk("spec", relative, heading, body))
            continue

        matches = list(REQ_RE.finditer(body))
        prefix = body[: matches[0].start()]
        for position, match in enumerate(matches):
            end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
            block = body[match.start() : end]
            symbol = match.group(0)
            content = f"{prefix}{block}" if prefix.strip() else block
            chunks.append(_make_chunk("spec", relative, symbol, content))
    if not chunks:
        chunks.append(_make_chunk("spec", relative, "__file__", text))
    return chunks


def _line_span(source_lines: list[str], start_line: int, end_line: int) -> str:
    return "\n".join(source_lines[start_line - 1 : end_line])


def _node_start_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    start = node.lineno
    if decorators:
        start = min([start] + [decorator.lineno for decorator in decorators])
    return start


def _python_chunks(root: Path, path: Path, kind: str) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(root).as_posix()
    source_lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [_make_chunk(kind, relative, "__file__", text)]

    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    class_nodes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    chunks: list[dict] = []

    if kind == "test":
        first_test = next((node for node in functions if node.name.startswith("test_")), None)
        if first_test is None:
            return [_make_chunk("test", relative, "__file__", text)]
        header_end = _node_start_line(first_test) - 1
        header = "\n".join(source_lines[:header_end])
        if header.strip():
            chunks.append(_make_chunk("test", relative, "__header__", header))
        for node in functions:
            if not node.name.startswith("test_"):
                continue
            content = _line_span(source_lines, _node_start_line(node), node.end_lineno)
            chunks.append(_make_chunk("test", relative, node.name, content))
        return chunks

    module_parts: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
            module_parts.append(_line_span(source_lines, _node_start_line(node), node.end_lineno))
    module_content = "\n\n".join(module_parts)
    if module_content.strip():
        chunks.append(_make_chunk("code", relative, "__module__", module_content))

    for node in functions:
        content = _line_span(source_lines, _node_start_line(node), node.end_lineno)
        chunks.append(_make_chunk("code", relative, node.name, content))

    for class_node in class_nodes:
        class_start = _node_start_line(class_node)
        methods = [
            child
            for child in class_node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        header_end = _node_start_line(methods[0]) - 1 if methods else class_node.end_lineno
        class_header = _line_span(source_lines, class_start, header_end)
        if class_header.strip():
            chunks.append(_make_chunk("code", relative, class_node.name, class_header))
        for method in methods:
            content = _line_span(source_lines, _node_start_line(method), method.end_lineno)
            symbol = f"{class_node.name}.{method.name}"
            chunks.append(_make_chunk("code", relative, symbol, content))
    if not chunks:
        chunks.append(_make_chunk("code", relative, "__file__", text))
    return chunks


def collect_chunks(root: Path) -> list[dict]:
    root = root.resolve()
    chunks: list[dict] = []
    for path in sorted((root / "specs").rglob("*.md")):
        chunks.extend(_markdown_chunks(root, path))
    for path in sorted((root / "src").rglob("*.py")):
        chunks.extend(_python_chunks(root, path, "code"))
    for path in sorted((root / "tests").rglob("*.py")):
        chunks.extend(_python_chunks(root, path, "test"))
    return chunks
