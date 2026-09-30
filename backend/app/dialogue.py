"""对话引擎：一次流式回复 + 一次后台分析，负责好感度、记忆、档案、反思、委托。"""

import logging
import random
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from .db import DB
from .config import settings
from .llm import LLMClient
from .memory import MemoryStore, similarity
from .prompts import (build_analysis_messages, build_messages, build_reflection_messages,
                      build_system_prompt)
from .registry import Registry

log = logging.getLogger("dialogue")


class DialogueEngine:
    def __init__(self, db: DB, store: MemoryStore, registry: Registry, llm: LLMClient,
                 world: Any = None):
        self.db = db
        self.store = store
        self.registry = registry
        self.llm = llm
        self.world = world  # world.World，用于取时间和事件

    # ---------------- 上下文 ----------------
    def _player_name(self, npc_id: str, player_id: str) -> str:
        rows = {r["key"]: r["value"] for r in self.store.facts(npc_id, player_id)}
        return rows.get("名字") or rows.get("称呼") or self.registry.game.get("player_name", "你")

    def build_context(self, npc_id: str, player_id: str, message: str) -> Dict[str, Any]:
        npc = self.registry.get(npc_id)
        rel = self.store.relation(npc_id, player_id)
        affinity = rel["affinity"]
        level = self.registry.game.affinity_level(affinity)
        rank = self.registry.game.level_rank(level["name"])
        snap = self.world.snapshot(npc_id) if self.world else {
            "game_time": "08:00", "location": npc["spawn"], "activity": "", "mood": "平静", "event": ""}
        memories = self.store.retrieve(npc_id, player_id, message, k=settings.RETRIEVE_TOP_K)
        cfg = self.registry.game
        topic = ""
        if random.random() < cfg.rand("topic_seed_chance", 0.35):
            topic = random.choice(cfg.get("topic_seeds", ["今天没什么特别的"]))
        if random.random() < cfg.rand("memory_callback_chance", 0.25):
            old = self.store.random_old_episode(npc_id, player_id, min_importance=5)
            if old and old["content"] not in [m["content"] for m in memories]:
                memories.append(old)
                topic = topic or f"突然想起一件事：{old['content']}"
        player_name = self._player_name(npc_id, player_id)
        system_prompt = build_system_prompt(
            npc, self.store, player_id,
            all_npcs=self.registry.all(), affinity=affinity, affinity_level=level, level_rank=rank,
            levels=cfg["affinity_levels"], game_time=snap["game_time"], location=snap["location"],
            activity=snap["activity"], mood=snap["mood"], player_name=player_name,
            event=snap["event"], topic_seed=topic, memories=memories,
        )
        # 防复读：把最近两句自己说过的话写进约束
        recent_replies = [t["content"] for t in self.store.recent_turns(npc_id, player_id, 3)
                          if t["role"] == "npc"][-2:]
        if recent_replies:
            system_prompt += "\n【不要重复】你最近说过：" + " / ".join(f"「{r}」" for r in recent_replies) + \
                             "。换一个说法或换个话题。"
        turns = self.store.recent_turns(npc_id, player_id, settings.SHORT_TERM_TURNS)
        return {
            "npc": npc, "affinity": affinity, "level": level, "rank": rank, "memories": memories,
            "system_prompt": system_prompt, "messages": build_messages(system_prompt, turns, message),
            "player_name": player_name, "snapshot": snap,
        }

    # ---------------- 聊天主流程 ----------------
    async def chat(self, npc_id: str, player_id: str, message: str) -> AsyncIterator[Dict[str, Any]]:
        ctx = self.build_context(npc_id, player_id, message)
        npc = ctx["npc"]
        yield {"type": "chat_start", "npc_id": npc_id, "npc_name": npc["name"],
               "affinity": round(ctx["affinity"], 1), "level": ctx["level"]["name"],
               "retrieved": [{"content": m["content"], "score": m.get("score")} for m in ctx["memories"]]}

        parts: List[str] = []
        try:
            async for piece in self.llm.stream(ctx["messages"], temperature=npc["temperature"],
                                               max_tokens=220, mock_text=_mock_line(npc)):
                parts.append(piece)
                yield {"type": "chat_delta", "npc_id": npc_id, "text": piece}
        except Exception as e:  # 网络失败也不让玩家看到空白
            log.warning("生成失败: %s", e)
            parts = ["……我这边好像有点问题，稍等再说？"]
            yield {"type": "chat_delta", "npc_id": npc_id, "text": parts[0]}
        reply = "".join(parts).strip()

        # 重复度检查：太像上一句就换话题重说一遍（走非流式，直接替换）
        last = self.store.last_npc_line(npc_id, player_id)
        if last and similarity(reply, last) > 0.72 and not self.llm.mock:
            try:
                retry_msgs = ctx["messages"] + [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": "（刚才那句和之前说过的太像了，换个角度、换句话再说一次，别重复。）"}]
                reply2 = await self.llm.complete(retry_msgs, temperature=min(1.2, npc["temperature"] + 0.15),
                                                 max_tokens=220)
                if reply2 and similarity(reply2, last) < similarity(reply, last):
                    reply = reply2
                    yield {"type": "chat_replace", "npc_id": npc_id, "text": reply}
            except Exception as e:
                log.warning("改说失败: %s", e)

        self.store.add_turn(npc_id, player_id, "player", message)
        self.store.add_turn(npc_id, player_id, "npc", reply)

        meta = await self.analyze(npc_id, player_id, message, reply)
        yield {"type": "chat_end", "npc_id": npc_id, "text": reply, **meta}

    async def analyze(self, npc_id: str, player_id: str, message: str, reply: str) -> Dict[str, Any]:
        """后台分析：好感度、重要性、情绪、记忆摘要、玩家档案、快捷选项。"""
        npc = self.registry.get(npc_id)
        known = "\n".join(f"- {f['key']}：{f['value']}" for f in self.store.facts(npc_id, player_id)) or "（无）"
        mock = {"affinity_change": 2, "importance": 4, "emotion": "平静",
                "memory": f"玩家说了「{message[:20]}」", "facts": [], "reply_options": ["嗯嗯", "然后呢？"],
                "reason": "闲聊"}
        data = await self.llm.complete_json(build_analysis_messages(npc["name"], message, reply, known),
                                            mock_value=mock) or mock

        importance = max(1.0, min(10.0, float(data.get("importance", 4))))
        delta = max(-15.0, min(8.0, float(data.get("affinity_change", 0))))
        emotion = str(data.get("emotion", "平静"))
        memory_text = str(data.get("memory") or f"玩家说：{message[:30]}｜我说：{reply[:30]}")

        for f in data.get("facts") or []:
            if isinstance(f, dict) and f.get("key") and f.get("value"):
                self.store.upsert_fact(npc_id, player_id, str(f["key"]), str(f["value"]))

        episode_id = self.store.add_episode(npc_id, player_id, memory_text, importance=importance,
                                            kind="chat", emotion=emotion,
                                            game_time=self.world.game_time_str() if self.world else "")
        affinity = self.store.change_affinity(npc_id, player_id, delta)
        level = self.registry.game.affinity_level(affinity)
        acc = self.store.bump_turn(npc_id, player_id, importance)

        # 送礼类对话给更高权重，顺便标记
        actions = []
        if acc >= settings.REFLECT_IMPORTANCE_SUM:
            reflection = await self._reflect(npc_id, player_id)
            if reflection:
                actions.append({"type": "reflection", "text": reflection})
        quest = await self._maybe_quest(npc_id, player_id, message, reply)
        if quest:
            actions.append({"type": "quest", "quest": quest})

        return {
            "affinity": round(affinity, 1), "affinity_change": delta, "level": level["name"],
            "level_style": level["style"], "emotion": emotion, "importance": importance,
            "memory": memory_text, "episode_id": episode_id,
            "reply_options": [str(o)[:16] for o in (data.get("reply_options") or [])][:3],
            "actions": actions,
        }

    async def _reflect(self, npc_id: str, player_id: str) -> Optional[str]:
        npc = self.registry.get(npc_id)
        episodes = self.store.list_episodes(npc_id, player_id, limit=25)
        player_name = self._player_name(npc_id, player_id)
        mock = f"{player_name}最近经常来找我聊天，感觉关系在变好。"
        text = await self.llm.complete(build_reflection_messages(npc["name"], player_name, episodes,
                                                                 self.store.facts(npc_id, player_id)),
                                       temperature=0.6, max_tokens=160, mock_text=mock)
        if text:
            self.store.add_reflection(npc_id, player_id, text.strip())
            self.store.reset_importance_acc(npc_id, player_id)
            self.store.add_episode(npc_id, player_id, f"我整理了一下对{player_name}的印象：{text.strip()}",
                                   importance=8, kind="event")
        return text

    async def _maybe_quest(self, npc_id: str, player_id: str, message: str, reply: str) -> Optional[Dict]:
        cfg = self.registry.game
        if random.random() > cfg.rand("quest_chance", 0.12):
            return None
        others = [n for n in self.registry.all() if n["id"] != npc_id]
        if not others:
            return None
        target = random.choice(others)
        giver = self.registry.get(npc_id)
        mock = {"content": f"帮我给{target['name']}带句话", "message": "记得吃饭，别老熬夜。",
                "reward": random.choice(list(cfg.get("items", {}).keys()))}
        prompt = [
            {"role": "system", "content": "游戏里的 NPC 想请玩家帮忙带句话给室友。"
                                          '输出 JSON：{"content": "委托内容(15字内)", "message": "要转达的原话(30字内)", '
                                          '"reward": "谢礼物品名"}。只输出 JSON。'},
            {"role": "user", "content": f"{giver['name']}（{giver['title']}，{giver['personality']}）"
                                        f"想托玩家给{target['name']}（{target['title']}）带句话。"
                                        f"可选谢礼：{list(cfg.get('items', {}).keys())}"},
        ]
        data = await self.llm.complete_json(prompt, mock_value=mock)
        if not data or not data.get("content"):
            return None
        cur = self.store.db.execute(
            "INSERT INTO quests(player_id,giver,target,content,message,reward_item,status,created_at) "
            "VALUES(?,?,?,?,?,?,'active',?)",
            (player_id, npc_id, target["id"], str(data["content"])[:40], str(data.get("message", ""))[:80],
             str(data.get("reward", ""))[:20], time.time()))
        self.store.add_episode(npc_id, player_id, f"我拜托玩家给{target['name']}带句话：{data.get('message','')}",
                               importance=6, kind="quest")
        return {"id": cur.lastrowid, "giver": npc_id, "target": target["id"], "target_name": target["name"],
                "content": data["content"], "message": data.get("message", ""), "reward": data.get("reward", "")}

    # ---------------- 送礼 ----------------
    async def give_gift(self, npc_id: str, player_id: str, item: str) -> Dict[str, Any]:
        npc = self.registry.get(npc_id)
        items = self.registry.game.get("items", {})
        item_desc = items.get(item, item)
        liked = item in (npc.get("likes") or [])
        disliked = item in (npc.get("dislikes") or [])
        delta = 8.0 if liked else (-5.0 if disliked else 3.0)
        prompt = [
            {"role": "system", "content": f"你是{npc['name']}（{npc['title']}，{npc['personality']}，"
                                          f"说话风格：{npc['speech_style']}）。玩家送了你{item_desc}。"
                                          f"你{'很喜欢' if liked else ('不太喜欢' if disliked else '觉得还行')}这个东西。"
                                          "用一句话（30字内）回应，符合你的性格，只输出这句话。"},
            {"role": "user", "content": f"（{npc['name']}收到礼物）"},
        ]
        if self.llm.mock:
            line = f"（{npc['name']}）{'谢谢！这个我超喜欢！' if liked else '啊……谢谢。'}"
        else:
            line = await self.llm.complete(prompt, temperature=npc["temperature"], max_tokens=80)
        affinity = self.store.change_affinity(npc_id, player_id, delta)
        level = self.registry.game.affinity_level(affinity)
        self.store.add_episode(npc_id, player_id, f"玩家送了我{item}，我{'很喜欢' if liked else ('不太喜欢' if disliked else '收下了')}",
                               importance=6, kind="gift", emotion="开心" if liked else "平静")
        self.store.bump_turn(npc_id, player_id, 6)
        return {"text": line, "affinity": round(affinity, 1), "affinity_change": delta,
                "level": level["name"], "liked": liked, "disliked": disliked}

    # ---------------- 进门打招呼 / 主动搭话 ----------------
    async def greet(self, npc_id: str, player_id: str) -> Optional[Dict[str, Any]]:
        npc = self.registry.get(npc_id)
        snap = self.world.snapshot(npc_id) if self.world else {"game_time": "08:00", "location": "", "activity": "", "mood": ""}
        prompt = [
            {"role": "system", "content": f"你是{npc['name']}（{npc['title']}，{npc['personality']}，"
                                          f"说话风格：{npc['speech_style']}）。{GREETING_SUFFIX}"},
            {"role": "user", "content": f"现在是{snap['game_time']}，你在{snap['location']}{snap['activity']}，"
                                        f"心情{snap['mood']}。玩家刚走到你旁边。"},
        ]
        mock = f"（{npc['name']}）哟，你来啦？"
        line = await self.llm.complete(prompt, temperature=npc["temperature"], max_tokens=60, mock_text=mock)
        return {"npc_id": npc_id, "npc_name": npc["name"], "text": line}


GREETING_SUFFIX = "玩家刚走过来，你主动跟他搭一句话：一句话，20字以内，符合性格和正在做的事，不要寒暄套话。只输出这句话。"


def _mock_line(npc: Dict[str, Any]) -> str:
    return random.choice([
        f"（{npc['name']}）嗯，你说得对。",
        f"（{npc['name']}）哈，这事有意思。",
        f"（{npc['name']}）等我一下，我这边正忙着手头的事。",
        f"（{npc['name']}）真的假的？你细说。",
    ])
