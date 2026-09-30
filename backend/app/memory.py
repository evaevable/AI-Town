"""记忆系统（按 NPC × 玩家隔离）。

六层：
  1. 短期窗口 turns       —— 最近 N 轮原文，直接进上下文
  2. 情景记忆 episodes    —— 每轮摘要 + 重要性(1-10) + 情绪；混合检索
  3. 玩家档案 facts       —— 结构化事实，key 去重覆盖
  4. 反思 reflections     —— NPC 对玩家的整体印象
  5. 八卦 episodes(kind=gossip/npc_chat) —— 从别的 NPC 听来的，带 source
  6. 世界记忆 world_events —— 全员共享

检索打分（参考 Generative Agents）：
  score = 0.5 × 相关度(FTS5 bm25 归一化) + 0.25 × 近因(按最后访问时间指数衰减) + 0.25 × 重要性/10
"""

import math
import re
import time
from typing import Any, Dict, List, Optional

from .db import DB

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> List[str]:
    """中文按二元组切分，英文数字按词。FTS5 默认分词器不认识中文，所以预先切好。"""
    toks: List[str] = []
    for seg in _CJK.findall(text or ""):
        if len(seg) == 1:
            toks.append(seg)
        toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    toks.extend(w.lower() for w in _WORD.findall(text or ""))
    return toks


