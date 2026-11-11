import logging
import os
import re
from datetime import datetime

import pymysql
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tavern")

app = FastAPI(title="忘忧酒馆数据服务", version="1.0.0")

RUMOR_MIN_SCORE = float(os.getenv("RUMOR_MIN_SCORE", "0.5"))


class OrderRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    drink: str = Field(min_length=1, max_length=64)
    pay_mode: str = Field(default="cash", pattern="^(cash|credit)$")
    session_id: str | None = Field(default=None, max_length=128)
    nickname: str | None = Field(default=None, max_length=16)


class RumorSearchRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=3, ge=1, le=10)
    session_id: str | None = Field(default=None, max_length=128)
    nickname: str | None = Field(default=None, max_length=16)


class PrankRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=300)
    session_id: str | None = Field(default=None, max_length=128)
    nickname: str | None = Field(default=None, max_length=16)


class SettleRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    nickname: str | None = Field(default=None, max_length=16)


class ChatRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=128)
    nickname: str | None = Field(default=None, max_length=16)


class ProfileRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    nickname: str = Field(min_length=1, max_length=16)
    session_id: str | None = Field(default=None, max_length=128)


class EventRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=32)
    detail: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=128)


def fmt_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def grudge_level(grudge: int) -> str:
    if grudge >= 8:
        return "通缉"
    if grudge >= 5:
        return "记仇"
    if grudge >= 2:
        return "白眼"
    return "平静"


def status_bar(player: dict) -> str:
    return (
        f"【铜板 {player['coins']}｜欠账 {player['debt']}｜"
        f"戒心 {player['grudge']}（{grudge_level(player['grudge'])}）｜声望 {player['reputation']}】"
    )


def player_json(player: dict) -> dict:
    return {
        "player_id": player["player_id"],
        "nickname": player["nickname"],
        "coins": player["coins"],
        "debt": player["debt"],
        "grudge": player["grudge"],
        "reputation": player["reputation"],
        "visits": player["visits"],
        "last_seen": fmt_dt(player["last_seen"]),
    }


def fetch_player(cur, player_id: str) -> dict:
    cur.execute("SELECT * FROM players WHERE player_id=%s", (player_id,))
    return cur.fetchone()


def ensure_player(cur, player_id: str) -> dict:
    player = fetch_player(cur, player_id)
    if player:
        return player
    cur.execute("INSERT INTO players (player_id) VALUES (%s)", (player_id,))
    return fetch_player(cur, player_id)


def log_event(cur, player_id: str, session_id: str | None, event_type: str, detail: str):
    cur.execute(
        "INSERT INTO events (player_id, session_id, event_type, detail) VALUES (%s, %s, %s, %s)",
        (player_id, session_id, event_type, detail),
    )


def touch_session(cur, player: dict, session_id: str | None) -> dict:
    if not session_id:
        return player
    if player["last_session_id"] != session_id:
        cur.execute(
            "UPDATE players SET visits=visits+1, last_session_id=%s, last_seen=NOW() WHERE player_id=%s",
            (session_id, player["player_id"]),
        )
        log_event(cur, player["player_id"], session_id, "enter", "推门进入忘忧酒馆")
        return fetch_player(cur, player["player_id"])
    cur.execute("UPDATE players SET last_seen=NOW() WHERE player_id=%s", (player["player_id"],))
    return player


def maybe_update_nickname(cur, player: dict, nickname: str | None, session_id: str | None) -> dict:
    if not nickname:
        return player
    nickname = nickname.strip()
    if not nickname or nickname == player["nickname"]:
        return player
    cur.execute("UPDATE players SET nickname=%s, last_seen=NOW() WHERE player_id=%s", (nickname, player["player_id"]))
    log_event(cur, player["player_id"], session_id, "profile", f"玩家自报姓名：{nickname}")
    return fetch_player(cur, player["player_id"])


def build_state(cur, player: dict) -> dict:
    cur.execute("SELECT id, name, price, stock, effect, description, hidden FROM drinks ORDER BY id")
    menu = []
    for row in cur.fetchall():
        if row["hidden"] and not (player["grudge"] <= 2 and player["reputation"] >= 3):
            continue
        menu.append({k: row[k] for k in ("id", "name", "price", "stock", "effect", "description")})

    cur.execute(
        "SELECT event_type, detail, created_at FROM events WHERE player_id=%s ORDER BY id DESC LIMIT 5",
        (player["player_id"],),
    )
    recent = [
        {"event_type": r["event_type"], "detail": r["detail"], "created_at": fmt_dt(r["created_at"])}
        for r in cur.fetchall()
    ]
    return {
        "player": player_json(player),
        "menu": menu,
        "recent_events": recent,
        "grudge_level": grudge_level(player["grudge"]),
        "status_bar": status_bar(player),
    }


