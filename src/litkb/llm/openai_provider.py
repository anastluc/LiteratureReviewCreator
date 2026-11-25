from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import httpx
from openai import OpenAI

from .base import Message


class OpenAICompatibleProvider:
    """OpenAI, Ollama, LM Studio, vLLM... anything speaking the OpenAI chat API."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None,
                 headers: dict[str, str] | None = None, http_client: httpx.Client | None = None):
        self.model = model
        self.client = OpenAI(base_url=base_url, default_headers=headers, http_client=http_client,
                             api_key=api_key or os.environ.get("OPENAI_API_KEY") or "not-needed")

    @staticmethod
    def _msgs(messages, system):
        return ([{"role": "system", "content": system}] if system else []) + list(messages)

    def _params(self, messages, system, max_tokens: int, temperature: float) -> dict[str, Any]:
        """Request parameters; `max_tokens` is the budget for the visible answer."""
        return dict(model=self.model, messages=self._msgs(messages, system), max_tokens=max_tokens,
                    temperature=temperature)

    def complete(self, messages: list[Message], *, system=None, max_tokens=4096, temperature=0.2) -> str:
        r = self.client.chat.completions.create(**self._params(messages, system, max_tokens, temperature))
        choice = r.choices[0]
        text = choice.message.content or ""
        if not text.strip() and choice.finish_reason == "length":
            raise RuntimeError(f"{self.model} hit its token limit before writing an answer (all tokens went to "
                               "reasoning); raise [llm.openrouter].reasoning_max_tokens or max_tokens")
        return text

    def stream(self, messages: list[Message], *, system=None, max_tokens=4096,
               temperature=0.2) -> Iterator[str]:
        r = self.client.chat.completions.create(**self._params(messages, system, max_tokens, temperature),
                                                stream=True)
        for chunk in r:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter (OpenAI-compatible API; model ids like `google/gemini-4-hydrogen`). Needs OPENROUTER_API_KEY.

    Reasoning models count hidden reasoning against `max_tokens`, so a reasoning budget is requested separately
    and added on top: the caller's `max_tokens` stays the budget for the visible answer. Reasoning text is
    excluded from responses (it is still billed). `reasoning_max_tokens = 0` leaves reasoning to the model."""

    def __init__(self, model: str, base_url: str = "https://openrouter.ai/api/v1", reasoning_max_tokens: int = 0,
                 http_client: httpx.Client | None = None):
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set: add it to .env (https://openrouter.ai/keys)")
        self.reasoning_max_tokens = reasoning_max_tokens
        # X-Title names the app in OpenRouter's usage dashboard; nothing user-specific is sent.
        super().__init__(model, base_url, api_key=key, headers={"X-Title": "litkb"}, http_client=http_client)

    def _params(self, messages, system, max_tokens, temperature):
        p = super()._params(messages, system, max_tokens, temperature)
        if self.reasoning_max_tokens > 0:
            p["max_tokens"] = max_tokens + self.reasoning_max_tokens
            p["extra_body"] = {"reasoning": {"max_tokens": self.reasoning_max_tokens, "exclude": True}}
        return p
