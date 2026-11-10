from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import coach, config, db, mastery, memory, server
from . import powercontext_client as pc
from .powercontext_client import PowerContextError
from .text import now_iso, read_jsonl_tail, sha256_text

COOKIE_NAME = "wengu_user"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    try:
        db.init_schema()
        counts = db.check()["counts"]
        if counts.get("materials", 0) == 0:
            db.import_seed()
    except Exception as error:  # pragma: no cover - startup diagnostics
        print(f"[wengu] seekdb 初始化失败：{error}", flush=True)
    try:
        server.start()
    except Exception as error:  # pragma: no cover - startup diagnostics
        print(f"[wengu] PowerContext 启动失败：{error}", flush=True)
    yield


app = FastAPI(title="温故 · 复习教练", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.globals.update(STYLE_LABELS=coach.STYLE_LABELS, APP_NAME="温故")


def _fmt_time(value: str | None) -> str:
    if not value:
        return "—"
    text = str(value)
    try:
        if "T" in text:
            parsed = datetime.fromisoformat(text)
        else:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%m-%d %H:%M")
    except ValueError:
        return text[:16]


templates.env.filters["fmt_time"] = _fmt_time


def _current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.cookies.get(COOKIE_NAME)
    if not user_id:
        return None
    return db.list_user(user_id)


def _render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    user: dict[str, Any] | None = None,
    status_code: int = 200,
):
    payload = {
        "request": request,
        "user": user,
        "current_path": request.url.path,
        "now": now_iso(),
    }
    payload.update(context or {})
    return templates.TemplateResponse(request, template, payload, status_code=status_code)


def _welcome_redirect() -> RedirectResponse:
    return RedirectResponse("/welcome", status_code=303)


def _set_cookie(response: RedirectResponse, user_id: str) -> RedirectResponse:
    response.set_cookie(COOKIE_NAME, user_id, max_age=31536000, httponly=True, samesite="lax")
    return response


def _plan_view(detail: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = {attempt["question_id"]: attempt for attempt in detail["attempts"]}
    next_item = detail.get("next_item")
    view = []
    for item in (detail.get("plan") or {}).get("items", []):
        attempt = attempts.get(item["id"])
        if attempt:
            status = "done-correct" if attempt["correct"] else "done-wrong"
        elif next_item and next_item["id"] == item["id"]:
            status = "current"
        else:
            status = "pending"
        view.append({**item, "status": status, "attempt": attempt})
    return view


def _owns_session(session_id: str, user_id: str) -> bool:
    session = db.get_session(session_id)
    return bool(session and session["user_id"] == user_id)


@app.get("/health")
def health():
    try:
        counts = db.check()["counts"]
        return {
            "ok": True,
            "seekdb": {"ok": True, "counts": counts},
            "powercontext": {"ok": pc.health(), "url": config.POWERCONTEXT_URL},
            "time": now_iso(),
        }
    except Exception as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=503)


@app.get("/welcome")
def welcome(request: Request):
    current = _current_user(request)
    try:
        users = db.list_users(30)
    except Exception:
        users = []
    return _render(request, "welcome.html", {"users": users, "current": current}, user=current)


@app.get("/enter")
def enter(request: Request):
    name = (request.query_params.get("name") or "").strip()[:24]
    if not name:
        return _welcome_redirect()
    existing = next((user for user in db.list_users(100) if user["display_name"] == name), None)
    user_id = existing["id"] if existing else "u_" + sha256_text(name)[:10]
    db.ensure_user(user_id, name)
    db.usage_event(user_id, "enter", {"source": "welcome"})
    try:
        memory.ensure_scope(user_id)
    except PowerContextError:
        pass
    return _set_cookie(RedirectResponse("/", status_code=303), user_id)


@app.get("/switch/{user_id}")
def switch_user(user_id: str):
    if not db.list_user(user_id):
        return _welcome_redirect()
    return _set_cookie(RedirectResponse("/", status_code=303), user_id)


