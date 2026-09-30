"""LLM 客户端：OpenAI 兼容接口 + 离线 Mock。

- 所有调用都是 async，并用信号量限制并发，避免把模型服务打满
- httpx 设 trust_env=False：不走系统代理（内网模型服务直连）
- 自动去掉 <think>...</think> 和开头空白
"""

import asyncio
import json
import logging
import random
import re
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import settings

log = logging.getLogger("llm")

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def clean_text(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    return text.strip().strip('"').strip()


def extract_json(text: str) -> Optional[Any]:
    """从模型输出中尽力抽出 JSON（支持 ```json 代码块、前后有废话）。"""
    text = _THINK_RE.sub("", text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


class LLMClient:
    def __init__(self, mock: Optional[bool] = None):
        self.mock = settings.LLM_MOCK if mock is None else mock
        self._sem = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
        self._client = None
        self.calls = 0  # 调用计数（测试、统计用）
        if not self.mock:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                timeout=settings.LLM_TIMEOUT,
                max_retries=1,
                http_client=httpx.AsyncClient(trust_env=False, timeout=settings.LLM_TIMEOUT),
            )

    @property
    def model(self) -> str:
        return "mock" if self.mock else settings.LLM_MODEL

    def _params(self, temperature: float, max_tokens: int, top_p: float) -> Dict[str, Any]:
        return dict(
            model=settings.LLM_MODEL,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            extra_body={"top_k": 20},
        )

    async def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.8,
        max_tokens: int = 400,
        top_p: float = 0.95,
        mock_text: Optional[str] = None,
    ) -> str:
        self.calls += 1
        if self.mock:
            await asyncio.sleep(0)
            return mock_text if mock_text is not None else _mock_reply(messages)
        # 推理型模型（如 echo-2.2.2）会先花掉一批 token 做思考，max_tokens 太小会导致正文为空
        max_tokens = max(512, max_tokens)
        async with self._sem:
            resp = await self._client.chat.completions.create(
                messages=messages, **self._params(temperature, max_tokens, top_p)
            )
        text = clean_text(resp.choices[0].message.content or "")
        if not text:  # 兜底重试一次，给足预算
            async with self._sem:
                resp = await self._client.chat.completions.create(
                    messages=messages, **self._params(temperature, 1024, top_p)
                )
            text = clean_text(resp.choices[0].message.content or "")
        return text

    async def stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.8,
        max_tokens: int = 400,
        top_p: float = 0.95,
        mock_text: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """逐段产出文本。开头的空白会被吞掉。"""
        self.calls += 1
        if self.mock:
            text = mock_text if mock_text is not None else _mock_reply(messages)
            for i in range(0, len(text), 3):
                await asyncio.sleep(0.01)
                yield text[i:i + 3]
            return
        async with self._sem:
            stream = await self._client.chat.completions.create(
                messages=messages, stream=True, **self._params(temperature, max(400, max_tokens), top_p)
            )
            started = False
            in_think = False
            async for chunk in stream:
                if not chunk.choices:
                    continue
                piece = chunk.choices[0].delta.content or ""
                if not piece:
                    continue
                # 简单处理流式里的 <think> 块
                if "<think>" in piece:
                    in_think = True
                    piece = piece.split("<think>")[0]
                if in_think:
                    if "</think>" in piece:
                        in_think = False
                        piece = piece.split("</think>", 1)[1]
                    else:
                        continue
                if not started:
                    piece = piece.lstrip()
                    if not piece:
                        continue
                    started = True
                yield piece

    async def complete_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 600,
        mock_value: Any = None,
    ) -> Optional[Any]:
        if self.mock:
            self.calls += 1
            return mock_value
        for attempt in range(2):
            try:
                text = await self.complete(messages, temperature=temperature, max_tokens=max_tokens)
            except Exception as e:  # 网络等错误
                log.warning("LLM JSON 调用失败: %s", e)
                return None
            data = extract_json(text)
            if data is not None:
                return data
            log.warning("JSON 解析失败(第%d次): %s", attempt + 1, text[:120])
        return None


_MOCK_LINES = [
    "哈哈，你说得对！",
    "嗯……让我想想，这个问题挺有意思的。",
    "真的吗？快跟我多说说！",
    "今天有点累，不过看到你还挺开心的。",
    "诶，你上次说的那件事后来怎么样了？",
]


def _mock_reply(messages: List[Dict[str, str]]) -> str:
    system = messages[0]["content"] if messages else ""
    m = re.search(r"你是(\S{1,6}?)[，,。]", system)
    who = m.group(1) if m else "我"
    return f"（{who}）{random.choice(_MOCK_LINES)}"


_llm: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


def set_llm(client: LLMClient) -> None:
    """测试用：注入自定义客户端。"""
    global _llm
    _llm = client
