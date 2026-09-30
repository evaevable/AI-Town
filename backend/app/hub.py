"""WebSocket 广播中心：游戏客户端订阅所有事件。"""

import asyncio
import json
import logging
from typing import Any, Dict, List

from fastapi import WebSocket

log = logging.getLogger("hub")


class Hub:
    def __init__(self) -> None:
        self.clients: List[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.clients.append(ws)
        log.info("客户端接入，当前 %d 个连接", len(self.clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self.lock:
            if ws in self.clients:
                self.clients.remove(ws)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(payload, ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


hub = Hub()
