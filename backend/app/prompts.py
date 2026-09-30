"""提示词组装：system prompt、上下文块、分析/独白/互聊提示。"""

from typing import Any, Dict, List, Optional

from .memory import MemoryStore

PLACEHOLDERS = [
    "{name}", "{title}", "{mood}", "{affinity}", "{affinity_level}", "{game_time}",
    "{location}", "{activity}", "{facts}", "{reflection}", "{secrets}", "{event}",
    "{player_name}", "{roommates}",
]


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "（无）"


def relationship_lines(npc: Dict[str, Any], others: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for o in others:
        if o["id"] == npc["id"]:
            continue
        note = (npc.get("relationships") or {}).get(o["name"], "")
        lines.append(f"{o['name']}（{o['title']}）{'：' + note if note else ''}")
    return lines


def facts_block(store: MemoryStore, npc_id: str, player_id: str, player_name: str) -> str:
    rows = store.facts(npc_id, player_id)
    lines = [f"玩家自称{player_name}" if player_name and player_name != "你" else ""]
    lines += [f"{r['key']}：{r['value']}" for r in rows]
    return "\n".join(f"- {l}" for l in lines if l) or "（你还不太了解这个人）"


def memories_block(episodes: List[Dict[str, Any]]) -> str:
    if not episodes:
        return ""
    parts = []
    for e in episodes:
        tag = {"gift": "送礼", "quest": "委托", "npc_chat": "你和室友聊天", "gossip": "听说的",
               "event": "发生的事"}.get(e["kind"], "对话")
        src = f"（{e['source']}说的）" if e.get("source") else ""
        parts.append(f"[{tag}] {e['content']}{src}")
    return "\n".join(parts)


def secrets_block(npc: Dict[str, Any], level_rank: int, levels: List[Dict[str, Any]]) -> str:
    out = []
    for s in npc.get("secrets") or []:
        rank = next((i for i, lv in enumerate(levels) if lv["name"] == s["level"]), len(levels) - 1)
        if level_rank >= rank:
            out.append(s["content"])
    return "\n".join(f"- {o}" for o in out)


def build_system_prompt(
    npc: Dict[str, Any],
    store: MemoryStore,
    player_id: str,
    *,
    all_npcs: List[Dict[str, Any]],
    affinity: float,
    affinity_level: Dict[str, Any],
    level_rank: int,
    levels: List[Dict[str, Any]],
    game_time: str,
    location: str,
    activity: str,
    mood: str,
    player_name: str,
    event: str = "",
    topic_seed: str = "",
    memories: Optional[List[Dict[str, Any]]] = None,
) -> str:
    facts = facts_block(store, npc["id"], player_id, player_name)
    reflection_rows = store.reflections(npc["id"], player_id, limit=2)
    reflection = "\n".join(f"- {r['content']}" for r in reflection_rows) or "（还没有形成整体印象）"
    secrets = secrets_block(npc, level_rank, levels) or "（暂时没有可以透露的心里话）"
    roommates = relationship_lines(npc, all_npcs)
    mem_text = memories_block(memories or []) or "（暂时想不起什么）"
    extra = []
    if event:
        extra.append(f"【现在正在发生的事】{event}")
    if topic_seed:
        extra.append(f"【你今天脑子里一直想着】{topic_seed}（聊天时可以自然地带出来，也可以不提）")

    if npc.get("prompt_mode") == "custom" and npc.get("system_prompt", "").strip():
        text = npc["system_prompt"]
        mapping = {
            "{name}": npc["name"], "{title}": npc["title"], "{mood}": mood,
            "{affinity}": f"{affinity:.0f}", "{affinity_level}": affinity_level["name"],
            "{game_time}": game_time, "{location}": location, "{activity}": activity,
            "{facts}": facts, "{reflection}": reflection, "{secrets}": secrets,
            "{event}": event or "（无）", "{player_name}": player_name,
            "{roommates}": "\n".join(roommates),
        }
        for k, v in mapping.items():
            text = text.replace(k, str(v))
        return f"{text}\n\n【当前状态】{game_time}，你在{location}{activity}，心情{mood}。\n" \
               f"【与玩家关系】{affinity_level['name']}（好感度 {affinity:.0f}/100）：{affinity_level['style']}\n" \
               f"【关于玩家】\n{facts}\n【你记得的事】\n{mem_text}\n{chr(10).join(extra)}"

    limit = npc.get("max_reply_chars", 60)
    parts = [
        f"你是{npc['name']}，{npc['age']}岁，{npc['title']}，住在一间合租小屋里。",
        "",
        "【你这个人】",
        f"- 性格：{npc['personality']}",
        f"- 擅长：{npc['expertise']}",
        f"- 爱好：{npc['hobbies']}",
        f"- 说话风格：{npc['speech_style']}",
        f"- 口头禅：{'、'.join(npc['catchphrases']) if npc['catchphrases'] else '（无）'}",
        f"- 你的背景：{npc['backstory']}",
        "",
        "【屋里其他人】",
        _bullets(roommates),
        "",
        "【你现在的状态】",
        f"- 现在是 {game_time}，你在{location}{activity}，心情{mood}",
        "",
        f"【你和玩家（{player_name}）的关系】",
        f"- {affinity_level['name']}，好感度 {affinity:.0f}/100",
        f"- 语气要求：{affinity_level['style']}",
        "",
        "【你知道的关于玩家的事】",
        facts,
        "",
        "【你对玩家的整体印象】",
        reflection,
        "",
        "【你可以透露的心里话（只在对的人、对的时机说，不要硬塞）】",
        secrets,
        "",
        "【你记得的相关片段】",
        mem_text,
    ]
    if extra:
        parts += [""] + extra
    parts += [
        "",
        "【怎么说话】",
        f"1. 用第一人称，像真人聊天，控制在 {limit} 字以内",
        "2. 可以有情绪、有态度，可以反问、可以吐槽、可以主动挑起话题",
        "3. 需要时可以用括号写一点动作或语气，例如（打了个哈欠），但不要每句都写",
        "4. 不要机械地重复对方的话，不要每句都提到自己的职业",
        "5. 绝对不要说自己是 AI、模型或程序；不要在回复前加自己的名字",
        "6. 不知道的事就自然地说不知道，不要编造与【你知道的事】矛盾的内容",
        "",
        "直接输出你要说的话：",
    ]
    return "\n".join(parts)


def build_messages(system_prompt: str, turns: List[Dict[str, Any]], message: str) -> List[Dict[str, str]]:
    msgs = [{"role": "system", "content": system_prompt}]
    for t in turns:
        role = "user" if t["role"] == "player" else "assistant"
        msgs.append({"role": role, "content": t["content"]})
    msgs.append({"role": "user", "content": message})
    return msgs


ANALYSIS_SYSTEM = """你是一个对话分析器。根据 NPC 和玩家的这一轮对话，输出 JSON（不要任何其他文字）：
{
  "affinity_change": -15 到 +8 的整数,   // 玩家态度友好/赞美/关心/送礼=正；冒犯/贬低/恶意=负；普通闲聊=0
  "importance": 1 到 10 的整数,          // 这条记忆对 NPC 有多重要（日常寒暄 2-4，个人信息/情感/承诺 6-9）
  "emotion": "开心|害羞|惊讶|生气|难过|无语|得意|思考|平静",
  "memory": "用第三人称一句话总结这轮对话（不超过40字，保留关键信息）",
  "facts": [{"key": "不超过6个字的字段名", "value": "关于玩家的稳定信息，没有就给空数组"}],  // 例如 名字/职业/爱好/喜好/经历/约定
  "reply_options": ["玩家接下来可能说的3句话，各不超过12字"],
  "reason": "10字以内的理由"
}
只输出 JSON。"""


def build_analysis_messages(npc_name: str, message: str, reply: str, known_facts: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user", "content": f"NPC：{npc_name}\n已知关于玩家的信息：\n{known_facts}\n\n"
                                    f"玩家说：{message}\n{npc_name}回答：{reply}"},
    ]


