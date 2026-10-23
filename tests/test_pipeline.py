from __future__ import annotations

import json

import httpx
import respx

from aiops_assistant.config import Settings
from aiops_assistant.models import Alert
from aiops_assistant.notify import build_slack_payload
from aiops_assistant.pipeline import Analyzer

from .conftest import LOKI, PROM, SLACK, FakeProvider, loki_response, prom_response

NOISY_LOGS = [
    "ERROR pool timeout after 5000ms acquiring connection to payments-db (active=50 max=50)",
    "ERROR pool timeout after 5001ms acquiring connection to payments-db (active=50 max=50)",
    "ERROR pool timeout after 4999ms acquiring connection to payments-db (active=50 max=50)",
    "WARN retrying charge for user jane.doe@example.com token=tok_live_abcdef123456",
    "ERROR upstream mongodb://svc:Pa55w0rd@mongo-0:27017 timed out",
]


def _mock_backends(logs: list[str] | None = None) -> None:
    respx.get(f"{LOKI}/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=loki_response(logs or NOISY_LOGS))
    )
    respx.get(f"{PROM}/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=prom_response([0.01, 0.02, 0.18, 0.22]))
    )


@respx.mock
async def test_end_to_end_redacts_before_llm(
    settings: Settings, alert: Alert, fake_provider: FakeProvider
) -> None:
    _mock_backends()
    analyzer = Analyzer(settings, provider=fake_provider)
    result = await analyzer.analyze(alert)
    await analyzer.aclose()

    _, prompt = fake_provider.calls[0]
    for secret in ("jane.doe@example.com", "tok_live_abcdef123456", "Pa55w0rd"):
        assert secret not in prompt
    assert "pool timeout" in prompt
    assert "http_requests_total" in prompt  # expr from generatorURL was queried

    stats = result.context_stats
    assert stats["raw_log_lines"] == 5
    assert stats["log_templates"] == 3  # 3 pool-timeout lines collapse into one
    assert stats["metric_series"] == 1
    assert stats["redactions_total"] >= 3
    assert result.provider == "fake"
    assert result.errors == []


@respx.mock
async def test_source_failure_is_non_fatal(
    settings: Settings, alert: Alert, fake_provider: FakeProvider
) -> None:
    respx.get(f"{LOKI}/loki/api/v1/query_range").mock(return_value=httpx.Response(502))
    respx.get(f"{PROM}/api/v1/query_range").mock(side_effect=httpx.ConnectError("refused"))
    analyzer = Analyzer(settings, provider=fake_provider)
    result = await analyzer.analyze(alert)
    await analyzer.aclose()
    assert len(result.errors) == 2
    assert any(e.startswith("loki:") for e in result.errors)
    assert "collection_errors" in fake_provider.calls[0][1]


@respx.mock
async def test_slack_notification(alert: Alert, fake_provider: FakeProvider) -> None:
    _mock_backends()
    slack = respx.post(SLACK).mock(return_value=httpx.Response(200, text="ok"))
    s = Settings(loki_url=LOKI, prometheus_url=PROM, slack_webhook_url=SLACK)
    analyzer = Analyzer(s, provider=fake_provider)
    await analyzer.analyze(alert)
    await analyzer.aclose()
    body = json.loads(slack.calls.last.request.content)
    assert body["text"].startswith("[HighErrorRate]")
    assert any(b["type"] == "header" for b in body["blocks"])


@respx.mock
async def test_slack_failure_recorded(alert: Alert, fake_provider: FakeProvider) -> None:
    _mock_backends()
    respx.post(SLACK).mock(return_value=httpx.Response(500))
    s = Settings(loki_url=LOKI, prometheus_url=PROM, slack_webhook_url=SLACK)
    analyzer = Analyzer(s, provider=fake_provider)
    result = await analyzer.analyze(alert)
    await analyzer.aclose()
    assert any(e.startswith("slack:") for e in result.errors)


async def test_no_sources_configured(alert: Alert, fake_provider: FakeProvider) -> None:
    analyzer = Analyzer(Settings(), provider=fake_provider)
    assert analyzer.log_sources == [] and analyzer.metric_sources == []
    result = await analyzer.analyze(alert)
    await analyzer.aclose()
    assert result.context_stats["raw_log_lines"] == 0


def test_select_alerts_filters_resolved(settings: Settings, webhook: object) -> None:
    from aiops_assistant.models import AlertmanagerWebhook

    assert isinstance(webhook, AlertmanagerWebhook)
    resolved = webhook.alerts[0].model_copy(update={"status": "resolved"})
    webhook.alerts.append(resolved)
    analyzer = Analyzer(settings, provider=FakeProvider())
    assert len(analyzer.select_alerts(webhook)) == 1


def test_slack_payload_shape(fake_provider: FakeProvider) -> None:
    import asyncio

    from aiops_assistant.models import AnalysisResult

    summary = asyncio.run(fake_provider.summarize("", ""))
    payload = build_slack_payload(
        AnalysisResult(alert_name="A", fingerprint="f", provider="p", model="m", summary=summary)
    )
    text = json.dumps(payload)
    assert "Probable cause" in text and "Next checks" in text
