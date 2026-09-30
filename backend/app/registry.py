"""NPC 注册表 + 游戏配置。npcs.yaml 是真源，管理页修改后写回文件并热加载。"""

import copy
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config import settings

SPRITES = ["character_2", "character_3", "character_4", "character_1"]

DEFAULT_BEHAVIOR = {
    "wander": True, "wander_radius": 60, "move_speed": 60,
    "chattiness": 0.5, "can_chat_with_npcs": True, "monologue": True,
}

DEFAULT_NPC = {
    "id": "", "name": "", "title": "", "gender": "", "age": 0,
    "sprite": "character_2", "tint": "#ffffff", "spawn": "走廊",
    "personality": "", "expertise": "", "hobbies": "", "speech_style": "",
    "catchphrases": [], "backstory": "", "relationships": {}, "secrets": [],
    "likes": [], "dislikes": [],
    "temperature": 0.85, "max_reply_chars": 60, "tone": "正常", "tone": "正常",
    "prompt_mode": "template", "system_prompt": "",
    "behavior": DEFAULT_BEHAVIOR, "schedule": [],
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ValidationError(ValueError):
    pass


def _load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class GameConfig:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: Dict[str, Any] = _load_yaml(self.path)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def locations(self) -> Dict[str, str]:
        return self.data.get("locations", {})

    def rand(self, key: str, default: float) -> float:
        return float(self.data.get("randomness", {}).get(key, default))

    def affinity_level(self, affinity: float) -> Dict[str, Any]:
        for lv in self.data["affinity_levels"]:
            if affinity >= lv["min"]:
                return lv
        return self.data["affinity_levels"][-1]

    def level_rank(self, name: str) -> int:
        """等级序号：陌生=0 … 挚友=4"""
        names = [lv["name"] for lv in reversed(self.data["affinity_levels"])]
        return names.index(name) if name in names else 0


def normalize_npc(raw: Dict[str, Any], locations: Dict[str, str],
                  default_tone: str = "正常") -> Dict[str, Any]:
    npc = copy.deepcopy(DEFAULT_NPC)
    npc["tone"] = default_tone
    npc.update({k: v for k, v in (raw or {}).items() if v is not None})
    npc["behavior"] = {**DEFAULT_BEHAVIOR, **(raw.get("behavior") or {})}

    if not _ID_RE.match(str(npc["id"])):
        raise ValidationError(f"id 只能用小写字母开头的字母/数字/下划线：{npc['id']!r}")
    if not str(npc["name"]).strip():
        raise ValidationError("name 不能为空")
    if npc.get("tone") not in ("温和", "正常", "泼辣"):
        raise ValidationError("tone 只能是 温和 / 正常 / 泼辣")
    if npc.get("tone") not in ("温和", "正常", "泼辣"):
        raise ValidationError("tone 只能是 温和 / 正常 / 泼辣")
    if npc["prompt_mode"] not in ("template", "custom"):
        raise ValidationError("prompt_mode 只能是 template 或 custom")
    if npc["prompt_mode"] == "custom" and not str(npc["system_prompt"]).strip():
        raise ValidationError("custom 模式必须填写 system_prompt")
    if npc["sprite"] not in SPRITES:
        raise ValidationError(f"sprite 必须是 {SPRITES} 之一")
    if npc["spawn"] not in locations:
        raise ValidationError(f"spawn 地点不存在：{npc['spawn']}")
    npc["temperature"] = max(0.0, min(1.5, float(npc["temperature"])))
    npc["max_reply_chars"] = max(10, min(300, int(npc["max_reply_chars"])))
    b = npc["behavior"]
    b["chattiness"] = max(0.0, min(1.0, float(b["chattiness"])))
    b["wander_radius"] = max(0, min(300, int(b["wander_radius"])))
    b["move_speed"] = max(10, min(300, int(b["move_speed"])))
    for key in ("wander", "can_chat_with_npcs", "monologue"):
        b[key] = bool(b[key])

    sched = []
    for s in npc.get("schedule") or []:
        t = str(s.get("time", "")).strip()
        if not _TIME_RE.match(t):
            raise ValidationError(f"日程时间格式应为 HH:MM：{t!r}")
        if s.get("location") not in locations:
            raise ValidationError(f"日程地点不存在：{s.get('location')}")
        sched.append({"time": t, "location": s["location"], "activity": str(s.get("activity", "")).strip()})
    npc["schedule"] = sorted(sched, key=lambda s: s["time"])

    for key in ("catchphrases", "likes", "dislikes"):
        v = npc[key]
        npc[key] = [x.strip() for x in (v.split("\n") if isinstance(v, str) else v) if str(x).strip()]
    secrets = []
    for s in npc.get("secrets") or []:
        if isinstance(s, dict) and str(s.get("content", "")).strip():
            secrets.append({"level": s.get("level", "挚友"), "content": s["content"].strip()})
    npc["secrets"] = secrets
    if not isinstance(npc.get("relationships"), dict):
        npc["relationships"] = {}
    return npc


class Registry:
    def __init__(self, npcs_file: Path, game_file: Path):
        self.npcs_file = Path(npcs_file)
        self.game = GameConfig(game_file)
        self._lock = threading.RLock()
        self._npcs: Dict[str, Dict[str, Any]] = {}
        self.version = 0
        self.reload()

    def reload(self) -> None:
        data = _load_yaml(self.npcs_file)
        tone_default = str(self.game.get("default_tone", "正常"))
        npcs = [normalize_npc(n, self.game.locations, tone_default) for n in data.get("npcs", [])]
        self._validate_set(npcs)
        with self._lock:
            self._npcs = {n["id"]: n for n in npcs}
            self.version += 1

    def _validate_set(self, npcs: List[Dict[str, Any]]) -> None:
        if len(npcs) > settings.MAX_NPCS:
            raise ValidationError(f"NPC 最多 {settings.MAX_NPCS} 个")
        ids = [n["id"] for n in npcs]
        names = [n["name"] for n in npcs]
        if len(set(ids)) != len(ids):
            raise ValidationError("NPC id 重复")
        if len(set(names)) != len(names):
            raise ValidationError("NPC 名字重复")

    def _save(self, npcs: List[Dict[str, Any]]) -> None:
        self._validate_set(npcs)
        header = ""
        text = self.npcs_file.read_text(encoding="utf-8") if self.npcs_file.exists() else ""
        for line in text.splitlines():  # 保留文件头注释
            if line.startswith("#") or not line.strip():
                header += line + "\n"
            else:
                break
        body = yaml.safe_dump({"npcs": npcs}, allow_unicode=True, sort_keys=False, width=1000)
        tmp = self.npcs_file.with_suffix(".tmp")
        tmp.write_text(header + body, encoding="utf-8")
        tmp.replace(self.npcs_file)
        with self._lock:
            self._npcs = {n["id"]: n for n in npcs}
            self.version += 1

    # ---------- 查询 ----------
    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(n) for n in self._npcs.values()]

    def get(self, npc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            n = self._npcs.get(npc_id)
            return copy.deepcopy(n) if n else None

    def by_name(self, name: str) -> Optional[Dict[str, Any]]:
        for n in self.all():
            if n["name"] == name:
                return n
        return None

    def resolve(self, key: str) -> Optional[Dict[str, Any]]:
        return self.get(key) or self.by_name(key)

    # ---------- 修改 ----------
    def upsert(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        npc = normalize_npc(raw, self.game.locations, str(self.game.get("default_tone", "正常")))
        npcs = self.all()
        idx = next((i for i, n in enumerate(npcs) if n["id"] == npc["id"]), None)
        if idx is None:
            if len(npcs) >= settings.MAX_NPCS:
                raise ValidationError(f"NPC 最多 {settings.MAX_NPCS} 个，请先删除一个")
            npcs.append(npc)
        else:
            npcs[idx] = npc
        self._save(npcs)
        return npc

    def delete(self, npc_id: str) -> bool:
        npcs = self.all()
        left = [n for n in npcs if n["id"] != npc_id]
        if len(left) == len(npcs):
            return False
        self._save(left)
        return True

    def public(self, npc: Dict[str, Any]) -> Dict[str, Any]:
        """给游戏客户端的精简信息"""
        return {k: npc[k] for k in ("id", "name", "title", "sprite", "tint", "spawn", "behavior")}


_registry: Optional[Registry] = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(settings.NPCS_FILE, settings.GAME_FILE)
    return _registry


def set_registry(reg: Registry) -> None:
    global _registry
    _registry = reg
