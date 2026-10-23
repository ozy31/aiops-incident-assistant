from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from aiops_assistant.api import create_app
from aiops_assistant.config import Settings
from aiops_assistant.pipeline import Analyzer

from .conftest import LOKI, PROM, FakeProvider, loki_response, prom_response


def _client(settings: Settings, provider: FakeProvider) -> TestClient:
    analyzer = Analyzer(settings, provider=provider)
    return TestClient(create_app(settings, analyzer))


def _mock() -> None:
    respx.get(f"{LOKI}/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=loki_response(["ERROR boom"]))
    )
    respx.get(f"{PROM}/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=prom_response([1.0, 2.0]))
    )


def test_health_and_metrics(settings: Settings, fake_provider: FakeProvider) -> None:
    with _client(settings, fake_provider) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert c.get("/readyz").json()["provider"] == "fake"
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "aiops_alerts_received_total" in r.text


@respx.mock(assert_all_mocked=False, assert_all_called=False)
def test_webhook_wait_returns_results(
    settings: Settings, fake_provider: FakeProvider, sample_payload: dict[str, Any]
) -> None:
    _mock()
    respx.route(host="testserver").pass_through()
    with _client(settings, fake_provider) as c:
        r = c.post("/webhook/alertmanager?wait=true", json=sample_payload)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["results"][0]["summary"]["confidence"] == "medium"
    assert body["results"][0]["alert_name"] == "HighErrorRate"


@respx.mock(assert_all_mocked=False, assert_all_called=False)
def test_webhook_async_accepts(
    settings: Settings, fake_provider: FakeProvider, sample_payload: dict[str, Any]
) -> None:
    _mock()
    respx.route(host="testserver").pass_through()
    with _client(settings, fake_provider) as c:
        r = c.post("/webhook/alertmanager", json=sample_payload)
    assert r.status_code == 202
    assert r.json() == {"accepted": 1}
    assert len(fake_provider.calls) == 1  # background task ran


def test_resolved_only_is_noop(
    settings: Settings, fake_provider: FakeProvider, sample_payload: dict[str, Any]
) -> None:
    sample_payload["alerts"][0]["status"] = "resolved"
    with _client(settings, fake_provider) as c:
        r = c.post("/webhook/alertmanager", json=sample_payload)
    assert r.json() == {"accepted": 0, "results": []}
    assert fake_provider.calls == []


def test_webhook_token_required(
    fake_provider: FakeProvider, sample_payload: dict[str, Any]
) -> None:
    s = Settings(llm_provider="none", webhook_token="s3cret")
    with _client(s, fake_provider) as c:
        assert c.post("/webhook/alertmanager", json=sample_payload).status_code == 401
        bad = {"Authorization": "Bearer nope"}
        assert c.post("/webhook/alertmanager", json=sample_payload, headers=bad).status_code == 401
        ok = {"Authorization": "Bearer s3cret"}
        r = c.post("/webhook/alertmanager?wait=true", json=sample_payload, headers=ok)
        assert r.status_code == 200


def test_analyze_endpoint(fake_provider: FakeProvider, sample_payload: dict[str, Any]) -> None:
    with _client(Settings(llm_provider="none"), fake_provider) as c:
        r = c.post("/analyze", json=sample_payload["alerts"][0])
    assert r.status_code == 200
    assert r.json()["summary"]["title"]