def ok(action: str, result_text: str, memory: str, state: dict, data: dict | None = None) -> dict:
    return {
        "ok": True,
        "action": action,
        "result_text": result_text,
        "memory": memory,
        "state": state,
        "data": data or {},
    }


def fail(action: str, code: str, message: str, state: dict) -> dict:
    return {"ok": False, "action": action, "error": {"code": code, "message": message}, "state": state}


def open_conn():
    try:
        return get_conn()
    except pymysql.MySQLError as exc:
        logger.error("seekdb connection failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"seekdb unavailable: {exc}") from exc


def recent_player(cur, player_id: str) -> dict:
    return fetch_player(cur, player_id)


@app.get("/")
def root():
    return {
        "name": "忘忧酒馆数据服务",
        "version": "1.0.0",
        "database": "seekdb/dify_app",
        "endpoints": ["/health", "/api/state", "/api/order", "/api/rumors/search", "/api/prank", "/api/settle", "/api/profile", "/api/events"],
    }


@app.get("/health")
def health():
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        finally:
            conn.close()
        return {"ok": True, "db": "seekdb", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except pymysql.MySQLError as exc:
        return {"ok": False, "db": "seekdb", "error": str(exc)}


@app.get("/api/state")
def get_state(player_id: str = Query(min_length=1, max_length=64), session_id: str | None = Query(default=None, max_length=128)):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, player_id)
        player = touch_session(cur, player, session_id)
        conn.commit()
        state = build_state(cur, player)
        logger.info("state player=%s session=%s visits=%s", player_id, session_id, player["visits"])
        return {"ok": True, "action": "state", "state": state}
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("state failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/order")
def order(req: OrderRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        player = maybe_update_nickname(cur, player, req.nickname, req.session_id)
        conn.commit()

        drink_key = req.drink.strip()
        if drink_key.isdigit():
            cur.execute("SELECT * FROM drinks WHERE id=%s", (int(drink_key),))
        else:
            cur.execute("SELECT * FROM drinks WHERE name=%s", (drink_key,))
        drink = cur.fetchone()
        state = build_state(cur, player)
        if not drink:
            return fail("order", "NO_DRINK", f"酒单上没有「{drink_key}」。", state)
        if drink["hidden"] and not (player["grudge"] <= 2 and player["reputation"] >= 3):
            return fail("order", "HIDDEN_LOCKED", "阿九把酒壶往身后一挪：这酒，你还没资格喝。", state)
        if player["grudge"] >= 8:
            return fail("order", "WANTED", "阿九指了指墙上的通缉令：今天不营业。", state)
        if drink["stock"] <= 0:
            return fail("order", "SOLD_OUT", f"{drink['name']}已经卖完了。", state)

        price = drink["price"]
        coins = player["coins"]
        debt = player["debt"]
        grudge = player["grudge"]
        reputation = player["reputation"]

        if req.pay_mode == "credit":
            if grudge >= 6:
                return fail("order", "NO_CREDIT", "阿九把账本合上了：你的脸，暂时不能赊。", state)
            debt += price
            grudge += 1
            reputation = max(0, reputation - 1)
            pay_text = f"赊账 {price} 铜板"
        else:
            if coins < price:
                return fail(
                    "order",
                    "NOT_ENOUGH_COINS",
                    f"铜板不够：{drink['name']}要 {price}，你只有 {coins}。可以试试赊账。",
                    state,
                )
            coins -= price
            reputation += 1
            pay_text = f"付了 {price} 铜板"

        if drink["name"] == "忘忧酿":
            grudge = max(0, grudge - 1)
        elif drink["name"] == "断肠烧":
            reputation += 2
        elif drink["name"] == "醒酒茶":
            grudge = max(0, grudge - 1)
        elif drink["name"] == "招牌卤味":
            grudge = max(0, grudge - 1)
            reputation += 1
        elif drink["name"] == "狐尾酒":
            grudge = max(0, grudge - 2)
            reputation += 3

        grudge = min(grudge, 10)
        cur.execute(
            "UPDATE players SET coins=%s, debt=%s, grudge=%s, reputation=%s, last_seen=NOW() WHERE player_id=%s",
            (coins, debt, grudge, reputation, player["player_id"]),
        )
        cur.execute("UPDATE drinks SET stock=stock-1 WHERE id=%s", (drink["id"],))
        detail = f"{player['nickname']}点了{drink['name']}，{pay_text}；效果：{drink['effect']}"
        log_event(cur, player["player_id"], req.session_id, "order", detail)
        conn.commit()

        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        result_text = (
            f"阿九把{drink['name']}推到你面前，{drink['description']}"
            f"（{drink['effect']}）。{pay_text}。"
        )
        memory = (
            f"{player['nickname']}在忘忧酒馆点了{drink['name']}，{pay_text}；"
            f"当前铜板{player['coins']}、欠账{player['debt']}、戒心{player['grudge']}。"
        )
        logger.info("order player=%s drink=%s ok=True coins=%s debt=%s grudge=%s", player["player_id"], drink["name"], coins, debt, grudge)
        return ok("order", result_text, memory, state, {"drink": drink["name"], "price": price, "pay_mode": req.pay_mode})
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("order failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/rumors/search")
@app.post("/api/gossip")
def search_rumors(req: RumorSearchRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        player = maybe_update_nickname(cur, player, req.nickname, req.session_id)
        conn.commit()

        query = req.query.strip()
        hits = []
        fallback = False
        try:
            cur.execute(
                "SELECT id, title, content, tags, heat, MATCH(title, content, tags) AGAINST(%s) AS score "
                "FROM rumors WHERE MATCH(title, content, tags) AGAINST(%s) ORDER BY score DESC LIMIT %s",
                (query, query, req.limit),
            )
            hits = [row for row in cur.fetchall() if float(row["score"]) >= RUMOR_MIN_SCORE]
        except pymysql.MySQLError:
            conn.rollback()
            fallback = True

        if not hits:
            fallback = True
            tokens = [t for t in re.split(r"\s+", query) if t][:5]
            if tokens:
                clause = " OR ".join(["title LIKE %s OR content LIKE %s OR tags LIKE %s"] * len(tokens))
                params = []
                for token in tokens:
                    like = f"%{token}%"
                    params.extend([like, like, like])
                params.append(req.limit)
                cur.execute(
                    f"SELECT id, title, content, tags, heat, 0 AS score FROM rumors WHERE {clause} "
                    f"ORDER BY heat DESC LIMIT %s",
                    params,
                )
                hits = cur.fetchall()

        if hits:
            ids = [row["id"] for row in hits]
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(f"UPDATE rumors SET heat=heat+1 WHERE id IN ({placeholders})", ids)
            log_event(cur, player["player_id"], req.session_id, "rumor", f"{player['nickname']}打听「{query}」，命中{len(ids)}条传闻")
        else:
            log_event(cur, player["player_id"], req.session_id, "rumor", f"{player['nickname']}打听「{query}」，没有结果")
        conn.commit()

        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        public_hits = []
        for row in hits:
            public_hits.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "content": row["content"],
                    "tags": row["tags"],
                    "heat": row["heat"],
                    "score": round(float(row["score"]), 4),
                }
            )
        if public_hits:
            result_text = "你打听到几件旧事：\n" + "\n".join(
                f"《{row['title']}》{row['content']}" for row in public_hits
            )
            memory = (
                f"{player['nickname']}打听了「{query}」，听到传闻《{public_hits[0]['title']}》："
                f"{public_hits[0]['content'][:80]}"
            )
        else:
            result_text = "阿九摇头：这事儿没听过。换个词再问。"
            memory = f"{player['nickname']}打听了「{query}」，酒馆里没有相关传闻。"
        logger.info("rumor player=%s query=%s hits=%s fallback=%s", player["player_id"], query, len(public_hits), fallback)
        return ok(
            "gossip",
            result_text,
            memory,
            state,
            {"query": query, "hits": public_hits, "fallback": fallback},
        )
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("rumor search failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/prank")
def prank(req: PrankRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        player = maybe_update_nickname(cur, player, req.nickname, req.session_id)
        conn.commit()

        description = req.description.strip()
        delta = 1
        if any(word in description for word in ("砸", "烧", "放火")):
            delta = 4
        elif any(word in description for word in ("账本", "偷")):
            delta = 3
        elif any(word in description for word in ("酒", "换", "兑", "盐", "糖", "鸡")):
            delta = 2

        grudge = min(10, player["grudge"] + delta)
        reputation = max(0, player["reputation"] - 1)
        cur.execute(
            "UPDATE players SET grudge=%s, reputation=%s, last_seen=NOW() WHERE player_id=%s",
            (grudge, reputation, player["player_id"]),
        )
        cur.execute(
            "INSERT INTO pranks (player_id, description, grudge_delta) VALUES (%s, %s, %s)",
            (player["player_id"], description, delta),
        )
        log_event(cur, player["player_id"], req.session_id, "prank", f"{player['nickname']}恶作剧：{description}（戒心+{delta}）")
        conn.commit()

        if grudge >= 8:
            consequence = "阿九把算盘一扣，门口的风铃响了。墙上多了一张画着你脸的悬赏。"
        elif grudge >= 5:
            consequence = "阿九没抬头，只把你的名字写进了账本最上面一页。"
        elif grudge >= 2:
            consequence = "阿九笑了笑，把酒壶往身后挪了挪。"
        else:
            consequence = "阿九挑了挑眉，像是把这件事记在了心里。"

        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        result_text = f"{consequence}（戒心 +{delta}，当前 {player['grudge']}）"
        memory = (
            f"{player['nickname']}对阿九恶作剧：{description}。"
            f"阿九戒心+{delta}，当前戒心{player['grudge']}。"
        )
        logger.info("prank player=%s delta=%s grudge=%s", player["player_id"], delta, grudge)
        return ok("prank", result_text, memory, state, {"description": description, "grudge_delta": delta})
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("prank failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/settle")
def settle(req: SettleRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        player = maybe_update_nickname(cur, player, req.nickname, req.session_id)
        conn.commit()

        state = build_state(cur, player)
        if player["debt"] <= 0:
            return fail("settle", "NO_DEBT", "你不欠账，阿九懒得理你。", state)

        pay = min(player["coins"], player["debt"])
        coins = player["coins"] - pay
        debt = player["debt"] - pay
        grudge = player["grudge"]
        reputation = player["reputation"]
        if debt == 0:
            grudge = max(0, grudge - 1)
            reputation += 1
            result_text = "阿九把那一页从账本上撕下来，丢进灯里烧了。账清了。"
        else:
            result_text = f"你掏出 {pay} 铜板，阿九在账本上又描了一笔。还欠 {debt}。"

        cur.execute(
            "UPDATE players SET coins=%s, debt=%s, grudge=%s, reputation=%s, last_seen=NOW() WHERE player_id=%s",
            (coins, debt, grudge, reputation, player["player_id"]),
        )
        log_event(cur, player["player_id"], req.session_id, "settle", f"{player['nickname']}还了{pay}铜板，剩余欠账{debt}")
        conn.commit()

        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        memory = f"{player['nickname']}还了{pay}铜板，当前欠账{player['debt']}、戒心{player['grudge']}。"
        logger.info("settle player=%s pay=%s debt=%s", player["player_id"], pay, debt)
        return ok("settle", result_text, memory, state, {"paid": pay, "debt": debt})
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("settle failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/profile")
def profile(req: ProfileRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        conn.commit()

        nickname = req.nickname.strip()
        cur.execute("UPDATE players SET nickname=%s, last_seen=NOW() WHERE player_id=%s", (nickname, player["player_id"]))
        log_event(cur, player["player_id"], req.session_id, "profile", f"玩家自报姓名：{nickname}")
        conn.commit()

        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        memory = f"玩家让阿九记住自己的名字：{nickname}。"
        logger.info("profile player=%s nickname=%s", player["player_id"], nickname)
        return ok("profile", f"阿九抬眼看你：行，{nickname}，记住了。", memory, state, {"nickname": nickname})
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("profile failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/chat")
def chat(req: ChatRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        player = maybe_update_nickname(cur, player, req.nickname, req.session_id)
        log_event(cur, player["player_id"], req.session_id, "chat", req.message)
        conn.commit()

        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        logger.info("chat player=%s", player["player_id"])
        return ok("chat", "", "", state, {"message": req.message})
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("chat failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/events")
def add_event(req: EventRequest):
    conn = open_conn()
    try:
        cur = conn.cursor()
        player = ensure_player(cur, req.player_id)
        player = touch_session(cur, player, req.session_id)
        log_event(cur, player["player_id"], req.session_id, req.event_type, req.detail)
        conn.commit()
        player = recent_player(cur, player["player_id"])
        state = build_state(cur, player)
        logger.info("event player=%s type=%s", player["player_id"], req.event_type)
        return {"ok": True, "action": "event", "state": state}
    except pymysql.MySQLError as exc:
        conn.rollback()
        logger.exception("event failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/events")
def list_events(player_id: str = Query(min_length=1, max_length=64), limit: int = Query(default=20, ge=1, le=50)):
    conn = open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, session_id, event_type, detail, created_at FROM events "
            "WHERE player_id=%s ORDER BY id DESC LIMIT %s",
            (player_id, limit),
        )
        rows = [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "event_type": r["event_type"],
                "detail": r["detail"],
                "created_at": fmt_dt(r["created_at"]),
            }
            for r in cur.fetchall()
        ]
        return {"ok": True, "player_id": player_id, "events": rows}
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        conn.close()
