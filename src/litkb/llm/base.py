"""Pluggable LLM providers (decision D2). Roles: 'synthesis' and 'fast'.

Provider and model are chosen per role, first match wins:
  model:    LITKB_MODEL_<ROLE> env > LITKB_MODEL env > [llm.<role>].model > [llm].model
  provider: LITKB_PROVIDER_<ROLE> env > LITKB_PROVIDER env > [llm.<role>].provider > [llm].provider
Default: OpenRouter with google/gemini-4-hydrogen for both roles.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Literal, Protocol, TypedDict

Role = Literal["synthesis", "fast"]
PROVIDERS = ("openrouter", "anthropic", "openai_compatible")


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class LLMProvider(Protocol):
    model: str

    def complete(self, messages: list[Message], *, system: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> str: ...

    def stream(self, messages: list[Message], *, system: str | None = None,
               max_tokens: int = 4096, temperature: float = 0.2) -> Iterator[str]: ...


def resolve_llm(role: Role, cfg: dict) -> tuple[str, str]:
    """(provider, model) for a role, after env overrides."""
    llm = cfg.get("llm", {})
    spec = llm.get(role, {}) or {}
    r = role.upper()

    def pick(key: str) -> str | None:
        return (os.environ.get(f"LITKB_{key}_{r}") or os.environ.get(f"LITKB_{key}")
                or spec.get(key.lower()) or llm.get(key.lower()))

    provider, model = pick("PROVIDER"), pick("MODEL")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider {provider!r} for role {role!r}; expected one of {PROVIDERS}")
    if not model:
        raise ValueError(f"No LLM model set for role {role!r}: set LITKB_MODEL in .env or [llm].model")
    return provider, model


def get_provider(role: Role, cfg: dict) -> LLMProvider:
    provider, model = resolve_llm(role, cfg)
    if provider == "openrouter":
        from .openai_provider import OpenRouterProvider
        o = cfg["llm"]["openrouter"]
        return OpenRouterProvider(model, o["base_url"], int(o.get("reasoning_max_tokens", 0)))
    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model)
    from .openai_provider import OpenAICompatibleProvider
    return OpenAICompatibleProvider(model, cfg["llm"]["openai_compatible"]["base_url"])
