from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests

from . import config, patch
from .contextpack import render_for_prompt
from .text import now_iso, write_json

SYSTEM_PROMPT = """你是规格驱动开发（SDD）工作流中的实现助手。
你会收到一个开发任务、适用的规格条目、相关代码和验证测试。所有资料都来自工程上下文模块。

只允许修改 src/taskboard/service.py。不得修改测试、规格文件、models.py 或 pytest.ini。

请只输出一个严格 JSON 对象，不要输出 Markdown 代码块或任何额外文字：
{"summary": "一句话说明改了什么", "edits": [{"path": "src/taskboard/service.py", "search": "当前文件中的精确片段", "replace": "替换后的完整片段"}]}

要求：
1. search 必须是当前文件中的精确子串（包含缩进），并且在文件中只出现一次。
2. replace 是替换后的完整片段，必须保留原有缩进风格。
3. 做最小修改，满足规格并保持兼容性。
4. 不要修改测试来让测试通过。
"""


class MiniMaxError(RuntimeError):
    pass


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        joined = "".join(parts)
        if joined.strip():
            return joined
    reasoning = message.get("reasoning_content")
    return reasoning if isinstance(reasoning, str) else ""


def extract_json(text: str) -> dict[str, Any]:
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(stripped[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise MiniMaxError(f"MiniMax 响应不是可解析的 JSON：{text[:500]}")


def call_minimax(task: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    if not config.MINIMAX_API_KEY:
        raise MiniMaxError(
            "未配置 MINIMAX_API_KEY。请在 minimax/.env 中填写 MiniMax Token Plan 的 API Key，"
            "或使用离线回放：python -m sddctx demo"
        )
    endpoint = f"{config.MINIMAX_BASE_URL}/chat/completions"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "以下是本次任务和上下文。请严格按 system 要求输出 JSON。\n\n"
                + render_for_prompt(pack)
            ),
        },
    ]
    payload = {
        "model": config.MINIMAX_MODEL,
        "messages": messages,
        "stream": False,
    }
    timestamp = now_iso().replace(":", "").replace("-", "")
    request_path = config.RUNS_DIR / f"minimax_request_{timestamp}.json"
    response_path = config.RUNS_DIR / f"minimax_response_{timestamp}.json"
    write_json(
        request_path,
        {
            "endpoint": endpoint,
            "model": config.MINIMAX_MODEL,
            "api_key_present": True,
            "task_id": task.get("id"),
            "context_pack_id": pack.get("pack_id"),
            "messages": messages,
        },
    )
    write_json(config.RUNS_DIR / "minimax_request.json", json.loads(request_path.read_text(encoding="utf-8")))

    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {config.MINIMAX_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.MINIMAX_TIMEOUT,
        )
    except requests.RequestException as error:
        raise MiniMaxError(f"调用 MiniMax 失败：{error}") from error

    try:
        body = response.json()
    except ValueError:
        body = {"raw_text": response.text}
    write_json(
        response_path,
        {"status_code": response.status_code, "body": body},
    )
    write_json(config.RUNS_DIR / "minimax_response.json", {"status_code": response.status_code, "body": body})
    if response.status_code >= 400:
        raise MiniMaxError(f"MiniMax 返回 HTTP {response.status_code}: {response.text[:1000]}")

    base_resp = body.get("base_resp") if isinstance(body, dict) else None
    if isinstance(base_resp, dict) and base_resp.get("status_code") not in (0, None):
        raise MiniMaxError(f"MiniMax 业务错误：{base_resp}")

    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise MiniMaxError(f"MiniMax 响应缺少 choices：{json.dumps(body, ensure_ascii=False)[:1000]}")
    content = _message_text(choices[0].get("message", {}))
    plan = extract_json(content)
    edits = plan.get("edits")
    if not isinstance(edits, list) or not edits:
        raise MiniMaxError(f"MiniMax 没有返回 edits：{plan}")
    for edit in edits:
        if not isinstance(edit, dict) or not all(key in edit for key in ("path", "search", "replace")):
            raise MiniMaxError(f"非法的 edit 条目：{edit}")
        normalized = edit["path"].replace("\\", "/").lstrip("/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        for prefix in ("workspace/taskboard/", "taskboard/"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
        if normalized != "src/taskboard/service.py":
            raise MiniMaxError(f"MiniMax 试图修改未授权文件：{edit['path']}")
    return {
        "plan": plan,
        "edits": edits,
        "content": content,
        "usage": body.get("usage"),
        "request_path": str(request_path),
        "response_path": str(response_path),
        "response_body": body,
    }


def _save_plan(task: dict[str, Any], pack: dict[str, Any], result: dict[str, Any], mode: str) -> None:
    write_json(
        config.ARTIFACTS_DIR / "minimax_edits.json",
        {
            "schema": "sdd-minimax-edits.v1",
            "task_id": task.get("id"),
            "context_pack_id": pack.get("pack_id"),
            "mode": mode,
            "created_at": now_iso(),
            "model": config.MINIMAX_MODEL,
            "summary": result["plan"].get("summary", ""),
            "edits": result["edits"],
            "request_path": result.get("request_path"),
            "response_path": result.get("response_path"),
        },
    )


def _append_record(
    task: dict[str, Any],
    pack: dict[str, Any],
    result: dict[str, Any],
    changed_files: list[str],
    mode: str,
) -> None:
    record = {
        "time": now_iso(),
        "mode": mode,
        "task_id": task.get("id"),
        "task_title": task.get("title"),
        "context_pack_id": pack.get("pack_id"),
        "context_sources": {
            "specs": [item.get("symbol") for item in pack.get("specs", [])],
            "code": [item.get("symbol") for item in pack.get("code", [])],
            "tests": [item.get("symbol") for item in pack.get("tests", [])],
        },
        "model": config.MINIMAX_MODEL,
        "endpoint": f"{config.MINIMAX_BASE_URL}/chat/completions",
        "request": result.get("request_path"),
        "response": result.get("response_path"),
        "summary": result["plan"].get("summary", ""),
        "edits": result["edits"],
        "changed_files": changed_files,
    }
    write_json(config.RUNS_DIR / "minimax_call.json", record)
    lines = [
        "",
        f"## {record['time']}  {record['task_id']}  {record['task_title']}",
        "",
        f"- 模式：{mode}",
        f"- 上下文包：{record['context_pack_id']}",
        f"- 规格来源：{', '.join(record['context_sources']['specs'])}",
        f"- 代码来源：{', '.join(record['context_sources']['code'])}",
        f"- 测试来源：{', '.join(record['context_sources']['tests'])}",
        f"- 模型：{record['model']}",
        f"- 请求记录：{record['request']}",
        f"- 响应记录：{record['response']}",
        f"- 修改摘要：{record['summary']}",
        f"- 修改文件：{', '.join(changed_files)}",
        "",
        "应用的最小编辑：",
        "```json",
        json.dumps(record["edits"], ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    path = config.DOCS_DIR / "minimax_dev_record.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def modify_with_minimax(task: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    result = call_minimax(task, pack)
    _save_plan(task, pack, result, mode="live")
    changed_files = patch.apply_edits(config.WORKSPACE_DIR, result["edits"])
    _append_record(task, pack, result, changed_files, mode="live")
    return {"plan": result["plan"], "changed_files": changed_files, "mode": "live"}


def apply_recorded(task: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    path = config.ARTIFACTS_DIR / "minimax_edits.json"
    if not path.exists():
        raise MiniMaxError(
            "没有找到 artifacts/minimax_edits.json。请先用 --live-minimax 真实调用一次 MiniMax。"
        )
    recorded = json.loads(path.read_text(encoding="utf-8"))
    edits = recorded.get("edits")
    if not isinstance(edits, list) or not edits:
        raise MiniMaxError("artifacts/minimax_edits.json 中没有可用 edits")
    result = {
        "plan": {"summary": recorded.get("summary", "recorded patch")},
        "edits": edits,
        "request_path": recorded.get("request_path"),
        "response_path": recorded.get("response_path"),
    }
    changed_files = patch.apply_edits(config.WORKSPACE_DIR, edits)
    _append_record(task, pack, result, changed_files, mode="replay")
    return {"plan": result["plan"], "changed_files": changed_files, "mode": "replay"}
