from __future__ import annotations

import time
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from . import config
from .text import append_jsonl, new_id, now_iso


def _connection_kwargs() -> dict[str, Any]:
    if config.SEEKDB_MODE == "embedded":
        try:
            import pylibseekdb
        except ImportError as error:  # pragma: no cover - platform dependent
            raise RuntimeError("SEEKDB_MODE=embedded 需要安装 pylibseekdb") from error
        instance = pylibseekdb.open(str(config.SEEKDB_DATA_DIR))
        return instance.connection_options()

    return {
        "host": config.SEEKDB_HOST,
        "port": config.SEEKDB_PORT,
        "user": config.SEEKDB_USER,
        "password": config.SEEKDB_PASSWORD,
        "charset": "utf8mb4",
        "connect_timeout": 10,
    }


def connect(with_database: bool = True) -> pymysql.connections.Connection:
    kwargs = _connection_kwargs()
    if with_database:
        kwargs["database"] = config.SEEKDB_DATABASE
    try:
        return pymysql.connect(autocommit=True, cursorclass=DictCursor, **kwargs)
    except pymysql.MySQLError as error:
        raise RuntimeError(
            "无法连接 seekdb。请先按 README 启动 seekdb 容器，并确认 "
            f"{config.SEEKDB_HOST}:{config.SEEKDB_PORT} 可访问。原始错误：{error}"
        ) from error


