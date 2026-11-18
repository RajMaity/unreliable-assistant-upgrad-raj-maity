"""Async OpenRouter chat client (OpenAI-compatible API) built on httpx.

Design row 6 (failure mode): one jittered retry (≤40 ms) on 429/5xx or transport errors,
then LLMUnavailableError so the caller can fall back to the extractive answer.
The key is read from the environment only; it is never written to disk by this code.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any

import httpx

DEFAULT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"


class LLMError(Exception):
    """Any LLM failure."""


class LLMUnavailableError(LLMError):
    """Provider unreachable, rate-limited or 5xx after the single retry."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("OPENROUTER_API_KEY is empty")
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            transport=transport,
        )

    async def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 600) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        last: str = "no attempt"
        for attempt in range(2):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                last = f"transport: {exc.__class__.__name__}"
            else:
                if resp.status_code == 200:
                    try:
                        return str(resp.json()["choices"][0]["message"]["content"] or "")
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise LLMError(f"malformed provider response: {exc}") from exc
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = f"http {resp.status_code}"
                else:
                    # 4xx other than 429 is not retryable (bad key, guardrail, bad model)
                    raise LLMError(f"http {resp.status_code}: {resp.text[:200]}")
            if attempt == 0:
                await asyncio.sleep(random.uniform(0.020, 0.040))
        raise LLMUnavailableError(last)

    async def aclose(self) -> None:
        await self._client.aclose()


def client_from_env() -> OpenRouterClient:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    return OpenRouterClient(key, model)
