from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aiops_assistant.config import Settings
from aiops_assistant.models import Alert, AlertmanagerWebhook, IncidentSummary

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "alert-sample.json"

LOKI = "http://loki.test"
PROM = "http://prom.test"
ES = "http://es.test"
SLACK = "https://hooks.slack.com/services/T000/B000/XXXX"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never pick up a developer's real AIOPS_* env or .env file."""
    import os

    for key in list(os.environ):
        if key.startswith("AIOPS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def sample_payload() -> dict[str, Any]:
    return json.loads(SAMPLE.read_text())  # type: ignore[no-any-return]


@pytest.fixture
def webhook(sample_payload: dict[str, Any]) -> AlertmanagerWebhook:
    return AlertmanagerWebhook.model_validate(sample_payload)


@pytest.fixture
def alert(webhook: AlertmanagerWebhook) -> Alert:
    return webhook.alerts[0]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        loki_url=LOKI,
        prometheus_url=PROM,
        llm_provider="none",
        log_format="console",
    )


class FakeProvider:
    """Records prompts; returns a canned summary. No network."""

    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def summarize(self, system: str, prompt: str) -> IncidentSummary:
        self.calls.append((system, prompt))
        return IncidentSummary(
            title="checkout-api 5xx after deploy",
            summary="Errors began right after a rollout.",
            probable_cause="Connection pool exhaustion to the payments DB.",
            evidence=["412x pool timeout"],
            blast_radius="shop/checkout-api",
            suggested_next_checks=["kubectl -n shop rollout history deploy/checkout-api"],
            confidence="medium",
            confidence_rationale="Logs and metrics agree; no DB metrics available.",
        )


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


def loki_response(lines: list[str], app: str = "checkout-api") -> dict[str, Any]:
    base = int(datetime(2026, 10, 20, 14, 30, tzinfo=UTC).timestamp() * 1e9)
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"app": app, "namespace": "shop"},
                    "values": [[str(base + i * 1_000_000_000), ln] for i, ln in enumerate(lines)],
                }
            ],
        },
    }


def prom_response(values: list[float]) -> dict[str, Any]:
    start = datetime(2026, 10, 20, 14, 0, tzinfo=UTC).timestamp()
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"app": "checkout-api"},
                    "values": [[start + 60 * i, str(v)] for i, v in enumerate(values)],
                }
            ],
        },
    }
