from __future__ import annotations

from typing import Any

import requests

from . import config


class PowerContextError(RuntimeError):
    pass


def _request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    url = f"{config.POWERCONTEXT_URL}{path}"
    try:
        response = requests.request(method, url, json=payload, timeout=timeout)
    except requests.RequestException as error:
        raise PowerContextError(
            f"无法访问 PowerContext {url}。请先启动本地 Server（python -m wengu pc start）。原始错误：{error}"
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


def create_scope(title: str, summary: str, idempotency_key: str) -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/scopes",
        {"title": title, "summary": summary, "idempotency_key": idempotency_key},
    )


def remember(scope_id: str, kind: str, text: str, reason: str, description: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason}
    if description:
        payload["description"] = description
    return _request("POST", "/v1/memory/remember", payload)


def search(scope_id: str, query: str, limit: int = 20, mode: str = "fts") -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/memory/search",
        {"scope_id": scope_id, "query": query, "limit": limit, "mode": mode},
    )


def prepare_context(scope_id: str, query: str, max_bytes: int = 4000, include_receipt: bool = True) -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/context/prepare",
        {
            "scope_id": scope_id,
            "query": query,
            "max_bytes": max_bytes,
            "include_receipt": include_receipt,
        },
    )
