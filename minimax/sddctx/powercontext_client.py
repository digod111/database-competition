from __future__ import annotations

from typing import Any

import requests

from . import config


class PowerContextError(RuntimeError):
    pass


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{config.POWERCONTEXT_URL}{path}"
    try:
        response = requests.request(method, url, json=payload, timeout=30)
    except requests.RequestException as error:
        raise PowerContextError(
            f"无法访问 PowerContext {url}。请先启动本地 Server（见 README）。原始错误：{error}"
        ) from error
    if response.status_code >= 400:
        detail = response.text[:2000]
        raise PowerContextError(f"PowerContext {path} 返回 HTTP {response.status_code}: {detail}")
    if not response.content:
        return {}
    return response.json()


def health() -> bool:
    try:
        response = requests.get(f"{config.POWERCONTEXT_URL}/health/live", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def create_scope() -> str:
    payload = {
        "title": config.POWERCONTEXT_SCOPE_TITLE,
        "summary": "SDD 上下文模块的任务状态与验证记录",
        "idempotency_key": config.POWERCONTEXT_SCOPE_KEY,
    }
    result = _request("POST", "/v1/scopes", payload)
    scope_id = result.get("scope_id") or result.get("id")
    if not scope_id:
        raise PowerContextError(f"创建 Scope 的响应缺少 scope_id：{result}")
    return str(scope_id)


def remember(scope_id: str, kind: str, text: str, reason: str) -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/memory/remember",
        {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason},
    )


def search(scope_id: str, query: str, limit: int = 20) -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/memory/search",
        {"scope_id": scope_id, "query": query, "limit": limit, "mode": "fts"},
    )


def prepare_context(scope_id: str, query: str, max_bytes: int = 4000) -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/context/prepare",
        {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
    )
