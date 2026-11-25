from __future__ import annotations

from collections.abc import Iterator

import anthropic

from .base import Message


class AnthropicProvider:
    def __init__(self, model: str):
        self.model = model
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def complete(self, messages: list[Message], *, system=None, max_tokens=4096, temperature=0.2) -> str:
        kw = {"system": system} if system else {}
        resp = self.client.messages.create(model=self.model, max_tokens=max_tokens,
                                           temperature=temperature, messages=messages, **kw)
        return "".join(b.text for b in resp.content if b.type == "text")

    def stream(self, messages: list[Message], *, system=None, max_tokens=4096,
               temperature=0.2) -> Iterator[str]:
        kw = {"system": system} if system else {}
        with self.client.messages.stream(model=self.model, max_tokens=max_tokens,
                                         temperature=temperature, messages=messages, **kw) as s:
            yield from s.text_stream