@app.get("/logout")
def logout():
    response = RedirectResponse("/welcome", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/")
def dashboard(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    data = coach.dashboard(user["id"])
    meaningful = {
        "library_search",
        "explanation_evidence",
        "explanation_evidence_kp_fallback",
        "due_questions",
        "weak_questions",
        "new_questions",
        "cli_search",
    }
    data["recent_queries"] = [
        query for query in data["recent_queries"] if query.get("purpose") in meaningful
    ][:8]
    active_detail = None
    active_view: list[dict[str, Any]] = []
    if data["active_session"]:
        active_detail = coach.get_session_detail(data["active_session"]["id"])
        active_view = _plan_view(active_detail)
    latest = data.get("latest_session")
    latest_summary = db.get_session_summary(latest["id"]) if latest else None
    return _render(
        request,
        "dashboard.html",
        {
            "data": data,
            "profile": data["profile"],
            "catalog": db.course_catalog(),
            "active_detail": active_detail,
            "active_view": active_view,
            "latest": latest,
            "latest_summary": latest_summary,
            "sessions": db.list_sessions(user["id"], 5),
        },
        user=user,
    )


@app.get("/library")
def library(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    query = (request.query_params.get("q") or "").strip()
    course = (request.query_params.get("course") or "").strip() or None
    chapter = (request.query_params.get("chapter") or "").strip() or None
    catalog = db.course_catalog()
    search_result = None
    materials: list[dict[str, Any]] = []
    if query:
        search_result = coach.library_search(user["id"], query, course=course, chapter=chapter)
    else:
        materials = db.list_materials(course, 100)
    return _render(
        request,
        "library.html",
        {
            "query": query,
            "course": course,
            "chapter": chapter,
            "catalog": catalog,
            "search_result": search_result,
            "materials": materials,
        },
        user=user,
    )


@app.get("/practice/start")
def practice_start(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    course = (request.query_params.get("course") or "").strip() or None
    valid_courses = {row["course"] for row in db.course_catalog()}
    if course not in valid_courses:
        course = None
    started = coach.start_session(user["id"], user["display_name"], focus_course=course)
    return RedirectResponse(f"/practice/{started['session_id']}", status_code=303)


@app.get("/practice/{session_id}")
def practice(request: Request, session_id: str):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    if not _owns_session(session_id, user["id"]):
        return _render(request, "error.html", {"message": "这个复习会话不属于当前用户。"}, user=user, status_code=403)
    detail = coach.get_session_detail(session_id)
    if detail["session"]["status"] != "active":
        return RedirectResponse(f"/practice/{session_id}/summary", status_code=303)
    context = {
        "session_id": session_id,
        "detail": detail,
        "plan": detail["plan"],
        "plan_view": _plan_view(detail),
        "next_item": detail["next_item"],
        "progress": detail["progress"],
        "profile": (detail["plan"] or {}).get("profile", {}),
        "influences": (detail["plan"] or {}).get("influences", []),
    }
    return _render(request, "practice.html", context, user=user)


@app.get("/practice/{session_id}/summary")
def practice_summary(request: Request, session_id: str):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    if not _owns_session(session_id, user["id"]):
        return _render(request, "error.html", {"message": "这个复习会话不属于当前用户。"}, user=user, status_code=403)
    detail = coach.get_session_detail(session_id)
    summary = db.get_session_summary(session_id)
    if summary is None:
        attempts = detail["attempts"]
        correct = sum(1 for attempt in attempts if attempt["correct"])
        summary = {
            "session_id": session_id,
            "answered": len(attempts),
            "correct": correct,
            "accuracy": round(correct / len(attempts), 4) if attempts else 0,
            "weak_points": [],
            "remaining": [item["id"] for item in (detail["plan"] or {}).get("items", []) if item["id"] not in detail["answered_ids"]],
            "summary": "会话已结束。",
            "memory": {},
            "finished_at": detail["session"].get("ended_at"),
        }
    return _render(
        request,
        "summary.html",
        {"session_id": session_id, "detail": detail, "summary": summary, "plan": detail["plan"]},
        user=user,
    )


@app.get("/memory")
def memory_page(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    data = coach.memory_evidence(user["id"])
    return _render(request, "memory.html", {"data": data}, user=user)


@app.get("/stats")
def stats_page(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    aggregate = db.stats_overview(None)
    max_attempts = max([row["attempts"] for row in aggregate["recent_days"]] or [1])
    return _render(
        request,
        "stats.html",
        {
            "aggregate": aggregate,
            "personal": db.stats_overview(user["id"]),
            "progress": db.user_progress(user["id"]),
            "events": db.usage_event_counts(),
            "feedback": db.list_feedback(20),
            "users": db.list_users(50),
            "max_attempts": max(1, max_attempts),
            "catalog": db.course_catalog(),
        },
        user=user,
    )


@app.get("/stats/export")
def stats_export(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    excluded = ("demo-", "pytest-")
    aggregate = db.stats_overview(None, exclude_prefixes=excluded)
    personal = db.stats_overview(user["id"])
    events = db.usage_event_counts(exclude_prefixes=excluded)
    feedback = [
        {"rating": row["rating"], "comment": row["comment"], "created_at": row["created_at"]}
        for row in db.list_feedback(50)
        if not row["user_id"].startswith(excluded)
    ]
    users = [item for item in db.list_users(500) if not item["id"].startswith(excluded)]
    payload = {
        "exported_at": now_iso(),
        "source": "温故应用真实日志（seekdb usage_events / attempts / feedback）；已排除 demo-/pytest- 验证账号",
        "aggregate": aggregate,
        "personal": personal,
        "usage_events": events,
        "feedback": feedback,
        "users": len(users),
    }
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": f'attachment; filename="wengu_usage_{datetime.now().strftime("%Y%m%d_%H%M")}.json"'
        },
    )


@app.get("/evidence")
def evidence(request: Request):
    user = _current_user(request)
    if not user:
        return _welcome_redirect()
    restart_log = read_jsonl_tail(config.RUNS_DIR / "powercontext_restart.jsonl", 6)
    all_queries = db.recent_queries(80)
    fulltext_queries = [query for query in all_queries if "MATCH" in (query.get("sql") or "").upper()]
    key_purposes = {
        "library_search",
        "explanation_evidence",
        "explanation_evidence_kp_fallback",
        "due_questions",
        "weak_questions",
        "new_questions",
        "cli_search",
    }
    key_queries = [query for query in all_queries if query.get("purpose") in key_purposes]
    selected = []
    seen = set()
    for group in (fulltext_queries[:4], key_queries[:8], all_queries[:6]):
        for query in group:
            query_id = query.get("query_id")
            if query_id in seen:
                continue
            seen.add(query_id)
            query["is_fulltext"] = "MATCH" in (query.get("sql") or "").upper()
            selected.append(query)
    return _render(
        request,
        "evidence.html",
        {
            "queries": selected[:15],
            "memory_log": memory.recent_memory_log(15),
            "restart_log": restart_log,
            "status": server.status(),
            "counts": db.check()["counts"],
            "runs_dir": str(config.RUNS_DIR),
        },
        user=user,
    )


class AnswerBody(BaseModel):
    question_id: str
    selected: int | str | None = None
    rating: str | None = None
    elapsed_ms: int = 0


class PreferencesBody(BaseModel):
    daily_goal: int | None = None
    explanation_style: str | None = None
    display_name: str | None = None


class FeedbackBody(BaseModel):
    rating: int
    comment: str = ""


class ResetBody(BaseModel):
    confirm: bool = False


@app.post("/api/practice/{session_id}/answer")
def api_answer(request: Request, session_id: str, body: AnswerBody):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "请先进入应用。"}, status_code=401)
    if not _owns_session(session_id, user["id"]):
        return JSONResponse({"error": "这个复习会话不属于当前用户。"}, status_code=403)
    try:
        result = coach.submit_answer(
            session_id=session_id,
            question_id=body.question_id,
            selected=body.selected,
            rating=body.rating,
            elapsed_ms=body.elapsed_ms,
        )
        return JSONResponse(result)
    except coach.CoachError as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.post("/api/practice/{session_id}/finish")
def api_finish(request: Request, session_id: str):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "请先进入应用。"}, status_code=401)
    if not _owns_session(session_id, user["id"]):
        return JSONResponse({"error": "这个复习会话不属于当前用户。"}, status_code=403)
    try:
        return JSONResponse(coach.finish_session(session_id))
    except coach.CoachError as error:
        return JSONResponse({"error": str(error)}, status_code=400)


@app.post("/api/preferences")
def api_preferences(request: Request, body: PreferencesBody):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "请先进入应用。"}, status_code=401)
    profile = coach.set_preferences(
        user["id"],
        daily_goal=body.daily_goal,
        explanation_style=body.explanation_style,
        display_name=body.display_name,
    )
    return JSONResponse({"profile": profile})