def build_reflection_messages(npc_name: str, player_name: str, episodes: List[Dict[str, Any]],
                              facts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    mem = "\n".join(f"- {e['content']}" for e in episodes)
    fac = "\n".join(f"- {f['key']}：{f['value']}" for f in facts) or "（无）"
    return [
        {"role": "system", "content": "你负责帮 NPC 整理对某个人的整体印象。输出 2 句话（每句不超过40字），"
                                      "写 NPC 心里怎么看待这个人、关系处在什么状态。只输出这两句话。"},
        {"role": "user", "content": f"NPC：{npc_name}\n对象：{player_name}\n\n最近发生的事：\n{mem}\n\n"
                                    f"已知信息：\n{fac}"},
    ]


def build_monologue_prompt(npcs: List[Dict[str, Any]], context: str) -> List[Dict[str, str]]:
    lines = []
    for n in npcs:
        lines.append(f"- {n['name']}（{n['title']}，{n['personality']}）：在{n['location']}{n['activity']}")
    return [
        {"role": "system", "content": "你为合租小屋里的几个角色写此刻的独白。"
                                      "每个角色一句话，20-40字，像在自言自语或者随口抱怨、感慨，"
                                      "符合各自性格和正在做的事，要有生活气息。"
                                      '严格输出 JSON：{"名字": "一句话", ...}，不要其他文字。'},
        {"role": "user", "content": f"当前场景：{context}\n\n角色：\n" + "\n".join(lines)},
    ]


def build_npc_chat_prompt(a: Dict[str, Any], b: Dict[str, Any], context: str,
                          share: str = "") -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": "你写一小段合租屋里两个室友的闲聊，2 到 4 句，交替说话，"
                                      "口语化、有性格差异、可以互相吐槽。"
                                      '严格输出 JSON 数组：[{"speaker": "名字", "line": "台词"}, ...]，不要其他文字。'},
        {"role": "user", "content": f"场景：{context}\n\n{a['name']}（{a['title']}，{a['personality']}，"
                                    f"说话风格：{a['speech_style']}）\n{b['name']}（{b['title']}，{b['personality']}，"
                                    f"说话风格：{b['speech_style']}）\n"
                                    f"{('他们共同知道的事：' + share) if share else ''}"},
    ]


GREETING_SYSTEM = """你扮演一个游戏角色，正在主动跟刚走过来的玩家搭一句话。
要求：一句话，20 字以内，符合角色性格和当前在做的事，可以带一点情绪或提问，不要寒暄套话。
只输出这句话本身。"""
