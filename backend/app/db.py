"""SQLite 单库：所有持久化状态都在这里，重启不丢。"""

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id TEXT NOT NULL, player_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- player / npc / system
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns ON turns(npc_id, player_id, id);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id TEXT NOT NULL, player_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'chat',   -- chat/gift/quest/npc_chat/gossip/event
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 5,  -- 1-10
    emotion TEXT DEFAULT '',
    source TEXT DEFAULT '',              -- 八卦来源 NPC 等
    game_time TEXT DEFAULT '',
    created_at REAL NOT NULL,
    last_access REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ep ON episodes(npc_id, player_id, archived);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(tokens);

CREATE TABLE IF NOT EXISTS facts (
    npc_id TEXT NOT NULL, player_id TEXT NOT NULL,
    key TEXT NOT NULL, value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (npc_id, player_id, key)
);

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id TEXT NOT NULL, player_id TEXT NOT NULL,
    content TEXT NOT NULL, created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    npc_id TEXT NOT NULL, player_id TEXT NOT NULL,
    affinity REAL NOT NULL DEFAULT 50,
    importance_acc REAL NOT NULL DEFAULT 0,   -- 距上次反思累计的重要性
    turns INTEGER NOT NULL DEFAULT 0,
    last_talk REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (npc_id, player_id)
);

CREATE TABLE IF NOT EXISTS inventory (
    player_id TEXT NOT NULL, item TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY (player_id, item)
);

CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL, giver TEXT NOT NULL, target TEXT NOT NULL,
    content TEXT NOT NULL, message TEXT NOT NULL,
    reward_item TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS world_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL, game_time TEXT DEFAULT '',
    created_at REAL NOT NULL, active_until REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DB:
    def __init__(self, path: Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def kv_get(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))


_db: Optional[DB] = None


def get_db() -> DB:
    global _db
    if _db is None:
        from .config import settings
        _db = DB(settings.DB_PATH)
    return _db


def set_db(db: DB) -> None:
    global _db
    _db = db
