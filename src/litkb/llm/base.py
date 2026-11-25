"""Pluggable LLM providers (decision D2). Roles: 'synthesis' and 'fast'."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Literal, Protocol, TypedDict


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class LLMProvider(Protocol):
    model: str

    def complete(self, messages: list[Message], *, system: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> str: ...

    def stream(self, messages: list[Message], *, system: str | None = None,
               max_tokens: int = 4096, temperature: float = 0.2) -> Iterator[str]: ...


def get_provider(role: Literal["synthesis", "fast"], cfg: dict) -> LLMProvider:
    spec = cfg["llm"][role]
    if spec["provider"] == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(spec["model"])
    if spec["provider"] == "openai_compatible":
        from .openai_provider import OpenAICompatibleProvider
        return OpenAICompatibleProvider(spec["model"], cfg["llm"]["openai_compatible"]["base_url"])
    raise ValueError(f"Unknown LLM provider: {spec['provider']}")
