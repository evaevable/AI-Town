"""全局配置：从 backend/.env 读取，全部有默认值。"""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


class Settings:
    API_HOST = _env("API_HOST", "127.0.0.1")
    API_PORT = int(_env("API_PORT", "8000"))

    # LLM（OpenAI 兼容接口）。LLM_MOCK=1 或 LLM_BASE_URL 为空时使用离线 Mock
    LLM_BASE_URL = _env("LLM_BASE_URL", "http://175.27.225.51:4047/v1")
    LLM_API_KEY = _env("LLM_API_KEY", "EMPTY") or "EMPTY"
    LLM_MODEL = _env("LLM_MODEL", "echo-2.2.2")
    LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "60"))
    LLM_MOCK = _env("LLM_MOCK", "0") == "1" or not _env("LLM_BASE_URL", "x")
    LLM_MAX_CONCURRENCY = int(_env("LLM_MAX_CONCURRENCY", "4"))

    # 数据
    DATA_DIR = Path(_env("DATA_DIR", str(BACKEND_DIR / "data")))
    DB_PATH = Path(_env("DB_PATH", str(DATA_DIR / "town.db")))
    NPCS_FILE = Path(_env("NPCS_FILE", str(DATA_DIR / "npcs.yaml")))
    GAME_FILE = Path(_env("GAME_FILE", str(DATA_DIR / "game.yaml")))

    MAX_NPCS = 4

    # 世界
    WORLD_TICK_SECONDS = float(_env("WORLD_TICK_SECONDS", "1.0"))
    # 现实 1 秒 = 游戏多少分钟（默认 1 → 现实 1 分钟 = 游戏 1 小时）
    GAME_MINUTES_PER_SECOND = float(_env("GAME_MINUTES_PER_SECOND", "1.0"))
    WORLD_ENABLED = _env("WORLD_ENABLED", "1") == "1"

    # 记忆
    SHORT_TERM_TURNS = int(_env("SHORT_TERM_TURNS", "8"))
    RETRIEVE_TOP_K = int(_env("RETRIEVE_TOP_K", "5"))
    REFLECT_IMPORTANCE_SUM = float(_env("REFLECT_IMPORTANCE_SUM", "30"))


settings = Settings()