def ensure_database() -> None:
    if config.SEEKDB_MODE == "embedded":
        return
    kwargs = _connection_kwargs()
    with pymysql.connect(autocommit=True, cursorclass=DictCursor, **kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{config.SEEKDB_DATABASE}`")


def _log_query(purpose: str, sql: str, params: list[Any], rows: list[dict], elapsed_ms: float) -> dict:
    meta = {
        "query_id": new_id("q_"),
        "time": now_iso(),
        "purpose": purpose,
        "sql": " ".join(sql.split()),
        "params": params,
        "row_count": len(rows),
        "ids": [row.get("id") for row in rows],
        "elapsed_ms": round(elapsed_ms, 3),
        "seekdb_mode": config.SEEKDB_MODE,
        "database": config.SEEKDB_DATABASE,
    }
    append_jsonl(config.RUNS_DIR / "db_queries.jsonl", meta)
    return meta


def init_schema() -> dict:
    ensure_database()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS sdd_materials (
            id VARCHAR(64) PRIMARY KEY,
            kind VARCHAR(16) NOT NULL,
            path VARCHAR(512) NOT NULL,
            symbol VARCHAR(256) NULL,
            content MEDIUMTEXT NOT NULL,
            keywords VARCHAR(1024) NOT NULL,
            content_hash CHAR(64) NOT NULL,
            updated_at DATETIME NOT NULL,
            KEY idx_sdd_kind (kind),
            KEY idx_sdd_path (path)
        ) DEFAULT CHARSET = utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS sdd_context_packs (
            id VARCHAR(64) PRIMARY KEY,
            task_id VARCHAR(128) NOT NULL,
            pack_json MEDIUMTEXT NOT NULL,
            created_at DATETIME NOT NULL,
            KEY idx_sdd_task (task_id)
        ) DEFAULT CHARSET = utf8mb4
        """,
    ]
    started = time.perf_counter()
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            cursor.execute("SELECT VERSION() AS version")
            version = cursor.fetchone() or {}
    elapsed = (time.perf_counter() - started) * 1000
    meta = _log_query("init_schema", "CREATE TABLE IF NOT EXISTS ...", [], [], elapsed)
    return {"version": version.get("version"), "query_id": meta["query_id"]}


def replace_materials(chunks: list[dict]) -> dict:
    started = time.perf_counter()
    sql = (
        "REPLACE INTO sdd_materials "
        "(id, kind, path, symbol, content, keywords, content_hash, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())"
    )
    rows = [
        (
            chunk["id"],
            chunk["kind"],
            chunk["path"],
            chunk.get("symbol"),
            chunk["content"],
            chunk["keywords"],
            chunk["content_hash"],
        )
        for chunk in chunks
    ]
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(sql, rows)
    elapsed = (time.perf_counter() - started) * 1000
    meta = _log_query(
        "replace_materials",
        sql,
        ["<chunk rows>"],
        [{"id": chunk["id"]} for chunk in chunks],
        elapsed,
    )
    return {"count": len(rows), "query_id": meta["query_id"], "elapsed_ms": meta["elapsed_ms"]}


def clear_materials() -> dict:
    started = time.perf_counter()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM sdd_materials")
    elapsed = (time.perf_counter() - started) * 1000
    meta = _log_query("clear_materials", "DELETE FROM sdd_materials", [], [], elapsed)
    return {"query_id": meta["query_id"], "elapsed_ms": meta["elapsed_ms"]}


def _like(term: str) -> str:
    return f"%{term.lower()}%"


def search_materials(terms: list[str], kinds: list[str], limit: int, purpose: str) -> tuple[list[dict], dict]:
    if not terms or not kinds:
        return [], {
            "query_id": None,
            "purpose": purpose,
            "row_count": 0,
            "elapsed_ms": 0.0,
            "sql": None,
            "params": [],
        }

    score_parts: list[str] = []
    score_params: list[str] = []
    where_parts: list[str] = []
    where_params: list[str] = []
    for term in terms:
        like = _like(term)
        score_parts.append(
            "(CASE WHEN LOWER(path) LIKE %s THEN 6 ELSE 0 END"
            " + CASE WHEN LOWER(COALESCE(symbol, '')) LIKE %s THEN 6 ELSE 0 END"
            " + CASE WHEN LOWER(keywords) LIKE %s THEN 3 ELSE 0 END"
            " + CASE WHEN LOWER(content) LIKE %s THEN 1 ELSE 0 END)"
        )
        score_params.extend([like, like, like, like])
        where_parts.append(
            "(LOWER(content) LIKE %s OR LOWER(keywords) LIKE %s"
            " OR LOWER(path) LIKE %s OR LOWER(COALESCE(symbol, '')) LIKE %s)"
        )
        where_params.extend([like, like, like, like])

    kind_placeholders = ", ".join(["%s"] * len(kinds))
    sql = (
        "SELECT id, kind, path, symbol, content, keywords, "
        f"({' + '.join(score_parts)}) AS score "
        "FROM sdd_materials "
        f"WHERE kind IN ({kind_placeholders}) AND ({' OR '.join(where_parts)}) "
        "ORDER BY score DESC, path ASC, id ASC LIMIT %s"
    )
    params: list[Any] = [*score_params, *kinds, *where_params, limit]
    started = time.perf_counter()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = list(cursor.fetchall())
    elapsed = (time.perf_counter() - started) * 1000
    meta = _log_query(purpose, sql, params, rows, elapsed)
    return rows, meta


def get_by_symbols(symbols: list[str], kinds: list[str] | None = None) -> list[dict]:
    if not symbols:
        return []
    placeholders = ", ".join(["%s"] * len(symbols))
    sql = f"SELECT id, kind, path, symbol, content, keywords FROM sdd_materials WHERE symbol IN ({placeholders})"
    params: list[Any] = list(symbols)
    if kinds:
        kind_placeholders = ", ".join(["%s"] * len(kinds))
        sql += f" AND kind IN ({kind_placeholders})"
        params.extend(kinds)
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def get_by_paths(paths: list[str], symbol: str | None = None, kinds: list[str] | None = None) -> list[dict]:
    if not paths:
        return []
    placeholders = ", ".join(["%s"] * len(paths))
    sql = f"SELECT id, kind, path, symbol, content, keywords FROM sdd_materials WHERE path IN ({placeholders})"
    params: list[Any] = list(paths)
    if symbol is not None:
        sql += " AND symbol = %s"
        params.append(symbol)
    if kinds:
        kind_placeholders = ", ".join(["%s"] * len(kinds))
        sql += f" AND kind IN ({kind_placeholders})"
        params.extend(kinds)
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def count_by_kind() -> dict[str, int]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT kind, COUNT(*) AS count FROM sdd_materials GROUP BY kind")
            rows = list(cursor.fetchall())
    return {row["kind"]: row["count"] for row in rows}


def save_context_pack(task_id: str, pack_id: str, pack_json: str) -> None:
    sql = (
        "REPLACE INTO sdd_context_packs (id, task_id, pack_json, created_at) "
        "VALUES (%s, %s, %s, NOW())"
    )
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (pack_id, task_id, pack_json))


def check() -> dict:
    started = time.perf_counter()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            version = (cursor.fetchone() or {}).get("version")
            cursor.execute("SELECT COUNT(*) AS count FROM sdd_materials")
            count = (cursor.fetchone() or {}).get("count", 0)
            cursor.execute("SELECT id, kind, path, symbol FROM sdd_materials ORDER BY id LIMIT 5")
            sample = list(cursor.fetchall())
    elapsed = (time.perf_counter() - started) * 1000
    meta = _log_query("check", "SELECT VERSION(); SELECT COUNT(*) ...", [], sample, elapsed)
    return {"version": version, "materials": count, "sample": sample, "query_id": meta["query_id"]}
