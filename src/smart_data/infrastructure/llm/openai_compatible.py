from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from src.smart_data.config import settings

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LLMError(Exception):
    """本地大模型调用失败。"""


class OpenAICompatibleClient:
    """OpenAI 兼容 Chat Completions 客户端，强制 JSON 结构化输出。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_concurrency: int | None = None,
        reasoning_effort: str | None = None,
    ):
        self.base_url = (base_url or settings.llm.base_url).rstrip("/")
        self.api_key = api_key or settings.llm.api_key
        self.model = model or settings.llm.model
        self.timeout_seconds = timeout_seconds or settings.llm.timeout_seconds
        self.reasoning_effort = reasoning_effort or getattr(settings.llm, "reasoning_effort", "low")
        self._semaphore = asyncio.Semaphore(max_concurrency or settings.llm.max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        trace_id: str | None = None,
        operation: str = "chat",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "reasoning_effort": self.reasoning_effort,
            "messages": messages,
        }
        headers = {}
        if trace_id:
            headers["X-Request-ID"] = trace_id

        async with self._semaphore:
            try:
                response = await self._client.post("/chat/completions", json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise LLMError(f"模型服务超时: {operation}") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"模型服务不可用: {operation}") from exc

        if response.status_code >= 400:
            raise LLMError(f"模型服务 HTTP {response.status_code}: {response.text[:240]}")

        data = response.json()
        try:
            message = data["choices"][0]["message"]
        except Exception as exc:
            raise LLMError("模型响应缺少 choices.message") from exc

        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("reasoning_content") or "").strip()
        try:
            return parse_json_object(content)
        except ValueError as exc:
            raise LLMError(f"模型未返回合法 JSON: {operation}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = _FENCE_RE.sub("", (text or "").strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("未找到 JSON 对象")
    snippet = cleaned[start : end + 1]
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        parsed = json.loads(snippet.replace("\\'", "'"))
    if not isinstance(parsed, dict):
        raise ValueError("JSON 根节点必须是对象")
    return parsed


def llm_enabled() -> bool:
    cfg = settings.llm
    if not getattr(cfg, "enabled", True):
        return False
    key = (cfg.api_key or "").strip()
    return key.lower() not in {"", "empty", "changeme", "your_api_key"}


_client: OpenAICompatibleClient | None = None


def get_llm_client() -> OpenAICompatibleClient | None:
    global _client
    if not llm_enabled():
        return None
    if _client is None:
        _client = OpenAICompatibleClient()
    return _client
