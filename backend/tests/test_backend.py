"""后端全流程测试（Mock 模型，不联网）。"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.config import settings
from app.db import DB, set_db
from app.llm import LLMClient
from app.memory import MemoryStore, similarity, tokenize
from app.registry import Registry, ValidationError, normalize_npc

BACKEND = Path(__file__).resolve().parent.parent
DATA = BACKEND / "data"


@pytest.fixture()
def env(tmp_path):
    import shutil
    for name in ("npcs.yaml", "game.yaml"):
        shutil.copy(DATA / name, tmp_path / name)
    settings.DB_PATH = tmp_path / "t.db"
    settings.NPCS_FILE = tmp_path / "npcs.yaml"
    settings.GAME_FILE = tmp_path / "game.yaml"
    set_db(DB(settings.DB_PATH))
    registry = Registry(settings.NPCS_FILE, settings.GAME_FILE)
    api.setup(registry, LLMClient(mock=True))
    yield api.env
    set_db(None)  # type: ignore[arg-type]


def test_registry_loads_three_npcs(env):
    npcs = env.registry.all()
    assert [n["name"] for n in npcs] == ["柒柒", "泽不易", "谦谦"]
    assert all(n["sprite"] in ("character_1", "character_2", "character_3", "character_4") for n in npcs)
    assert all(n["schedule"] for n in npcs)


def test_max_four_npcs(env):
    base = env.registry.all()[0]
    for i in range(1):
        env.registry.upsert({**base, "id": f"extra{i}", "name": f"测试{i}", "spawn": "走廊"})
    assert len(env.registry.all()) == 4
    with pytest.raises(ValidationError):
        env.registry.upsert({**base, "id": "extra9", "name": "测试9"})


def test_normalize_rejects_bad_data(env):
    with pytest.raises(ValidationError):
        normalize_npc({"id": "BadID", "name": "x"}, env.registry.game.locations)
    with pytest.raises(ValidationError):
        normalize_npc({"id": "ok", "name": "x", "prompt_mode": "custom", "system_prompt": " "},
                      env.registry.game.locations)
    with pytest.raises(ValidationError):
        normalize_npc({"id": "ok", "name": "x", "spawn": "不存在的地方"}, env.registry.game.locations)


def test_tokenize_and_similarity():
    assert "你好" in tokenize("你好世界")
    assert similarity("今天天气不错", "今天天气不错") == 1.0
    assert similarity("今天天气不错", "我在打游戏") < 0.2


def test_chat_flow_writes_memory(env):
    npc = env.registry.by_name("柒柒")
    events = []

    async def run():
        async for e in env.dialogue.chat(npc["id"], "player", "你好，我叫 Lance，我喜欢喝咖啡"):
            events.append(e)

    asyncio.run(run())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "chat_start" and kinds[-1] == "chat_end"
    end = events[-1]
    assert end["text"] and end["affinity"] > 50
    # 记忆：短期窗口 + 情景记忆都写进去了
    assert len(env.store.recent_turns(npc["id"], "player", 4)) == 2
    assert env.store.list_episodes(npc["id"], "player")
    # 检索能召回
    hits = env.store.retrieve(npc["id"], "player", "咖啡", k=3)
    assert hits and hits[0]["score"] > 0


def test_retrieve_scores_have_components(env):
    npc = env.registry.all()[0]
    env.store.add_episode(npc["id"], "player", "玩家说喜欢下雨天，还养了一只叫豆豆的猫", importance=7)
    hits = env.store.retrieve(npc["id"], "player", "猫", k=3, touch=False)
    assert hits[0]["importance"] == 7
    for key in ("relevance", "recency", "score"):
        assert key in hits[0]


def test_reflection_and_facts(env):
    npc = env.registry.all()[0]
    env.store.upsert_fact(npc["id"], "player", "名字", "Lance")
    for i in range(6):
        env.store.add_episode(npc["id"], "player", f"第{i}次聊天，聊得很开心", importance=7)
    acc = env.store.bump_turn(npc["id"], "player", 40)
    assert acc >= settings.REFLECT_IMPORTANCE_SUM
    text = asyncio.run(env.dialogue._reflect(npc["id"], "player"))
    assert text and env.store.reflections(npc["id"], "player")
    assert env.store.relation(npc["id"], "player")["importance_acc"] == 0


def test_gift_changes_affinity_by_like(env):
    qiqi = env.registry.by_name("柒柒")
    before = env.store.relation(qiqi["id"], "player")["affinity"]
    liked = asyncio.run(env.dialogue.give_gift(qiqi["id"], "player", "蛋糕"))
    assert liked["liked"] is True and liked["text"]
    assert liked["affinity"] > before
    disliked = env.dialogue.registry.by_name("泽不易")
    r = asyncio.run(env.dialogue.give_gift(disliked["id"], "player", "花"))
    assert r["disliked"] is True and r["affinity_change"] < 0


def test_world_schedule_and_monologue(env):
    world = env.world
    world.set_clock(time_str="09:00")
    world._apply_schedules(force=True)
    qiqi = env.registry.by_name("柒柒")
    assert world.snapshot(qiqi["id"])["location"] == "客厅"
    world.set_clock(time_str="21:00")
    world._apply_schedules(force=True)
    assert world.snapshot(qiqi["id"])["location"] == "电脑桌"
    lines = asyncio.run(world.generate_monologues())
    assert lines and all(v for v in lines.values())


def test_npc_chat_writes_gossip(env):
    world = env.world
    a, b = env.registry.all()[1], env.registry.all()[2]
    world._state[a["id"]]["location"] = world._state[b["id"]]["location"] = "走廊"
    lines = asyncio.run(world.npc_chat(a["id"], b["id"]))
    assert lines
    assert env.store.db.query("SELECT * FROM episodes WHERE kind='gossip'")
    assert env.store.db.query("SELECT * FROM episodes WHERE kind='npc_chat'")


def test_director_direct_and_event(env):
    world = env.world
    npc = env.registry.all()[0]

    async def run():
        r = await world.direct(npc["id"], location="厨房", activity="切菜", say="今晚吃咖喱")
        e = await world.trigger_event("外面下起了大雨")
        return r, e

    r, e = asyncio.run(run())
    assert r["ok"] and r["state"]["location"] == "厨房"
    assert world.snapshot(npc["id"])["event"] == "外面下起了大雨"
    assert e["content"] == "外面下起了大雨"


def test_behavior_override(env):
    world = env.world
    npc = env.registry.all()[0]
    world.set_behavior(npc["id"], monologue=False, can_chat_with_npcs=False)
    b = world.behavior(env.registry.get(npc["id"]))
    assert b["monologue"] is False and b["can_chat_with_npcs"] is False


def test_api_endpoints(env):
    with TestClient(_app()) as client:
        assert client.get("/health").json()["status"] == "ok"
        npcs = client.get("/api/npcs").json()
        assert npcs["total"] == 3 and npcs["max"] == 4
        npc_id = npcs["npcs"][0]["id"]
        detail = client.get(f"/api/npcs/{npc_id}").json()
        assert detail["npc"]["name"] == "柒柒" and "stats" in detail
        prompt = client.get(f"/api/npcs/{npc_id}/prompt", params={"message": "你好"}).json()
        assert "柒柒" in prompt["system_prompt"]
        # 改人设：立刻生效
        npc = detail["npc"]
        npc["personality"] = "非常暴躁"
        assert client.put(f"/api/npcs/{npc_id}", json=npc).status_code == 200
        assert env.registry.get(npc_id)["personality"] == "非常暴躁"
        # 记忆接口
        chat = client.post("/api/chat", json={"npc_id": npc_id, "message": "你好呀"}).json()
        assert chat["text"]
        mem = client.get(f"/api/npcs/{npc_id}/memory").json()
        assert mem["episodes"] and mem["stats"]["turns"] >= 2
        recall = client.get(f"/api/npcs/{npc_id}/recall", params={"query": "你好"}).json()
        assert recall["results"]
        # 导演接口
        world = client.get("/api/world").json()
        assert world["clock"]["time"]
        assert client.post("/api/world/clock", json={"paused": True, "speed": 2}).json()["paused"] is True
        assert client.post("/api/world/direct", json={"npc_id": npc_id, "location": "厨房"}).json()["ok"]
        assert client.post("/api/world/event", json={"content": "有人按门铃"}).json()["content"]
        assert client.post("/api/world/behavior", json={"npc_id": npc_id, "wander": False}).json()["ok"]
        # 玩家状态
        player = client.get("/api/player").json()
        assert player["inventory"] and len(player["affinities"]) == 3


def test_websocket_chat_and_gift(env):
    with TestClient(_app()) as client:
        with client.websocket_connect("/ws") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "welcome" and len(welcome["npcs"]) == 3
            assert ws.receive_json()["type"] == "inventory"
            assert ws.receive_json()["type"] == "quests"
            ws.send_json({"type": "chat", "npc_id": "qiqi", "message": "你好"})
            seen = []
            while True:
                msg = ws.receive_json()
                seen.append(msg["type"])
                if msg["type"] == "chat_end":
                    assert msg["text"] and "reply_options" in msg
                    break
            assert "chat_delta" in seen and "chat_start" in seen
            ws.send_json({"type": "gift", "npc_id": "qiqi", "item": "蛋糕"})
            while True:
                msg = ws.receive_json()
                if msg["type"] == "gift_result":
                    assert msg["liked"] is True
                    break
            ws.send_json({"type": "near", "npc_id": "qiqi"})
            ws.send_json({"type": "ping"})
            while True:
                if ws.receive_json()["type"] == "pong":
                    break


def _app():
    from main import app
    return app
