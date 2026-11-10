from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from . import config
from .text import (
    append_jsonl,
    chunk_markdown,
    excerpt,
    meta_list,
    new_id,
    now_dt,
    now_iso,
    parse_frontmatter,
    sha256_text,
    to_db_time,
)


class SeekDBError(RuntimeError):
    pass


def _connection_kwargs(with_database: bool = True) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": config.SEEKDB_HOST,
        "port": config.SEEKDB_PORT,
        "user": config.SEEKDB_USER,
        "password": config.SEEKDB_PASSWORD,
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "autocommit": True,
        "cursorclass": DictCursor,
    }
    if with_database:
        kwargs["database"] = config.SEEKDB_DATABASE
    return kwargs


def connect(with_database: bool = True) -> pymysql.connections.Connection:
    try:
        return pymysql.connect(**_connection_kwargs(with_database))
    except pymysql.MySQLError as error:
        raise SeekDBError(
            "无法连接 seekdb。请确认 Docker 容器已启动，并检查 "
            f"{config.SEEKDB_HOST}:{config.SEEKDB_PORT}。原始错误：{error}"
        ) from error


def ensure_database() -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", config.SEEKDB_DATABASE):
        raise SeekDBError(f"数据库名不合法：{config.SEEKDB_DATABASE}")
    with connect(with_database=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{config.SEEKDB_DATABASE}` "
                "DEFAULT CHARACTER SET utf8mb4"
            )


def _sanitize_params(params: Any, limit: int = 500) -> Any:
    if params is None:
        return []
    if isinstance(params, (list, tuple)):
        result = []
        for value in params:
            text = str(value)
            result.append(text[:limit] + ("…" if len(text) > limit else ""))
        return result
    text = str(params)
    return text[:limit] + ("…" if len(text) > limit else "")


def _run(
    purpose: str,
    sql: str,
    params: Any = None,
    fetch: str = "all",
    log_params: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    affected = 0
    with connect() as connection:
        with connection.cursor() as cursor:
            affected = cursor.execute(sql, params or ())
            if fetch == "all":
                rows = list(cursor.fetchall())
            elif fetch == "one":
                row = cursor.fetchone()
                rows = [row] if row else []
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    meta = {
        "query_id": new_id("q_"),
        "time": now_iso(),
        "purpose": purpose,
        "sql": " ".join(sql.split()),
        "params": _sanitize_params(params if log_params is None else log_params),
        "row_count": len(rows) if fetch != "none" else affected,
        "affected_rows": affected,
        "ids": [row.get("id") for row in rows[:10] if row.get("id")],
        "scores": [round(float(row["score"]), 6) for row in rows[:5] if row.get("score") is not None],
        "elapsed_ms": elapsed_ms,
        "database": config.SEEKDB_DATABASE,
    }
    append_jsonl(config.RUNS_DIR / "db_queries.jsonl", meta)
    return rows, meta


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS materials (
        id VARCHAR(64) PRIMARY KEY,
        course VARCHAR(64) NOT NULL,
        chapter VARCHAR(128) NOT NULL,
        title VARCHAR(256) NOT NULL,
        heading VARCHAR(256) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        keywords VARCHAR(1024) NOT NULL DEFAULT '',
        knowledge_points VARCHAR(1024) NOT NULL DEFAULT '',
        source_path VARCHAR(512) NOT NULL,
        search_text MEDIUMTEXT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        created_at DATETIME NOT NULL,
        FULLTEXT INDEX ft_material_search (search_text) WITH PARSER ngram,
        KEY idx_material_course (course, chapter)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS questions (
        id VARCHAR(64) PRIMARY KEY,
        course VARCHAR(64) NOT NULL,
        chapter VARCHAR(128) NOT NULL,
        knowledge_point VARCHAR(128) NOT NULL,
        type VARCHAR(16) NOT NULL,
        difficulty INT NOT NULL DEFAULT 2,
        stem TEXT NOT NULL,
        options_json MEDIUMTEXT NOT NULL,
        answer_json MEDIUMTEXT NOT NULL,
        explanation MEDIUMTEXT NOT NULL,
        source_material_id VARCHAR(64) NULL,
        source_path VARCHAR(512) NOT NULL DEFAULT '',
        source_heading VARCHAR(256) NOT NULL DEFAULT '',
        tags VARCHAR(512) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        KEY idx_question_course (course),
        KEY idx_question_kp (knowledge_point)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id VARCHAR(64) PRIMARY KEY,
        display_name VARCHAR(64) NOT NULL,
        created_at DATETIME NOT NULL,
        last_seen_at DATETIME NOT NULL
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS question_state (
        user_id VARCHAR(64) NOT NULL,
        question_id VARCHAR(64) NOT NULL,
        mastery DOUBLE NOT NULL DEFAULT 0,
        ease DOUBLE NOT NULL DEFAULT 2.5,
        interval_days DOUBLE NOT NULL DEFAULT 0,
        due_at DATETIME NULL,
        correct_count INT NOT NULL DEFAULT 0,
        wrong_count INT NOT NULL DEFAULT 0,
        streak INT NOT NULL DEFAULT 0,
        last_result VARCHAR(16) NOT NULL DEFAULT '',
        last_reviewed_at DATETIME NULL,
        PRIMARY KEY (user_id, question_id),
        KEY idx_state_due (user_id, due_at)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        started_at DATETIME NOT NULL,
        ended_at DATETIME NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        planned_count INT NOT NULL DEFAULT 0,
        answered_count INT NOT NULL DEFAULT 0,
        correct_count INT NOT NULL DEFAULT 0,
        plan_json MEDIUMTEXT NULL,
        memory_recall_json MEDIUMTEXT NULL,
        influences_json MEDIUMTEXT NULL,
        KEY idx_session_user (user_id, started_at)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS attempts (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        question_id VARCHAR(64) NOT NULL,
        session_id VARCHAR(64) NOT NULL,
        answer MEDIUMTEXT NOT NULL,
        correct TINYINT NOT NULL,
        quality INT NOT NULL,
        elapsed_ms INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        KEY idx_attempt_user (user_id, created_at),
        KEY idx_attempt_session (session_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        event VARCHAR(32) NOT NULL,
        detail_json MEDIUMTEXT NOT NULL,
        created_at DATETIME NOT NULL,
        KEY idx_usage_time (created_at),
        KEY idx_usage_user (user_id, created_at)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS session_summaries (
        session_id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        summary_json MEDIUMTEXT NOT NULL,
        created_at DATETIME NOT NULL,
        KEY idx_summary_user (user_id, created_at)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        display_name VARCHAR(64) NOT NULL,
        rating INT NOT NULL,
        comment TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        KEY idx_feedback_time (created_at)
    ) DEFAULT CHARSET = utf8mb4
    """,
]


def init_schema() -> dict[str, Any]:
    ensure_database()
    started = time.perf_counter()
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)
            cursor.execute("SELECT VERSION() AS version")
            version = (cursor.fetchone() or {}).get("version")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    meta = {
        "query_id": new_id("q_"),
        "time": now_iso(),
        "purpose": "init_schema",
        "sql": "CREATE TABLE IF NOT EXISTS ... (8 tables)",
        "params": [],
        "row_count": len(SCHEMA_STATEMENTS),
        "affected_rows": 0,
        "ids": [],
        "scores": [],
        "elapsed_ms": elapsed_ms,
        "database": config.SEEKDB_DATABASE,
        "version": version,
    }
    append_jsonl(config.RUNS_DIR / "db_queries.jsonl", meta)
    return {"version": version, "query_id": meta["query_id"]}


def _course_files(seed_dir: Path) -> list[Path]:
    return sorted(path for path in seed_dir.joinpath("notes").rglob("*.md") if path.is_file())


def build_material_rows(seed_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _course_files(seed_dir):
        relative = path.relative_to(seed_dir).as_posix()
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if not meta.get("course") or not meta.get("title"):
            raise ValueError(f"笔记缺少 course/title 元数据：{relative}")
        sections = chunk_markdown(body)
        if not sections:
            raise ValueError(f"笔记没有可导入的正文：{relative}")
        keywords = meta.get("keywords", "")
        knowledge_points = meta.get("knowledge_points", "")
        for section in sections:
            heading = section["heading"]
            title = meta["title"] if heading == "概述" else f"{meta['title']} / {heading}"
            content = section["content"]
            search_text = "\n".join(
                [title, heading, keywords, knowledge_points, content]
            )
            material_id = f"mat_{sha256_text(relative + '#' + heading)[:12]}"
            rows.append(
                {
                    "id": material_id,
                    "course": meta["course"].strip(),
                    "chapter": meta.get("chapter", "").strip(),
                    "title": title,
                    "heading": heading,
                    "content": content,
                    "keywords": keywords,
                    "knowledge_points": knowledge_points,
                    "source_path": relative,
                    "search_text": search_text,
                    "content_hash": sha256_text(content),
                    "created_at": to_db_time(now_dt()),
                }
            )
    if not rows:
        raise ValueError(f"种子目录中没有找到笔记：{seed_dir}")
    return rows


def _validate_questions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("questions", [])
    if not isinstance(payload, list) or not payload:
        raise ValueError("questions.json 必须包含非空数组")
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        required = ("id", "course", "chapter", "knowledge_point", "type", "stem", "explanation")
        missing = [key for key in required if not str(item.get(key, "")).strip()]
        if missing:
            raise ValueError(f"第 {index + 1} 道题缺少字段：{', '.join(missing)}")
        if item["id"] in seen:
            raise ValueError(f"题目 ID 重复：{item['id']}")
        seen.add(item["id"])
        qtype = item["type"]
        options = item.get("options", [])
        answer = item.get("answer")
        if qtype == "mcq":
            if not isinstance(options, list) or len(options) < 2:
                raise ValueError(f"{item['id']} 的选择题至少需要两个选项")
            if not isinstance(answer, int) or not 0 <= answer < len(options):
                raise ValueError(f"{item['id']} 的 answer 必须是合法选项下标")
        elif qtype == "short":
            if not str(answer or "").strip():
                raise ValueError(f"{item['id']} 的简答题缺少参考答案")
            options = []
        else:
            raise ValueError(f"{item['id']} 的题型不支持：{qtype}")
        questions.append(
            {
                "id": item["id"],
                "course": item["course"],
                "chapter": item["chapter"],
                "knowledge_point": item["knowledge_point"],
                "type": qtype,
                "difficulty": int(item.get("difficulty", 2)),
                "stem": item["stem"],
                "options_json": json.dumps(options, ensure_ascii=False),
                "answer_json": json.dumps(answer, ensure_ascii=False),
                "explanation": item["explanation"],
                "source_path": item.get("source_path", ""),
                "source_heading": item.get("source_heading", ""),
                "tags": ",".join(item.get("tags", [])) if isinstance(item.get("tags"), list) else str(item.get("tags", "")),
                "created_at": to_db_time(now_dt()),
            }
        )
    return questions


def import_seed(seed_dir: Path | None = None, replace_all: bool = False) -> dict[str, Any]:
    seed_dir = (seed_dir or config.SEED_DIR).resolve()
    materials = build_material_rows(seed_dir)
    question_payload = json.loads((seed_dir / "questions.json").read_text(encoding="utf-8"))
    questions = _validate_questions(question_payload)

    material_ids = {(row["source_path"], row["heading"]): row["id"] for row in materials}
    for question in questions:
        question["source_material_id"] = material_ids.get(
            (question["source_path"], question["source_heading"])
        )

    if replace_all:
        _run("clear_attempts", "DELETE FROM attempts", fetch="none", log_params=[])
        _run("clear_sessions", "DELETE FROM sessions", fetch="none", log_params=[])
        _run("clear_question_state", "DELETE FROM question_state", fetch="none", log_params=[])
        _run("clear_usage", "DELETE FROM usage_events", fetch="none", log_params=[])
        _run("clear_feedback", "DELETE FROM feedback", fetch="none", log_params=[])
        _run("clear_session_summaries", "DELETE FROM session_summaries", fetch="none", log_params=[])
        _run("clear_questions", "DELETE FROM questions", fetch="none", log_params=[])
        _run("clear_materials", "DELETE FROM materials", fetch="none", log_params=[])

    started = time.perf_counter()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                REPLACE INTO materials
                (id, course, chapter, title, heading, content, keywords, knowledge_points,
                 source_path, search_text, content_hash, created_at)
                VALUES (%(id)s, %(course)s, %(chapter)s, %(title)s, %(heading)s, %(content)s,
                        %(keywords)s, %(knowledge_points)s, %(source_path)s, %(search_text)s,
                        %(content_hash)s, %(created_at)s)
                """,
                materials,
            )
            cursor.executemany(
                """
                REPLACE INTO questions
                (id, course, chapter, knowledge_point, type, difficulty, stem, options_json,
                 answer_json, explanation, source_material_id, source_path, source_heading,
                 tags, created_at)
                VALUES (%(id)s, %(course)s, %(chapter)s, %(knowledge_point)s, %(type)s, %(difficulty)s,
                        %(stem)s, %(options_json)s, %(answer_json)s, %(explanation)s,
                        %(source_material_id)s, %(source_path)s, %(source_heading)s, %(tags)s,
                        %(created_at)s)
                """,
                questions,
            )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    meta = {
        "query_id": new_id("q_"),
        "time": now_iso(),
        "purpose": "import_seed",
        "sql": "REPLACE INTO materials ...; REPLACE INTO questions ...",
        "params": [str(seed_dir)],
        "row_count": len(materials) + len(questions),
        "affected_rows": len(materials) + len(questions),
        "ids": [row["id"] for row in materials[:5]] + [row["id"] for row in questions[:5]],
        "scores": [],
        "elapsed_ms": elapsed_ms,
        "database": config.SEEKDB_DATABASE,
        "materials": len(materials),
        "questions": len(questions),
        "unresolved_sources": [
            question["id"] for question in questions if not question["source_material_id"]
        ],
    }
    append_jsonl(config.RUNS_DIR / "db_queries.jsonl", meta)
    return {
        "materials": len(materials),
        "questions": len(questions),
        "unresolved_sources": meta["unresolved_sources"],
        "query_id": meta["query_id"],
    }


def _row_question(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["options"] = json.loads(result.pop("options_json") or "[]")
    result["answer"] = json.loads(result.pop("answer_json") or "null")
    result["tags"] = meta_list(result.get("tags"))
    return result


def get_question(question_id: str) -> dict[str, Any] | None:
    rows, _ = _run("get_question", "SELECT * FROM questions WHERE id = %s", [question_id], fetch="one")
    return _row_question(rows[0]) if rows else None


def list_questions(course: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    sql = "SELECT * FROM questions"
    params: list[Any] = []
    if course:
        sql += " WHERE course = %s"
        params.append(course)
    sql += " ORDER BY course, chapter, id LIMIT %s"
    params.append(limit)
    rows, _ = _run("list_questions", sql, params)
    return [_row_question(row) for row in rows]


def get_material(material_id: str) -> dict[str, Any] | None:
    rows, _ = _run("get_material", "SELECT * FROM materials WHERE id = %s", [material_id], fetch="one")
    return rows[0] if rows else None


def list_materials(course: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT id, course, chapter, title, heading, content, keywords, knowledge_points, source_path FROM materials"
    params: list[Any] = []
    if course:
        sql += " WHERE course = %s"
        params.append(course)
    sql += " ORDER BY course, chapter, title LIMIT %s"
    params.append(limit)
    rows, _ = _run("list_materials", sql, params)
    return rows


def search_materials(
    query: str,
    course: str | None = None,
    chapter: str | None = None,
    knowledge_point: str | None = None,
    limit: int = 8,
    purpose: str = "material_search",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        sql = (
            "SELECT id, course, chapter, title, heading, content, keywords, knowledge_points, "
            "source_path, 0 AS score FROM materials WHERE 1 = 1"
        )
        params: list[Any] = []
        if course:
            sql += " AND course = %s"
            params.append(course)
        if chapter:
            sql += " AND chapter = %s"
            params.append(chapter)
        if knowledge_point:
            sql += " AND knowledge_points LIKE %s"
            params.append(f"%{knowledge_point}%")
        sql += " ORDER BY course, title LIMIT %s"
        params.append(limit)
        return _run(purpose, sql, params, log_params=[course, chapter, knowledge_point, limit])

    sql = (
        "SELECT id, course, chapter, title, heading, content, keywords, knowledge_points, source_path, "
        "MATCH(search_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score "
        "FROM materials WHERE MATCH(search_text) AGAINST (%s IN NATURAL LANGUAGE MODE)"
    )
    params = [query, query]
    if course:
        sql += " AND course = %s"
        params.append(course)
    if chapter:
        sql += " AND chapter = %s"
        params.append(chapter)
    if knowledge_point:
        sql += " AND knowledge_points LIKE %s"
        params.append(f"%{knowledge_point}%")
    sql += " ORDER BY score DESC, course, title LIMIT %s"
    params.append(limit)
    rows, meta = _run(purpose, sql, params)
    if rows:
        return rows, meta
    return search_materials_like(
        query, course=course, chapter=chapter, knowledge_point=knowledge_point,
        limit=limit, purpose=f"{purpose}_like_fallback",
    )


def search_materials_like(
    query: str,
    course: str | None = None,
    chapter: str | None = None,
    knowledge_point: str | None = None,
    limit: int = 8,
    purpose: str = "material_search_like",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    if not terms:
        return [], {"query_id": None, "purpose": purpose, "row_count": 0, "elapsed_ms": 0}
    score_parts = []
    score_params: list[Any] = []
    where_parts = []
    where_params: list[Any] = []
    for term in terms:
        like = f"%{term}%"
        score_parts.append(
            "(CASE WHEN title LIKE %s THEN 6 ELSE 0 END + CASE WHEN keywords LIKE %s THEN 3 ELSE 0 END "
            "+ CASE WHEN content LIKE %s THEN 1 ELSE 0 END)"
        )
        score_params.extend([like, like, like])
        where_parts.append("(title LIKE %s OR keywords LIKE %s OR content LIKE %s)")
        where_params.extend([like, like, like])
    sql = (
        "SELECT id, course, chapter, title, heading, content, keywords, knowledge_points, source_path, "
        f"({' + '.join(score_parts)}) AS score FROM materials WHERE ({' OR '.join(where_parts)})"
    )
    params: list[Any] = [*score_params, *where_params]
    if course:
        sql += " AND course = %s"
        params.append(course)
    if chapter:
        sql += " AND chapter = %s"
        params.append(chapter)
    if knowledge_point:
        sql += " AND knowledge_points LIKE %s"
        params.append(f"%{knowledge_point}%")
    sql += " ORDER BY score DESC, course, title LIMIT %s"
    params.append(limit)
    return _run(purpose, sql, params)


def search_for_question(question: dict[str, Any], limit: int = 2) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = f"{question['knowledge_point']} {excerpt(question['stem'], 50)}"
    rows, meta = search_materials(
        query, course=question["course"], knowledge_point=question["knowledge_point"],
        limit=limit, purpose="explanation_evidence",
    )
    if rows:
        return rows, meta
    return search_materials(
        question["knowledge_point"], course=question["course"], limit=limit,
        purpose="explanation_evidence_kp_fallback",
    )


def ensure_user(user_id: str, display_name: str | None = None) -> dict[str, Any]:
    user_id = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "", user_id)[:64] or config.WENGU_DEFAULT_USER
    existing = list_user(user_id)
    now = to_db_time(now_dt())
    name = (display_name or (existing or {}).get("display_name") or user_id)[:64]
    if existing:
        _run(
            "touch_user", "UPDATE users SET display_name = %s, last_seen_at = %s WHERE id = %s",
            [name, now, user_id], fetch="none", log_params=[user_id],
        )
        return {**existing, "display_name": name, "last_seen_at": now}
    _run(
        "create_user",
        "INSERT INTO users (id, display_name, created_at, last_seen_at) VALUES (%s, %s, %s, %s)",
        [user_id, name, now, now], fetch="none", log_params=[user_id],
    )
    return {"id": user_id, "display_name": name, "created_at": now, "last_seen_at": now}


def list_user(user_id: str) -> dict[str, Any] | None:
    rows, _ = _run("get_user", "SELECT * FROM users WHERE id = %s", [user_id], fetch="one", log_params=[user_id])
    return rows[0] if rows else None


def list_users(limit: int = 100) -> list[dict[str, Any]]:
    rows, _ = _run("list_users", "SELECT * FROM users ORDER BY last_seen_at DESC LIMIT %s", [limit])
    return rows


def get_state(user_id: str, question_id: str) -> dict[str, Any] | None:
    rows, _ = _run(
        "get_question_state",
        "SELECT * FROM question_state WHERE user_id = %s AND question_id = %s",
        [user_id, question_id], fetch="one", log_params=[user_id, question_id],
    )
    return rows[0] if rows else None


def list_states(user_id: str) -> list[dict[str, Any]]:
    rows, _ = _run(
        "list_question_states",
        "SELECT * FROM question_state WHERE user_id = %s ORDER BY due_at, question_id",
        [user_id], log_params=[user_id],
    )
    return rows


def list_due_questions(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    now = to_db_time(now_dt())
    rows, _ = _run(
        "due_questions",
        "SELECT q.*, s.mastery, s.due_at, s.correct_count, s.wrong_count, s.streak "
        "FROM question_state s JOIN questions q ON q.id = s.question_id "
        "WHERE s.user_id = %s AND s.due_at IS NOT NULL AND s.due_at <= %s "
        "ORDER BY s.mastery ASC, s.due_at ASC LIMIT %s",
        [user_id, now, limit], log_params=[user_id, now, limit],
    )
    return [_row_question(row) for row in rows]


def list_weak_questions(user_id: str, limit: int = 20, threshold: float = 0.6) -> list[dict[str, Any]]:
    rows, _ = _run(
        "weak_questions",
        "SELECT q.*, s.mastery, s.due_at, s.correct_count, s.wrong_count, s.streak "
        "FROM question_state s JOIN questions q ON q.id = s.question_id "
        "WHERE s.user_id = %s AND s.mastery < %s "
        "ORDER BY s.mastery ASC, s.wrong_count DESC LIMIT %s",
        [user_id, threshold, limit], log_params=[user_id, threshold, limit],
    )
    return [_row_question(row) for row in rows]


def list_new_questions(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows, _ = _run(
        "new_questions",
        "SELECT q.* FROM questions q LEFT JOIN question_state s "
        "ON s.question_id = q.id AND s.user_id = %s WHERE s.question_id IS NULL "
        "ORDER BY q.course, q.chapter, q.id LIMIT %s",
        [user_id, limit], log_params=[user_id, limit],
    )
    return [_row_question(row) for row in rows]


def upsert_state(
    user_id: str,
    question_id: str,
    mastery: float,
    ease: float,
    interval_days: float,
    due_at: str,
    correct_count: int,
    wrong_count: int,
    streak: int,
    last_result: str,
) -> dict[str, Any]:
    sql = (
        "REPLACE INTO question_state "
        "(user_id, question_id, mastery, ease, interval_days, due_at, correct_count, wrong_count, "
        "streak, last_result, last_reviewed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    params = [
        user_id, question_id, round(mastery, 4), round(ease, 3), round(interval_days, 3), due_at,
        correct_count, wrong_count, streak, last_result, to_db_time(now_dt()),
    ]
    _run("upsert_question_state", sql, params, fetch="none", log_params=[user_id, question_id, last_result])
    return {"user_id": user_id, "question_id": question_id, "due_at": due_at, "mastery": mastery}


def create_session(user_id: str, plan: dict[str, Any], recall: dict[str, Any]) -> str:
    session_id = new_id("ses_")
    _run(
        "create_session",
        "INSERT INTO sessions (id, user_id, started_at, status, planned_count, plan_json, memory_recall_json, influences_json) "
        "VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)",
        [
            session_id, user_id, to_db_time(now_dt()), len(plan.get("items", [])),
            json.dumps(plan, ensure_ascii=False),
            json.dumps(recall, ensure_ascii=False),
            json.dumps(plan.get("influences", []), ensure_ascii=False),
        ],
        fetch="none", log_params=[session_id, user_id, len(plan.get("items", []))],
    )
    return session_id


def get_session(session_id: str) -> dict[str, Any] | None:
    rows, _ = _run("get_session", "SELECT * FROM sessions WHERE id = %s", [session_id], fetch="one", log_params=[session_id])
    if not rows:
        return None
    row = rows[0]
    for key in ("plan_json", "memory_recall_json", "influences_json"):
        try:
            row[key.replace("_json", "")] = json.loads(row.get(key) or "null")
        except json.JSONDecodeError:
            row[key.replace("_json", "")] = None
    return row


def finish_session(session_id: str) -> None:
    totals, _ = _run(
        "session_totals",
        "SELECT COUNT(*) AS answered, COALESCE(SUM(correct), 0) AS correct FROM attempts WHERE session_id = %s",
        [session_id], fetch="one", log_params=[session_id],
    )
    stats = totals[0] if totals else {"answered": 0, "correct": 0}
    _run(
        "finish_session",
        "UPDATE sessions SET ended_at = %s, status = 'finished', answered_count = %s, correct_count = %s WHERE id = %s",
        [to_db_time(now_dt()), stats["answered"], stats["correct"], session_id],
        fetch="none", log_params=[session_id, stats["answered"], stats["correct"]],
    )


def record_attempt(
    user_id: str,
    question_id: str,
    session_id: str,
    answer: Any,
    correct: bool,
    quality: int,
    elapsed_ms: int = 0,
) -> str:
    attempt_id = new_id("att_")
    _run(
        "record_attempt",
        "INSERT INTO attempts (id, user_id, question_id, session_id, answer, correct, quality, elapsed_ms, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            attempt_id, user_id, question_id, session_id, json.dumps(answer, ensure_ascii=False),
            1 if correct else 0, quality, elapsed_ms, to_db_time(now_dt()),
        ],
        fetch="none", log_params=[attempt_id, user_id, question_id, session_id, correct, quality],
    )
    return attempt_id


def session_attempts(session_id: str) -> list[dict[str, Any]]:
    rows, _ = _run(
        "session_attempts",
        "SELECT a.*, q.course, q.chapter, q.knowledge_point, q.stem FROM attempts a JOIN questions q ON q.id = a.question_id "
        "WHERE a.session_id = %s ORDER BY a.created_at",
        [session_id], log_params=[session_id],
    )
    return rows


def usage_event(user_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
    _run(
        "usage_event",
        "INSERT INTO usage_events (id, user_id, event, detail_json, created_at) VALUES (%s, %s, %s, %s, %s)",
        [new_id("use_"), user_id, event, json.dumps(detail or {}, ensure_ascii=False), to_db_time(now_dt())],
        fetch="none", log_params=[user_id, event],
    )


def add_feedback(user_id: str, display_name: str, rating: int, comment: str) -> str:
    feedback_id = new_id("fb_")
    _run(
        "add_feedback",
        "INSERT INTO feedback (id, user_id, display_name, rating, comment, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        [feedback_id, user_id, display_name[:64], max(1, min(5, rating)), comment[:2000], to_db_time(now_dt())],
        fetch="none", log_params=[feedback_id, user_id, rating],
    )
    return feedback_id


def list_feedback(limit: int = 20) -> list[dict[str, Any]]:
    rows, _ = _run("list_feedback", "SELECT * FROM feedback ORDER BY created_at DESC LIMIT %s", [limit])
    return rows


def clear_user_data(user_id: str) -> None:
    for table, column in (
        ("attempts", "user_id"),
        ("sessions", "user_id"),
        ("session_summaries", "user_id"),
        ("question_state", "user_id"),
        ("usage_events", "user_id"),
        ("feedback", "user_id"),
    ):
        _run(f"clear_{table}", f"DELETE FROM {table} WHERE {column} = %s", [user_id], fetch="none", log_params=[user_id])


def clear_all_data() -> None:
    for table in ("attempts", "sessions", "session_summaries", "question_state", "usage_events", "feedback", "users"):
        _run(f"clear_{table}", f"DELETE FROM {table}", fetch="none", log_params=[])


def check() -> dict[str, Any]:
    rows, meta = _run("db_check", "SELECT VERSION() AS version", fetch="one")
    version = (rows[0] or {}).get("version") if rows else None
    counts = {}
    for table in ("materials", "questions", "users", "attempts", "sessions", "session_summaries", "usage_events", "feedback"):
        result, _ = _run(f"count_{table}", f"SELECT COUNT(*) AS count FROM {table}", fetch="one")
        counts[table] = (result[0] or {}).get("count", 0) if result else 0
    return {"version": version, "counts": counts, "query_id": meta["query_id"]}


def stats_overview(user_id: str | None = None, exclude_prefixes: tuple[str, ...] = ()) -> dict[str, Any]:
    def conditions(alias: str = "") -> tuple[list[str], list[Any]]:
        prefix = f"{alias}." if alias else ""
        conds: list[str] = []
        values: list[Any] = []
        if user_id:
            conds.append(f"{prefix}user_id = %s")
            values.append(user_id)
        for excluded in exclude_prefixes:
            conds.append(f"{prefix}user_id NOT LIKE %s")
            values.append(f"{excluded}%")
        return conds, values

    conds, params = conditions()
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    attempts, _ = _run(
        "stats_attempts",
        f"SELECT COUNT(*) AS total, COALESCE(SUM(correct), 0) AS correct FROM attempts{where}",
        params, fetch="one", log_params=params,
    )
    attempt_stats = attempts[0] if attempts else {"total": 0, "correct": 0}
    sessions, _ = _run(
        "stats_sessions",
        f"SELECT COUNT(*) AS total, COUNT(DISTINCT user_id) AS users FROM sessions{where}",
        params, fetch="one", log_params=params,
    )
    session_stats = sessions[0] if sessions else {"total": 0, "users": 0}
    days, _ = _run(
        "stats_active_days",
        f"SELECT COUNT(DISTINCT DATE(created_at)) AS days FROM attempts{where}",
        params, fetch="one", log_params=params,
    )
    active_days = (days[0] or {}).get("days", 0) if days else 0
    weak_conds, weak_params = conditions("a")
    weak_conds = ["a.correct = 0"] + weak_conds
    weak, _ = _run(
        "stats_weak_points",
        "SELECT q.knowledge_point, COUNT(*) AS wrong_count, COUNT(DISTINCT a.user_id) AS users "
        "FROM attempts a JOIN questions q ON q.id = a.question_id WHERE "
        + " AND ".join(weak_conds)
        + " GROUP BY q.knowledge_point ORDER BY wrong_count DESC, q.knowledge_point LIMIT 8",
        weak_params, log_params=[user_id or "all"],
    )
    recent_conds, recent_params = conditions()
    recent_conds = ["created_at >= DATE_SUB(%s, INTERVAL 6 DAY)"] + recent_conds
    recent, _ = _run(
        "stats_recent_days",
        "SELECT DATE(created_at) AS day, COUNT(*) AS attempts, COALESCE(SUM(correct), 0) AS correct "
        "FROM attempts WHERE " + " AND ".join(recent_conds)
        + " GROUP BY DATE(created_at) ORDER BY day",
        [to_db_time(now_dt())] + recent_params, log_params=[user_id or "all"],
    )
    total = int(attempt_stats.get("total", 0) or 0)
    correct = int(attempt_stats.get("correct", 0) or 0)
    return {
        "attempts": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0,
        "sessions": int(session_stats.get("total", 0) or 0),
        "users": int(session_stats.get("users", 0) or 0),
        "active_days": int(active_days or 0),
        "weak_points": weak,
        "recent_days": [
            {
                "day": str(row["day"]),
                "attempts": int(row["attempts"]),
                "correct": int(row["correct"]),
                "accuracy": round(int(row["correct"]) / int(row["attempts"]), 3) if row["attempts"] else 0,
            }
            for row in recent
        ],
    }


def user_progress(user_id: str) -> dict[str, Any]:
    rows, _ = _run(
        "user_progress",
        "SELECT COUNT(*) AS seen, COALESCE(AVG(mastery), 0) AS avg_mastery, "
        "SUM(CASE WHEN mastery < 0.6 THEN 1 ELSE 0 END) AS weak "
        "FROM question_state WHERE user_id = %s",
        [user_id], fetch="one", log_params=[user_id],
    )
    row = rows[0] if rows else {"seen": 0, "avg_mastery": 0, "weak": 0}
    total_questions, _ = _run("count_questions", "SELECT COUNT(*) AS count FROM questions", fetch="one")
    total = (total_questions[0] or {}).get("count", 0) if total_questions else 0
    return {
        "seen": row.get("seen", 0) or 0,
        "total": total,
        "avg_mastery": round(float(row.get("avg_mastery", 0) or 0), 3),
        "weak": row.get("weak", 0) or 0,
    }


def recent_queries(limit: int = 12) -> list[dict[str, Any]]:
    path = config.RUNS_DIR / "db_queries.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit * 3 :]
    items = []
    for line in reversed(lines):
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(items) >= limit:
            break
    return items


def session_has_attempt(session_id: str, question_id: str) -> bool:
    rows, _ = _run(
        "session_has_attempt",
        "SELECT id FROM attempts WHERE session_id = %s AND question_id = %s LIMIT 1",
        [session_id, question_id], fetch="one", log_params=[session_id, question_id],
    )
    return bool(rows)


def latest_session(user_id: str, status: str | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM sessions WHERE user_id = %s"
    params: list[Any] = [user_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY started_at DESC LIMIT 1"
    rows, _ = _run("latest_session", sql, params, fetch="one", log_params=[user_id, status])
    if not rows:
        return None
    row = rows[0]
    for key in ("plan_json", "memory_recall_json", "influences_json"):
        try:
            row[key.replace("_json", "")] = json.loads(row.get(key) or "null")
        except json.JSONDecodeError:
            row[key.replace("_json", "")] = None
    return row


def save_session_summary(session_id: str, user_id: str, summary: dict[str, Any]) -> None:
    _run(
        "save_session_summary",
        "REPLACE INTO session_summaries (session_id, user_id, summary_json, created_at) VALUES (%s, %s, %s, %s)",
        [session_id, user_id, json.dumps(summary, ensure_ascii=False), to_db_time(now_dt())],
        fetch="none", log_params=[session_id, user_id],
    )


def get_session_summary(session_id: str) -> dict[str, Any] | None:
    rows, _ = _run(
        "get_session_summary",
        "SELECT * FROM session_summaries WHERE session_id = %s",
        [session_id], fetch="one", log_params=[session_id],
    )
    if not rows:
        return None
    try:
        return json.loads(rows[0]["summary_json"])
    except json.JSONDecodeError:
        return None


def course_catalog() -> list[dict[str, Any]]:
    rows, _ = _run(
        "course_catalog",
        "SELECT q.course AS course, COUNT(*) AS questions, "
        "(SELECT COUNT(*) FROM materials m WHERE m.course = q.course) AS materials "
        "FROM questions q GROUP BY q.course ORDER BY q.course",
    )
    return rows


def usage_event_counts(exclude_prefixes: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    conds = []
    params: list[Any] = []
    for excluded in exclude_prefixes:
        conds.append("user_id NOT LIKE %s")
        params.append(f"{excluded}%")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows, _ = _run(
        "usage_event_counts",
        f"SELECT event, COUNT(*) AS count FROM usage_events{where} GROUP BY event ORDER BY count DESC",
        params, log_params=params,
    )
    return rows


def list_sessions(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows, _ = _run(
        "list_sessions",
        "SELECT id, user_id, started_at, ended_at, status, planned_count, answered_count, correct_count "
        "FROM sessions WHERE user_id = %s ORDER BY started_at DESC LIMIT %s",
        [user_id, limit], log_params=[user_id, limit],
    )
    return rows


def clear_demo_users(prefix: str = "demo-") -> int:
    pattern = f"{prefix}%"
    for table in ("attempts", "sessions", "session_summaries", "question_state", "usage_events", "feedback"):
        _run(f"clear_demo_{table}", f"DELETE FROM {table} WHERE user_id LIKE %s", [pattern], fetch="none", log_params=[pattern])
    rows, _ = _run(
        "clear_demo_users", "DELETE FROM users WHERE id LIKE %s", [pattern],
        fetch="none", log_params=[pattern],
    )
    return len(rows)
