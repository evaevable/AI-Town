"""HTTP / WebSocket 接口。管理页用的接口都在 /api 下。"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .config import settings
from .db import get_db
from .dialogue import DialogueEngine
from .hub import hub
from .llm import LLMClient
from .memory import MemoryStore
from .registry import Registry, ValidationError, normalize_npc
from .world import World

log = logging.getLogger("api")

router = APIRouter()
PLAYER_ID = "player"


class Env:
    """把各组件放在一起，方便 api 与 lifespan 共用。"""

    def __init__(self) -> None:
        self.ready = False
        self.db = None
        self.store = None
        self.registry = None
        self.llm = None
        self.dialogue = None
        self.world = None


env = Env()


def setup(registry: Registry, llm: LLMClient, world_enabled: bool = True) -> Env:
    env.llm = llm
    env.registry = registry
    env.db = get_db()
    env.store = MemoryStore(env.db)
    env.world = World(env.db, env.store, registry, llm, broadcast=hub.broadcast)
    env.dialogue = DialogueEngine(env.db, env.store, registry, llm, world=env.world)
    env.world.dialogue = env.dialogue
    # 新 NPC 或新玩家默认背包
    for item, count in (registry.game.get("starting_inventory") or {}).items():
        env.db.execute("INSERT OR IGNORE INTO inventory(player_id,item,count) VALUES(?,?,?)",
                       (PLAYER_ID, item, count))
    env.ready = True
    return env


# ---------------- 基础 ----------------
@router.get("/")
async def root() -> Dict[str, Any]:
    return {"service": "合租小屋 API", "version": "2.0", "docs": "/docs", "admin": "/admin",
            "npcs": [n["name"] for n in env.registry.all()], "llm": env.llm.model,
            "mock": bool(env.llm and env.llm.mock)}


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "time": time.time()}


# ---------------- NPC ----------------
@router.get("/api/npcs")
async def list_npcs() -> Dict[str, Any]:
    npcs = []
    for n in env.registry.all():
        st = env.world._state.get(n["id"], {})
        npcs.append({**env.registry.public(n), "location": st.get("location", ""),
                     "activity": st.get("activity", ""), "mood": st.get("mood", ""),
                     "behavior": env.world.behavior(n)})
    return {"npcs": npcs, "total": len(npcs), "max": settings.MAX_NPCS,
            "locations": list(env.registry.game.locations.keys()),
            "sprites": ["character_1", "character_2", "character_3", "character_4"],
            "items": list((env.registry.game.get("items") or {}).keys())}


@router.get("/api/npcs/{npc_id}")
async def get_npc(npc_id: str) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id)
    if not npc:
        raise HTTPException(404, f"NPC '{npc_id}' 不存在")
    rel = env.store.relation(npc["id"], PLAYER_ID)
    level = env.registry.game.affinity_level(rel["affinity"])
    return {"npc": npc, "state": env.world.state()["npcs"][_index(npc["id"])],
            "affinity": rel["affinity"], "level": level["name"], "level_style": level["style"],
            "stats": env.store.stats(npc["id"], PLAYER_ID)}


def _index(npc_id: str) -> int:
    ids = [n["id"] for n in env.registry.all()]
    return ids.index(npc_id)


@router.post("/api/npcs")
async def create_npc(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        npc = env.registry.upsert(body)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    await _after_npc_change()
    return {"ok": True, "npc": npc}


@router.put("/api/npcs/{npc_id}")
async def update_npc(npc_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if env.registry.resolve(npc_id) is None:
        raise HTTPException(404, f"NPC '{npc_id}' 不存在")
    body = {**body, "id": npc_id}
    try:
        npc = env.registry.upsert(body)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    await _after_npc_change()
    return {"ok": True, "npc": npc}


@router.delete("/api/npcs/{npc_id}")
async def delete_npc(npc_id: str, purge: bool = False) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id)
    if not npc:
        raise HTTPException(404, "NPC 不存在")
    if len(env.registry.all()) <= 1:
        raise HTTPException(400, "至少保留一个 NPC")
    env.registry.delete(npc["id"])
    if purge:
        env.store.clear_npc(npc["id"])
    await _after_npc_change()
    return {"ok": True, "deleted": npc["id"]}


@router.get("/api/npcs/{npc_id}/prompt")
async def preview_prompt(npc_id: str, message: str = "你好", player_id: str = PLAYER_ID) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id)
    if not npc:
        raise HTTPException(404, "NPC 不存在")
    ctx = env.dialogue.build_context(npc["id"], player_id, message)
    return {"npc_id": npc["id"], "system_prompt": ctx["system_prompt"],
            "messages": ctx["messages"], "retrieved_memories": [
                {"content": m["content"], "score": m.get("score"), "kind": m["kind"]} for m in ctx["memories"]]}


async def _after_npc_change() -> None:
    env.world._init_state() if not env.world._state else None
    for npc in env.registry.all():
        env.world._state.setdefault(npc["id"], {"location": npc["spawn"], "activity": "",
                                                "mood": "平静", "direct_until": 0.0})
    await hub.broadcast({"type": "npcs_changed", "npcs": [env.registry.public(n) for n in env.registry.all()]})


# ---------------- 记忆 ----------------
@router.get("/api/npcs/{npc_id}/memory")
async def get_memory(npc_id: str, player_id: str = PLAYER_ID, limit: int = 100) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id)
    if not npc:
        raise HTTPException(404, "NPC 不存在")
    rel = env.store.relation(npc["id"], player_id)
    return {
        "npc_id": npc["id"], "player_id": player_id,
        "affinity": rel["affinity"], "level": env.registry.game.affinity_level(rel["affinity"])["name"],
        "episodes": env.store.list_episodes(npc["id"], player_id, limit=limit),
        "facts": env.store.facts(npc["id"], player_id),
        "reflections": env.store.reflections(npc["id"], player_id, limit=10),
        "turns": env.store.recent_turns(npc["id"], player_id, 20),
        "stats": env.store.stats(npc["id"], player_id),
    }


@router.get("/api/npcs/{npc_id}/recall")
async def recall(npc_id: str, query: str, player_id: str = PLAYER_ID, k: int = 5) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id)
    if not npc:
        raise HTTPException(404, "NPC 不存在")
    return {"results": env.store.retrieve(npc["id"], player_id, query, k=k, touch=False)}


class EpisodeBody(BaseModel):
    content: str
    importance: float = 5


@router.post("/api/npcs/{npc_id}/memory/episodes")
async def add_episode(npc_id: str, body: EpisodeBody, player_id: str = PLAYER_ID) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id) or _raise_404()
    eid = env.store.add_episode(npc["id"], player_id, body.content, importance=body.importance,
                                kind="event", game_time=env.world.game_time_str())
    return {"ok": True, "id": eid}


@router.put("/api/memory/episodes/{eid}")
async def edit_episode(eid: int, body: EpisodeBody) -> Dict[str, Any]:
    env.store.update_episode(eid, content=body.content, importance=body.importance)
    return {"ok": True}


@router.delete("/api/memory/episodes/{eid}")
async def delete_episode(eid: int) -> Dict[str, Any]:
    env.store.delete_episode(eid)
    return {"ok": True}


@router.post("/api/npcs/{npc_id}/memory/facts")
async def add_fact(npc_id: str, key: str, value: str, player_id: str = PLAYER_ID) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id) or _raise_404()
    env.store.upsert_fact(npc["id"], player_id, key, value)
    return {"ok": True}


@router.delete("/api/npcs/{npc_id}/memory/facts/{key}")
async def delete_fact(npc_id: str, key: str, player_id: str = PLAYER_ID) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id) or _raise_404()
    env.store.delete_fact(npc["id"], player_id, key)
    return {"ok": True}


@router.delete("/api/memory/reflections/{rid}")
async def delete_reflection(rid: int) -> Dict[str, Any]:
    env.store.delete_reflection(rid)
    return {"ok": True}


@router.post("/api/npcs/{npc_id}/memory/reset")
async def reset_memory(npc_id: str, player_id: str = PLAYER_ID) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id) or _raise_404()
    env.store.clear_npc(npc["id"])
    return {"ok": True}


@router.put("/api/npcs/{npc_id}/affinity")
async def set_affinity(npc_id: str, value: float, player_id: str = PLAYER_ID) -> Dict[str, Any]:
    npc = env.registry.resolve(npc_id) or _raise_404()
    env.store.set_affinity(npc["id"], player_id, value)
    return {"ok": True, "affinity": env.store.relation(npc["id"], player_id)["affinity"]}


def _raise_404():
    raise HTTPException(404, "NPC 不存在")


# ---------------- 世界 / 导演 ----------------
@router.get("/api/world")
async def world_state() -> Dict[str, Any]:
    return env.world.state()


class ClockBody(BaseModel):
    paused: Optional[bool] = None
    speed: Optional[float] = None
    time: Optional[str] = None
    day: Optional[int] = None


@router.post("/api/world/clock")
async def set_clock(body: ClockBody) -> Dict[str, Any]:
    env.world.set_clock(paused=body.paused, speed=body.speed, time_str=body.time, day=body.day)
    await hub.broadcast(env.world.clock_payload())
    return env.world.clock_payload()


class EventBody(BaseModel):
    content: str


@router.post("/api/world/event")
async def trigger_event(body: EventBody) -> Dict[str, Any]:
    return await env.world.trigger_event(body.content)


@router.get("/api/world/events")
async def list_events(limit: int = 20) -> Dict[str, Any]:
    return {"events": env.store.recent_events(limit)}


class DirectBody(BaseModel):
    npc_id: str
    location: Optional[str] = None
    activity: Optional[str] = None
    say: Optional[str] = None
    hold_minutes: float = 45


@router.post("/api/world/direct")
async def direct(body: DirectBody) -> Dict[str, Any]:
    return await env.world.direct(body.npc_id, location=body.location, activity=body.activity,
                                  say=body.say, hold_minutes=body.hold_minutes)


class BehaviorBody(BaseModel):
    npc_id: str
    wander: Optional[bool] = None
    chattiness: Optional[float] = None
    can_chat_with_npcs: Optional[bool] = None
    monologue: Optional[bool] = None


@router.post("/api/world/behavior")
async def set_behavior(body: BehaviorBody) -> Dict[str, Any]:
    npc = env.registry.resolve(body.npc_id) or _raise_404()
    if body.chattiness is not None:
        env.world._overrides.setdefault(npc["id"], {})
        npc2 = env.registry.get(npc["id"])
        npc2["behavior"]["chattiness"] = max(0.0, min(1.0, body.chattiness))
    env.world.set_behavior(npc["id"], wander=body.wander,
                           can_chat_with_npcs=body.can_chat_with_npcs, monologue=body.monologue)
    return {"ok": True, "behavior": env.world.behavior(env.registry.get(npc["id"]))}


@router.post("/api/world/monologue")
async def force_monologue() -> Dict[str, Any]:
    return {"dialogues": await env.world.generate_monologues()}


@router.post("/api/world/npc_chat")
async def force_npc_chat(a: str, b: str) -> Dict[str, Any]:
    return {"lines": await env.world.npc_chat(a, b)}


# ---------------- 玩家 / 背包 / 委托 ----------------
@router.get("/api/player")
async def player_state(player_id: str = PLAYER_ID) -> Dict[str, Any]:
    return {
        "player_id": player_id,
        "inventory": env.db.query("SELECT item,count FROM inventory WHERE player_id=? AND count>0", (player_id,)),
        "quests": env.db.query("SELECT * FROM quests WHERE player_id=? AND status!='done' ORDER BY id DESC",
                               (player_id,)),
        "affinities": [{"npc_id": n["id"], "name": n["name"],
                        "affinity": env.store.relation(n["id"], player_id)["affinity"],
                        "level": env.registry.game.affinity_level(
                            env.store.relation(n["id"], player_id)["affinity"])["name"]}
                       for n in env.registry.all()],
    }


@router.post("/api/player/inventory")
async def grant_item(item: str, count: int = 1, player_id: str = PLAYER_ID) -> Dict[str, Any]:
    env.db.execute("INSERT INTO inventory(player_id,item,count) VALUES(?,?,?) "
                   "ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count",
                   (player_id, item, count))
    await _push_inventory(player_id)
    return {"ok": True}


async def _push_inventory(player_id: str) -> None:
    rows = env.db.query("SELECT item,count FROM inventory WHERE player_id=? AND count>0 ORDER BY item", (player_id,))
    await hub.broadcast({"type": "inventory", "items": rows})


async def _push_quests(player_id: str) -> None:
    rows = env.db.query("SELECT * FROM quests WHERE player_id=? AND status='active' ORDER BY id DESC", (player_id,))
    await hub.broadcast({"type": "quests", "quests": rows})


async def complete_quests(target_npc_id: str, player_id: str = PLAYER_ID) -> List[Dict[str, Any]]:
    """玩家和委托人指定的 NPC 说上话以后，委托完成。"""
    rows = env.db.query("SELECT * FROM quests WHERE player_id=? AND target=? AND status='active'",
                        (player_id, target_npc_id))
    done = []
    for q in rows:
        env.db.execute("UPDATE quests SET status='done' WHERE id=?", (q["id"],))
        giver = env.registry.get(q["giver"])
        if q["reward_item"]:
            env.db.execute("INSERT INTO inventory(player_id,item,count) VALUES(?,?,1) "
                           "ON CONFLICT(player_id,item) DO UPDATE SET count=count+1",
                           (player_id, q["reward_item"]))
        if giver:
            env.store.change_affinity(giver["id"], player_id, 6)
            env.store.add_episode(giver["id"], player_id, f"玩家帮我把话带给了{q['target']}", importance=6,
                                  kind="quest", game_time=env.world.game_time_str())
        env.store.add_episode(target_npc_id, player_id, f"玩家转告我：{q['message']}", importance=5,
                              kind="quest", game_time=env.world.game_time_str())
        done.append(q)
        await hub.broadcast({"type": "toast",
                             "text": f"委托完成：{q['content']}（谢礼 {q['reward_item'] or '一句谢谢'}）"})
    if done:
        await _push_inventory(player_id)
        await _push_quests(player_id)
    return done


# ---------------- 非流式聊天（调试 / curl 用） ----------------
class ChatBody(BaseModel):
    npc_id: str = ""
    npc_name: str = ""
    message: str
    player_id: str = PLAYER_ID


@router.post("/api/chat")
async def chat_once(body: ChatBody) -> Dict[str, Any]:
    npc = env.registry.resolve(body.npc_id or body.npc_name)
    if not npc:
        raise HTTPException(404, "NPC 不存在")
    await complete_quests(npc["id"], body.player_id)
    result: Dict[str, Any] = {"npc_id": npc["id"], "npc_name": npc["name"]}
    async for event in env.dialogue.chat(npc["id"], body.player_id, body.message):
        if event["type"] == "chat_end":
            result.update(event)
    await _push_inventory(body.player_id)
    return result


# ---------------- WebSocket ----------------
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    player_id = PLAYER_ID
    try:
        await ws.send_text(json.dumps({"type": "welcome", "npcs": [env.registry.public(n) for n in env.registry.all()],
                                       "clock": env.world.clock_payload(),
                                       "event": env.world.current_event(),
                                       "locations": list(env.registry.game.locations.keys())},
                                      ensure_ascii=False))
        await ws.send_text(json.dumps({"type": "inventory", "items": env.db.query(
            "SELECT item,count FROM inventory WHERE player_id=? AND count>0 ORDER BY item", (player_id,))},
            ensure_ascii=False))
        await _push_quests(player_id)
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle_ws(ws, msg, player_id)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("WS 异常: %s", e)
    finally:
        await hub.disconnect(ws)


async def _send(ws: WebSocket, payload: Dict[str, Any]) -> None:
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


async def _handle_ws(ws: WebSocket, msg: Dict[str, Any], player_id: str) -> None:
    kind = msg.get("type")
    if kind == "chat":
        npc = env.registry.resolve(str(msg.get("npc_id", "")))
        if not npc:
            return await _send(ws, {"type": "toast", "text": "没有这个 NPC"})
        await complete_quests(npc["id"], player_id)
        async for event in env.dialogue.chat(npc["id"], player_id, str(msg.get("message", ""))[:500]):
            await _send(ws, event)
        await _push_inventory(player_id)
    elif kind == "gift":
        npc = env.registry.resolve(str(msg.get("npc_id", "")))
        item = str(msg.get("item", ""))
        if not npc or not item:
            return
        owned = env.db.one("SELECT count FROM inventory WHERE player_id=? AND item=?", (player_id, item))
        if not owned or owned["count"] <= 0:
            return await _send(ws, {"type": "toast", "text": f"你没有{item}"})
        env.db.execute("UPDATE inventory SET count=count-1 WHERE player_id=? AND item=?", (player_id, item))
        result = await env.dialogue.give_gift(npc["id"], player_id, item)
        await _send(ws, {"type": "gift_result", "npc_id": npc["id"], "npc_name": npc["name"],
                         "item": item, **result})
        await _push_inventory(player_id)
    elif kind == "near":
        npc = env.registry.resolve(str(msg.get("npc_id", "")))
        if npc:
            await env.world.maybe_greet(npc["id"])
    elif kind == "hello":
        await _send(ws, {"type": "welcome", "npcs": [env.registry.public(n) for n in env.registry.all()],
                         "clock": env.world.clock_payload(), "event": env.world.current_event(),
                         "locations": list(env.registry.game.locations.keys())})
    elif kind == "ping":
        await _send(ws, {"type": "pong"})
