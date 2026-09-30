"""世界模拟：游戏时钟、日程、心情、独白、NPC 互聊、导演指令。

后端只决定「谁在什么地点、在做什么、说什么」，具体像素位置由 Godot 客户端寻路走过去。
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .config import settings
from .db import DB
from .llm import LLMClient
from .memory import MemoryStore
from .prompts import build_monologue_prompt, build_npc_chat_prompt
from .registry import Registry

log = logging.getLogger("world")

Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]


def _fire(coro_factory: Callable[[], Awaitable[Any]]) -> bool:
    """后台触发一个协程；没有事件循环时（例如同步测试里）直接跳过。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    asyncio.ensure_future(coro_factory())
    return True


class World:
    def __init__(self, db: DB, store: MemoryStore, registry: Registry, llm: LLMClient,
                 broadcast: Optional[Broadcast] = None, dialogue: Any = None):
        self.db = db
        self.store = store
        self.registry = registry
        self.llm = llm
        self.broadcast = broadcast
        self.dialogue = dialogue
        self.player_id = "player"

        self.minutes = float(self.db.kv_get("clock_minutes", "480"))  # 08:00
        self.day = int(self.db.kv_get("clock_day", "1"))
        self.paused = self.db.kv_get("clock_paused", "0") == "1"
        self.speed = float(self.db.kv_get("clock_speed", str(settings.GAME_MINUTES_PER_SECOND)))
        self._state: Dict[str, Dict[str, Any]] = {}
        self._overrides: Dict[str, Dict[str, bool]] = {}
        self._mono_due: Dict[str, float] = {}
        self._last_pair_chat: Dict[str, float] = {}
        self._greet_at: Dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_hour = -1
        self._init_state()

    # ---------------- 初始化 ----------------
    def _init_state(self) -> None:
        for npc in self.registry.all():
            mood_row = self.db.kv_get(f"mood:{npc['id']}", "")
            parts = mood_row.split("|")
            if len(parts) == 2 and parts[0] == str(self.day):
                mood = parts[1]
            else:
                mood = random.choice(self.registry.game.get("moods", ["平静"]))
                self.db.kv_set(f"mood:{npc['id']}", f"{self.day}|{mood}")
            loc, act = self._schedule_at(npc, self.game_time_str())
            self._state[npc["id"]] = {"location": loc, "activity": act, "mood": mood,
                                      "direct_until": 0.0}
            self._mono_due[npc["id"]] = random.uniform(0, 30)
        self._last_hour = self.minutes // 60

    def _schedule_at(self, npc: Dict[str, Any], hhmm: str) -> tuple:
        sched = npc.get("schedule") or []
        if not sched:
            return npc["spawn"], ""
        cur = sched[-1]
        for s in sched:
            if s["time"] <= hhmm:
                cur = s
            else:
                break
        return cur["location"], cur["activity"]

    # ---------------- 时钟 ----------------
    def game_time_str(self) -> str:
        m = int(self.minutes) % (24 * 60)
        return f"{m // 60:02d}:{m % 60:02d}"

    def _date_label(self) -> str:
        base = datetime(2026, 9, 1) + timedelta(days=self.day - 1)
        return base.strftime("%m月%d日")

    def clock_payload(self) -> Dict[str, Any]:
        m = int(self.minutes) % (24 * 60)
        seg = "凌晨" if m < 6 * 60 else "早上" if m < 11 * 60 else "中午" if m < 13 * 60 \
            else "下午" if m < 17 * 60 else "傍晚" if m < 19 * 60 else "晚上"
        return {"type": "clock", "day": self.day, "date": self._date_label(), "time": self.game_time_str(),
                "segment": seg, "paused": self.paused, "speed": self.speed}

    def set_clock(self, *, paused: Optional[bool] = None, speed: Optional[float] = None,
                  time_str: Optional[str] = None, day: Optional[int] = None) -> None:
        if paused is not None:
            self.paused = paused
            self.db.kv_set("clock_paused", "1" if paused else "0")
        if speed is not None:
            self.speed = max(0.0, min(120.0, float(speed)))
            self.db.kv_set("clock_speed", str(self.speed))
        if time_str:
            hh, mm = time_str.split(":")
            self.minutes = float(int(hh) * 60 + int(mm))
            self._apply_schedules(force=True)
        if day is not None:
            self.day = int(day)
            self._roll_moods()

    def _roll_moods(self) -> None:
        for npc in self.registry.all():
            mood = random.choice(self.registry.game.get("moods", ["平静"]))
            self.db.kv_set(f"mood:{npc['id']}", f"{self.day}|{mood}")
            if npc["id"] in self._state:
                self._state[npc["id"]]["mood"] = mood
        self.db.kv_set("clock_day", str(self.day))

    # ---------------- 行为开关（导演面板可覆盖） ----------------
    def behavior(self, npc: Dict[str, Any]) -> Dict[str, Any]:
        base = dict(npc.get("behavior") or {})
        base.update(self._overrides.get(npc["id"], {}))
        return base

    def set_behavior(self, npc_id: str, **kwargs: bool) -> Dict[str, Any]:
        ov = self._overrides.setdefault(npc_id, {})
        for k, v in kwargs.items():
            if v is not None and k in ("wander", "can_chat_with_npcs", "monologue"):
                ov[k] = bool(v)
        return ov

    # ---------------- 循环 ----------------
    async def start(self) -> None:
        if self._running or not settings.WORLD_ENABLED:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick(settings.WORLD_TICK_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception("world tick 失败: %s", e)
            await asyncio.sleep(settings.WORLD_TICK_SECONDS)

    async def _tick(self, dt: float) -> None:
        if not self.paused:
            self.minutes += self.speed * dt
            if self.minutes >= 24 * 60:
                self.minutes -= 24 * 60
                self.day += 1
                self.db.kv_set("clock_day", str(self.day))
                self._roll_moods()
            self.db.kv_set("clock_minutes", str(self.minutes))
            self._apply_schedules()
        hour = int(self.minutes) // 60
        if hour != self._last_hour:
            self._last_hour = hour
            if not self.paused:
                await self._maybe_npc_chats()
        await self._maybe_monologues(dt if not self.paused else 0)
        await self._emit_clock()

    async def _emit_clock(self) -> None:
        if self.broadcast:
            await self.broadcast(self.clock_payload())

    def _apply_schedules(self, force: bool = False) -> None:
        hhmm = self.game_time_str()
        for npc in self.registry.all():
            st = self._state.get(npc["id"])
            if not st or time.time() < st.get("direct_until", 0):
                continue
            loc, act = self._schedule_at(npc, hhmm)
            if force or loc != st["location"] or act != st["activity"]:
                st["location"], st["activity"] = loc, act
                if self.broadcast:
                    _fire(lambda: self.broadcast({
                        "type": "npc_move", "npc_id": npc["id"], "npc_name": npc["name"],
                        "location": loc, "activity": act, "reason": "schedule"}))

    # ---------------- 独白 ----------------
    async def _maybe_monologues(self, dt: float) -> None:
        if self.paused:
            return
        due = []
        for npc in self.registry.all():
            if not self.behavior(npc).get("monologue", True):
                continue
            self._mono_due[npc["id"]] = self._mono_due.get(npc["id"], 0) - self.speed * dt
            if self._mono_due[npc["id"]] <= 0:
                due.append(npc)
        if not due:
            return
        window = self.registry.game.rand("monologue_minutes", 90)
        for npc in due:
            self._mono_due[npc["id"]] = window * random.uniform(0.7, 1.4)
        await self.generate_monologues([n["id"] for n in due])

    async def generate_monologues(self, npc_ids: Optional[List[str]] = None) -> Dict[str, str]:
        npcs = [n for n in self.registry.all() if not npc_ids or n["id"] in npc_ids]
        if not npcs:
            return {}
        ctx = f"{self._date_label()} {self.game_time_str()}，合租小屋里"
        event = self.current_event()
        if event:
            ctx += f"，{event}"
        enriched = []
        for n in npcs:
            st = self._state[n["id"]]
            enriched.append({**n, "location": st["location"], "activity": st["activity"]})
        mock = {n["name"]: f"（{n['name']}）嗯……先把手头的事做完吧。" for n in npcs}
        data = await self.llm.complete_json(build_monologue_prompt(enriched, ctx), mock_value=mock)
        out: Dict[str, str] = {}
        if isinstance(data, dict):
            for n in npcs:
                text = str(data.get(n["name"], "")).strip()
                if text:
                    out[n["id"]] = text
        for npc_id, text in out.items():
            if self.broadcast:
                await self.broadcast({"type": "bubble", "npc_id": npc_id, "text": text, "kind": "monologue"})
        return out

    # ---------------- NPC 互聊 ----------------
    def current_event(self) -> str:
        rows = self.store.active_events()
        return rows[0]["content"] if rows else ""

    async def _maybe_npc_chats(self) -> None:
        cfg = self.registry.game
        chance = cfg.rand("npc_chat_chance", 0.25)
        npcs = self.registry.all()
        for i, a in enumerate(npcs):
            for b in npcs[i + 1:]:
                ba, bb = self.behavior(a), self.behavior(b)
                if not (ba.get("can_chat_with_npcs", True) and bb.get("can_chat_with_npcs", True)):
                    continue
                if self._state[a["id"]]["location"] != self._state[b["id"]]["location"]:
                    continue
                key = "|".join(sorted([a["id"], b["id"]]))
                if time.time() - self._last_pair_chat.get(key, 0) < 40:
                    continue
                if random.random() > chance:
                    continue
                self._last_pair_chat[key] = time.time()
                _fire(lambda: self.npc_chat(a["id"], b["id"]))

    async def npc_chat(self, a_id: str, b_id: str) -> List[Dict[str, str]]:
        a, b = self.registry.get(a_id), self.registry.get(b_id)
        if not a or not b:
            return []
        ctx = f"{self._date_label()} {self.game_time_str()}，{self._state[a_id]['location']}"
        event = self.current_event()
        if event:
            ctx += f"，{event}"
        share = ""
        gossips = self.store.db.query(
            "SELECT content FROM episodes WHERE npc_id IN (?,?) AND kind='gossip' ORDER BY id DESC LIMIT 1",
            (a_id, b_id))
        if gossips:
            share = gossips[0]["content"]
        mock = [{"speaker": a["name"], "line": f"（{a['name']}）你今天怎么样？"},
                {"speaker": b["name"], "line": f"（{b['name']}）还行，就是有点累。"}]
        data = await self.llm.complete_json(build_npc_chat_prompt(a, b, ctx, share), mock_value=mock)
        lines: List[Dict[str, str]] = []
        if isinstance(data, list):
            for item in data[:4]:
                if isinstance(item, dict) and str(item.get("line", "")).strip():
                    speaker = a["name"] if str(item.get("speaker", "")) == a["name"] else b["name"]
                    lines.append({"speaker": speaker, "line": str(item["line"]).strip()})
        if not lines:
            return []
        if self.broadcast:
            await self.broadcast({"type": "npc_chat", "location": self._state[a_id]["location"],
                                  "lines": lines})
        summary = "；".join(f"{l['speaker']}：{l['line']}" for l in lines)
        for npc, other in ((a, b), (b, a)):
            self.store.add_episode(npc["id"], self.player_id, f"我和{other['name']}聊天：{summary}",
                                   importance=4, kind="npc_chat", source=other["name"],
                                   game_time=self.game_time_str())
            self.store.add_episode(npc["id"], self.player_id,
                                   f"听{other['name']}提到：{summary}", importance=3, kind="gossip",
                                   source=other["name"], game_time=self.game_time_str())
        return lines

    # ---------------- 世界事件 ----------------
    async def trigger_event(self, content: str) -> Dict[str, Any]:
        eid = self.store.add_world_event(content, game_time=self.game_time_str())
        payload = {"type": "event", "id": eid, "content": content, "time": self.game_time_str()}
        if self.broadcast:
            await self.broadcast(payload)
        # 事件立刻影响独白
        _fire(lambda: self.generate_monologues())
        return payload

    # ---------------- 导演指令 ----------------
    async def direct(self, npc_id: str, *, location: Optional[str] = None,
                     activity: Optional[str] = None, say: Optional[str] = None,
                     hold_minutes: float = 45) -> Dict[str, Any]:
        npc = self.registry.get(npc_id)
        if not npc:
            return {"ok": False, "error": "NPC 不存在"}
        st = self._state[npc_id]
        if location and location in self.registry.game.locations:
            st["location"] = location
            st["direct_until"] = time.time() + hold_minutes  # 保留真实时间，暂停时也生效
        if activity:
            st["activity"] = activity
        if self.broadcast:
            await self.broadcast({"type": "npc_move", "npc_id": npc_id, "npc_name": npc["name"],
                                  "location": st["location"], "activity": st["activity"], "reason": "director"})
        if say:
            self.store.add_episode(npc_id, self.player_id, f"（导演让我说）{say}", importance=3,
                                   kind="event", game_time=self.game_time_str())
            if self.broadcast:
                await self.broadcast({"type": "bubble", "npc_id": npc_id, "text": say, "kind": "director"})
        return {"ok": True, "state": dict(st)}

    def state(self) -> Dict[str, Any]:
        return {"clock": self.clock_payload(),
                "evpresets": self.registry.game.get("event_presets", []),
                "items": list((self.registry.game.get("items") or {}).keys()),
                "locations": list(self.registry.game.locations.keys()),
                "npcs": [{"id": n["id"], "name": n["name"], **self._state[n["id"]],
                          "behavior": self.behavior(n)} for n in self.registry.all()],
                "event": self.current_event()}

    # ---------------- 查询 ----------------
    def snapshot(self, npc_id: str) -> Dict[str, Any]:
        st = self._state.get(npc_id, {"location": "", "activity": "", "mood": "平静"})
        return {"game_time": f"{self._date_label()} {self.game_time_str()}", "location": st["location"],
                "activity": st["activity"], "mood": st["mood"], "event": self.current_event(),
                "day": self.day}

    # ---------------- 主动搭话 ----------------
    async def maybe_greet(self, npc_id: str) -> Optional[Dict[str, Any]]:
        npc = self.registry.get(npc_id)
        if not npc or not self.dialogue:
            return None
        b = self.behavior(npc)
        now = time.time()
        if now - self._greet_at.get(npc_id, 0) < 25:
            return None
        if random.random() > float(b.get("chattiness", 0.5)) * 0.75:
            return None
        self._greet_at[npc_id] = now
        result = await self.dialogue.greet(npc_id, self.player_id)
        if result and self.broadcast:
            await self.broadcast({"type": "bubble", "npc_id": npc_id, "text": result["text"], "kind": "greet"})
        return result
