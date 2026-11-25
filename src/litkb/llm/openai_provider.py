from __future__ import annotations

import os
from collections.abc import Iterator

from openai import OpenAI

from .base import Message


class OpenAICompatibleProvider:
    """OpenAI, Ollama, LM Studio, vLLM... anything speaking the OpenAI chat API."""

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY") or "not-needed")

    @staticmethod
    def _msgs(messages, system):
        return ([{"role": "system", "content": system}] if system else []) + list(messages)

    def complete(self, messages: list[Message], *, system=None, max_tokens=4096, temperature=0.2) -> str:
        r = self.client.chat.completions.create(model=self.model, messages=self._msgs(messages, system),
                                                max_tokens=max_tokens, temperature=temperature)
        return r.choices[0].message.content or ""

    def stream(self, messages: list[Message], *, system=None, max_tokens=4096,
               temperature=0.2) -> Iterator[str]:
        r = self.client.chat.completions.create(model=self.model, messages=self._msgs(messages, system),
                                                max_tokens=max_tokens, temperature=temperature, stream=True)
        for chunk in r:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