@app.post("/api/feedback")
def api_feedback(request: Request, body: FeedbackBody):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "请先进入应用。"}, status_code=401)
    rating = max(1, min(5, int(body.rating)))
    comment = body.comment.strip()[:500]
    feedback_id = db.add_feedback(user["id"], user["display_name"], rating, comment)
    db.usage_event(user["id"], "feedback", {"rating": rating, "length": len(comment)})
    return JSONResponse({"ok": True, "feedback_id": feedback_id})


@app.post("/api/memory/reset")
def api_memory_reset(request: Request, body: ResetBody):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "请先进入应用。"}, status_code=401)
    if not body.confirm:
        return JSONResponse({"error": "请确认后再清空。"}, status_code=400)
    db.clear_user_data(user["id"])
    scope_id = memory.reset_scope(user["id"])
    db.usage_event(user["id"], "memory_reset", {"new_scope_id": scope_id})
    return JSONResponse({"ok": True, "scope_id": scope_id})


@app.exception_handler(coach.CoachError)
async def coach_error_handler(request: Request, exc: coach.CoachError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": str(exc)}, status_code=400)
    return _render(request, "error.html", {"message": str(exc)}, user=_current_user(request), status_code=400)


@app.exception_handler(PowerContextError)
async def pc_error_handler(request: Request, exc: PowerContextError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": str(exc)}, status_code=503)
    return _render(request, "error.html", {"message": str(exc)}, user=_current_user(request), status_code=503)


@app.exception_handler(db.SeekDBError)
async def db_error_handler(request: Request, exc: db.SeekDBError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": str(exc)}, status_code=503)
    return _render(request, "error.html", {"message": str(exc)}, user=_current_user(request), status_code=503)
