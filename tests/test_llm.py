import json

import httpx
import pytest

from litkb.config import load_config
from litkb.llm import get_provider, resolve_llm
from litkb.llm.openai_provider import OpenRouterProvider


def test_default_is_openrouter_gemini():
    cfg = load_config()
    assert resolve_llm("synthesis", cfg) == ("openrouter", "google/gemini-4-hydrogen")
    assert resolve_llm("fast", cfg) == ("openrouter", "google/gemini-4-hydrogen")


def test_env_and_role_precedence(monkeypatch):
    cfg = {"llm": {"provider": "openrouter", "model": "a/base", "fast": {"model": "a/fast-cfg"}, "synthesis": {}}}
    assert resolve_llm("fast", cfg) == ("openrouter", "a/fast-cfg")          # role config beats [llm]
    monkeypatch.setenv("LITKB_MODEL", "b/env")
    assert resolve_llm("fast", cfg)[1] == "b/env"                            # env beats config
    assert resolve_llm("synthesis", cfg)[1] == "b/env"
    monkeypatch.setenv("LITKB_MODEL_FAST", "c/env-fast")
    assert resolve_llm("fast", cfg)[1] == "c/env-fast"                       # role env beats general env
    assert resolve_llm("synthesis", cfg)[1] == "b/env"
    monkeypatch.setenv("LITKB_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        resolve_llm("fast", cfg)


def test_openrouter_needs_key():
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_provider("synthesis", load_config())


def test_openrouter_request(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "google/gemini-4-hydrogen",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi [W1]"}}]})

    provider, model = resolve_llm("fast", load_config())
    llm = OpenRouterProvider(model, reasoning_max_tokens=2048,
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider == "openrouter"
    assert llm.complete([{"role": "user", "content": "q"}], system="sys") == "hi [W1]"
    req = seen[-1]
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "google/gemini-4-hydrogen"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert req.headers["authorization"] == "Bearer sk-or-test" and req.headers["x-title"] == "litkb"
    assert body["max_tokens"] == 4096 + 2048                                 # answer budget + reasoning budget
    assert body["reasoning"] == {"max_tokens": 2048, "exclude": True}


def test_reasoning_exhaustion_is_an_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def handler(request):
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                         "choices": [{"index": 0, "finish_reason": "length",
                                                      "message": {"role": "assistant", "content": ""}}]})

    llm = OpenRouterProvider("m", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="token limit"):
        llm.complete([{"role": "user", "content": "q"}])
