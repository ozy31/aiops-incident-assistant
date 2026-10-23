from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from aiops_assistant.llm import LLMError, OfflineProvider, build_provider, parse_summary
from aiops_assistant.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from aiops_assistant.llm.openai_compat import OpenAICompatProvider
from aiops_assistant.models import INCIDENT_SUMMARY_JSON_SCHEMA, IncidentSummary

GOOD = {
    "title": "t",
    "summary": "s",
    "probable_cause": "c",
    "evidence": ["e"],
    "blast_radius": "b",
    "suggested_next_checks": ["n"],
    "confidence": "high",
    "confidence_rationale": "r",
}


def test_schema_matches_model() -> None:
    assert set(INCIDENT_SUMMARY_JSON_SCHEMA["properties"]) == set(IncidentSummary.model_fields)
    assert set(INCIDENT_SUMMARY_JSON_SCHEMA["required"]) == set(IncidentSummary.model_fields)


def test_parse_summary_plain_and_fenced() -> None:
    assert parse_summary(json.dumps(GOOD)).confidence == "high"
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"
    assert parse_summary(fenced).title == "t"


@pytest.mark.parametrize("bad", ["no json here", '{"title": "x"}', "{not json}"])
def test_parse_summary_rejects(bad: str) -> None:
    with pytest.raises(LLMError):
        parse_summary(bad)


# --- Anthropic (SDK client replaced by a fake; no network) ----------------------


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def _fake_client(text: str, stop_reason: str = "end_turn") -> Any:
    resp = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )
    return SimpleNamespace(
        messages=_FakeMessages(resp), beta=SimpleNamespace(messages=_FakeMessages(resp))
    )


async def test_anthropic_with_server_fallback() -> None:
    client = _fake_client(json.dumps(GOOD))
    p = AnthropicProvider(model="claude-opus-5-5", effort="medium", client=client)
    out = await p.summarize("sys", "prompt")
    assert out.title == "t"
    kw = client.beta.messages.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default"
    assert kw["betas"] == [FALLBACK_BETA]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "medium"
    assert kw["system"] == "sys"


async def test_anthropic_without_fallback_and_effort() -> None:
    client = _fake_client(json.dumps(GOOD))
    p = AnthropicProvider(model="m", effort="", server_fallback=False, client=client)
    await p.summarize("sys", "prompt")
    kw = client.messages.kwargs
    assert "effort" not in kw["output_config"]
    assert "fallbacks" not in kw


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
async def test_anthropic_bad_stop_reason(stop: str) -> None:
    p = AnthropicProvider(model="m", client=_fake_client("{}", stop_reason=stop))
    with pytest.raises(LLMError, match=stop):
        await p.summarize("s", "p")


# --- OpenAI-compatible ------------------------------------------------------------


@respx.mock
async def test_openai_compat() -> None:
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]}
        )
    )
    async with httpx.AsyncClient() as http:
        p = OpenAICompatProvider(
            base_url="http://llm.test/v1", model="qwen", api_key="k", client=http
        )
        out = await p.summarize("sys", "user")
    assert out.confidence == "high"
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "qwen"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer k"


@respx.mock
async def test_openai_compat_http_error() -> None:
    respx.post("http://llm.test/v1/chat/completions").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        p = OpenAICompatProvider(base_url="http://llm.test/v1", model="x", client=http)
        with pytest.raises(LLMError):
            await p.summarize("s", "u")


# --- factory / offline --------------------------------------------------------------


def test_build_provider_selects_backend(settings: Any) -> None:
    assert isinstance(build_provider(settings), OfflineProvider)
    settings.llm_provider = "openai"
    assert isinstance(build_provider(settings), OpenAICompatProvider)


async def test_offline_provider_digest() -> None:
    prompt = (
        "<context>\n"
        + json.dumps(
            {
                "alert": {"name": "X", "labels": {"namespace": "ns"}},
                "logs": {"templates": [{"count": 3, "template": "boom <NUM>"}]},
                "metrics": [],
                "kubernetes": {"events": []},
            }
        )
        + "\n</context>"
    )
    out = await OfflineProvider().summarize("", prompt)
    assert out.confidence == "low"
    assert "boom <NUM>" in out.probable_cause
