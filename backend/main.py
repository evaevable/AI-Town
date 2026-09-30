"""合租小屋后端入口：python main.py 或 uvicorn main:app --reload"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import api
from app.config import settings
from app.llm import LLMClient
from app.registry import Registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

ADMIN_DIR = Path(__file__).resolve().parent / "admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n" + "=" * 58)
    print("🏠 合租小屋后端启动中…")
    print("=" * 58)
    registry = Registry(settings.NPCS_FILE, settings.GAME_FILE)
    llm = LLMClient()
    api.setup(registry, llm)
    if llm.mock:
        print("⚠️  当前为 Mock 模式：在 backend/.env 里配置 LLM_BASE_URL 后重启即可用真实模型")
    else:
        print(f"✅ 模型：{settings.LLM_MODEL} @ {settings.LLM_BASE_URL}")
    print(f"👥 NPC：{', '.join(n['name'] + '(' + n['title'] + ')' for n in registry.all())}")
    await api.env.world.start()
    print(f"📡 API: http://{settings.API_HOST}:{settings.API_PORT}   文档: /docs   管理页: /admin")
    print(f"💾 数据: {settings.DB_PATH}")
    print("=" * 58 + "\n")
    yield
    await api.env.world.stop()
    print("\n🛑 已关闭")


app = FastAPI(title="合租小屋 API", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(api.router)

if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.API_HOST, port=settings.API_PORT, reload=False,
                log_level="info")