def similarity(a: str, b: str) -> float:
    """二元组 Jaccard 相似度，用于防复读。"""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class MemoryStore:
    def __init__(self, db: DB):
        self.db = db

    # ---------------- 1. 短期窗口 ----------------
    def add_turn(self, npc_id: str, player_id: str, role: str, content: str) -> None:
        self.db.execute("INSERT INTO turns(npc_id,player_id,role,content,created_at) VALUES(?,?,?,?,?)",
                        (npc_id, player_id, role, content, time.time()))

    def recent_turns(self, npc_id: str, player_id: str, n_turns: int) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT role,content,created_at FROM turns WHERE npc_id=? AND player_id=? ORDER BY id DESC LIMIT ?",
            (npc_id, player_id, n_turns * 2))
        return list(reversed(rows))

    def last_npc_line(self, npc_id: str, player_id: str) -> str:
        row = self.db.one("SELECT content FROM turns WHERE npc_id=? AND player_id=? AND role='npc' "
                          "ORDER BY id DESC LIMIT 1", (npc_id, player_id))
        return row["content"] if row else ""

    # ---------------- 2/5. 情景记忆 & 八卦 ----------------
    def add_episode(self, npc_id: str, player_id: str, content: str, importance: float = 5,
                    kind: str = "chat", emotion: str = "", source: str = "", game_time: str = "") -> int:
        importance = max(1.0, min(10.0, float(importance)))
        now = time.time()
        with self.db.lock:
            cur = self.db.conn.execute(
                "INSERT INTO episodes(npc_id,player_id,kind,content,importance,emotion,source,game_time,"
                "created_at,last_access) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (npc_id, player_id, kind, content, importance, emotion, source, game_time, now, now))
            eid = cur.lastrowid
            self.db.conn.execute("INSERT INTO episodes_fts(rowid,tokens) VALUES(?,?)",
                                 (eid, " ".join(tokenize(content))))
            self.db.conn.commit()
        return eid

    def retrieve(self, npc_id: str, player_id: str, query: str, k: int = 5,
                 touch: bool = True) -> List[Dict[str, Any]]:
        toks = list(dict.fromkeys(tokenize(query)))[:40]
        relevance: Dict[int, float] = {}
        if toks:
            match = " OR ".join('"%s"' % t.replace('"', "") for t in toks)
            rows = self.db.query(
                "SELECT e.id, bm25(episodes_fts) AS r FROM episodes_fts JOIN episodes e ON e.id=episodes_fts.rowid "
                "WHERE episodes_fts MATCH ? AND e.npc_id=? AND e.player_id=? AND e.archived=0 "
                "ORDER BY r LIMIT 30", (match, npc_id, player_id))
            if rows:
                best = min(r["r"] for r in rows)  # bm25 越小越相关（负数）
                for r in rows:
                    relevance[r["id"]] = (r["r"] / best) if best < 0 else 0.0
        # 候选 = FTS 命中 + 最近/高重要性若干条（保证没有关键词重合时也能想起重要的事）
        recent = self.db.query(
            "SELECT id FROM episodes WHERE npc_id=? AND player_id=? AND archived=0 "
            "ORDER BY importance DESC, id DESC LIMIT 15", (npc_id, player_id))
        ids = set(relevance) | {r["id"] for r in recent}
        if not ids:
            return []
        rows = self.db.query(
            f"SELECT * FROM episodes WHERE id IN ({','.join('?' * len(ids))})", list(ids))
        now = time.time()
        scored = []
        for r in rows:
            hours = max(0.0, (now - r["last_access"]) / 3600)
            recency = math.exp(-hours / 72)  # 三天衰减到 1/e
            rel = relevance.get(r["id"], 0.0)
            score = 0.5 * rel + 0.25 * recency + 0.25 * r["importance"] / 10
            r.update(relevance=round(rel, 3), recency=round(recency, 3), score=round(score, 3))
            scored.append(r)
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:k]
        if touch and top:
            self.db.execute(
                f"UPDATE episodes SET last_access=?, access_count=access_count+1 "
                f"WHERE id IN ({','.join('?' * len(top))})", [now] + [t["id"] for t in top])
        return top

    def random_old_episode(self, npc_id: str, player_id: str, min_importance: float = 5) -> Optional[Dict[str, Any]]:
        return self.db.one(
            "SELECT * FROM episodes WHERE npc_id=? AND player_id=? AND archived=0 AND importance>=? "
            "AND kind IN ('chat','gift','quest','gossip') ORDER BY RANDOM() LIMIT 1",
            (npc_id, player_id, min_importance))

    def list_episodes(self, npc_id: str, player_id: str, limit: int = 100,
                      include_archived: bool = False) -> List[Dict[str, Any]]:
        cond = "" if include_archived else "AND archived=0"
        return self.db.query(f"SELECT * FROM episodes WHERE npc_id=? AND player_id=? {cond} "
                             f"ORDER BY id DESC LIMIT ?", (npc_id, player_id, limit))

    def update_episode(self, eid: int, content: Optional[str] = None, importance: Optional[float] = None) -> None:
        if content is not None:
            self.db.execute("UPDATE episodes SET content=? WHERE id=?", (content, eid))
            self.db.execute("UPDATE episodes_fts SET tokens=? WHERE rowid=?", (" ".join(tokenize(content)), eid))
        if importance is not None:
            self.db.execute("UPDATE episodes SET importance=? WHERE id=?", (max(1, min(10, importance)), eid))

    def delete_episode(self, eid: int) -> None:
        self.db.execute("DELETE FROM episodes WHERE id=?", (eid,))
        self.db.execute("DELETE FROM episodes_fts WHERE rowid=?", (eid,))

    def forget(self, max_age_hours: float = 24 * 7) -> int:
        """遗忘：不重要、从没被想起、很久没访问的记忆归档（不物理删除，管理页可见）。"""
        cutoff = time.time() - max_age_hours * 3600
        cur = self.db.execute(
            "UPDATE episodes SET archived=1 WHERE archived=0 AND importance<4 AND access_count<2 AND last_access<?",
            (cutoff,))
        return cur.rowcount

    # ---------------- 3. 玩家档案 ----------------
    def upsert_fact(self, npc_id: str, player_id: str, key: str, value: str) -> None:
        key, value = key.strip()[:30], value.strip()[:120]
        if not key or not value:
            return
        self.db.execute(
            "INSERT INTO facts(npc_id,player_id,key,value,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(npc_id,player_id,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (npc_id, player_id, key, value, time.time()))

    def facts(self, npc_id: str, player_id: str) -> List[Dict[str, Any]]:
        return self.db.query("SELECT key,value,updated_at FROM facts WHERE npc_id=? AND player_id=? "
                             "ORDER BY updated_at DESC", (npc_id, player_id))

    def delete_fact(self, npc_id: str, player_id: str, key: str) -> None:
        self.db.execute("DELETE FROM facts WHERE npc_id=? AND player_id=? AND key=?", (npc_id, player_id, key))

    # ---------------- 4. 反思 ----------------
    def add_reflection(self, npc_id: str, player_id: str, content: str) -> None:
        self.db.execute("INSERT INTO reflections(npc_id,player_id,content,created_at) VALUES(?,?,?,?)",
                        (npc_id, player_id, content, time.time()))

    def reflections(self, npc_id: str, player_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        return self.db.query("SELECT id,content,created_at FROM reflections WHERE npc_id=? AND player_id=? "
                             "ORDER BY id DESC LIMIT ?", (npc_id, player_id, limit))

    def delete_reflection(self, rid: int) -> None:
        self.db.execute("DELETE FROM reflections WHERE id=?", (rid,))

    # ---------------- 关系 ----------------
    def relation(self, npc_id: str, player_id: str) -> Dict[str, Any]:
        row = self.db.one("SELECT * FROM relations WHERE npc_id=? AND player_id=?", (npc_id, player_id))
        if row:
            return row
        self.db.execute("INSERT OR IGNORE INTO relations(npc_id,player_id) VALUES(?,?)", (npc_id, player_id))
        return self.db.one("SELECT * FROM relations WHERE npc_id=? AND player_id=?", (npc_id, player_id))

    def change_affinity(self, npc_id: str, player_id: str, delta: float) -> float:
        rel = self.relation(npc_id, player_id)
        new = max(0.0, min(100.0, rel["affinity"] + delta))
        self.db.execute("UPDATE relations SET affinity=? WHERE npc_id=? AND player_id=?", (new, npc_id, player_id))
        return new

    def set_affinity(self, npc_id: str, player_id: str, value: float) -> None:
        self.relation(npc_id, player_id)
        self.db.execute("UPDATE relations SET affinity=? WHERE npc_id=? AND player_id=?",
                        (max(0.0, min(100.0, value)), npc_id, player_id))

    def bump_turn(self, npc_id: str, player_id: str, importance: float) -> float:
        """累计一轮，返回距上次反思的累计重要性。"""
        self.relation(npc_id, player_id)
        self.db.execute("UPDATE relations SET turns=turns+1, importance_acc=importance_acc+?, last_talk=? "
                        "WHERE npc_id=? AND player_id=?", (importance, time.time(), npc_id, player_id))
        return self.relation(npc_id, player_id)["importance_acc"]

    def reset_importance_acc(self, npc_id: str, player_id: str) -> None:
        self.db.execute("UPDATE relations SET importance_acc=0 WHERE npc_id=? AND player_id=?", (npc_id, player_id))

    # ---------------- 6. 世界记忆 ----------------
    def add_world_event(self, content: str, game_time: str = "", active_seconds: float = 600) -> int:
        now = time.time()
        cur = self.db.execute("INSERT INTO world_events(content,game_time,created_at,active_until) VALUES(?,?,?,?)",
                              (content, game_time, now, now + active_seconds))
        return cur.lastrowid

    def active_events(self) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM world_events WHERE active_until>? ORDER BY id DESC LIMIT 3",
                             (time.time(),))

    def recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM world_events ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------- 管理 ----------------
    def clear_npc(self, npc_id: str) -> None:
        ids = [r["id"] for r in self.db.query("SELECT id FROM episodes WHERE npc_id=?", (npc_id,))]
        for eid in ids:
            self.db.execute("DELETE FROM episodes_fts WHERE rowid=?", (eid,))
        for table in ("turns", "episodes", "facts", "reflections", "relations"):
            self.db.execute(f"DELETE FROM {table} WHERE npc_id=?", (npc_id,))

    def stats(self, npc_id: str, player_id: str) -> Dict[str, int]:
        q = lambda sql: self.db.one(sql, (npc_id, player_id))["c"]  # noqa: E731
        return {
            "turns": q("SELECT COUNT(*) c FROM turns WHERE npc_id=? AND player_id=?"),
            "episodes": q("SELECT COUNT(*) c FROM episodes WHERE npc_id=? AND player_id=? AND archived=0"),
            "archived": q("SELECT COUNT(*) c FROM episodes WHERE npc_id=? AND player_id=? AND archived=1"),
            "facts": q("SELECT COUNT(*) c FROM facts WHERE npc_id=? AND player_id=?"),
            "reflections": q("SELECT COUNT(*) c FROM reflections WHERE npc_id=? AND player_id=?"),
        }
