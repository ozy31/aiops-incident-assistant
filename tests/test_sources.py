from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from aiops_assistant.models import Alert
from aiops_assistant.sources import ElasticsearchSource, LokiSource, PrometheusSource
from aiops_assistant.sources.kubernetes import workload_from_labels
from aiops_assistant.sources.loki import build_logql
from aiops_assistant.sources.prometheus import expr_from_generator_url, render_template

from .conftest import ES, LOKI, PROM, loki_response, prom_response

START = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
END = START + timedelta(minutes=35)


# --- Loki --------------------------------------------------------------------


def test_build_logql_from_labels(alert: Alert) -> None:
    q = build_logql(alert, ["namespace", "app", "missing"], "(?i)error")
    assert q == '{namespace="shop", app="checkout-api"} |~ "(?i)error"'


def test_build_logql_annotation_override(alert: Alert) -> None:
    alert.annotations["aiops_logql"] = '{job="custom"} |= "boom"'
    assert build_logql(alert, ["namespace"], "x") == '{job="custom"} |= "boom"'


def test_build_logql_no_matching_labels() -> None:
    assert build_logql(Alert(labels={"alertname": "X"}), ["namespace"], "") is None


def test_build_logql_escapes_quotes() -> None:
    q = build_logql(Alert(labels={"app": 'we"ird'}), ["app"], "")
    assert q == '{app="we\\"ird"}'


@respx.mock
async def test_loki_fetch(alert: Alert) -> None:
    route = respx.get(f"{LOKI}/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=loki_response(["boom 1", "boom 2"]))
    )
    async with httpx.AsyncClient() as client:
        src = LokiSource(
            client,
            LOKI,
            selector_labels=["namespace", "app"],
            line_filter="error",
            limit=100,
            tenant_id="team-a",
        )
        lines = await src.fetch(alert, START, END)
    assert [ln.message for ln in lines] == ["boom 1", "boom 2"]
    assert lines[0].source == "loki:checkout-api"
    req = route.calls.last.request
    assert req.headers["X-Scope-OrgID"] == "team-a"
    assert req.url.params["direction"] == "backward"
    assert req.url.params["start"] == str(int(START.timestamp() * 1e9))


@respx.mock
async def test_loki_http_error_raises(alert: Alert) -> None:
    respx.get(f"{LOKI}/loki/api/v1/query_range").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        src = LokiSource(client, LOKI, selector_labels=["app"], line_filter="", limit=10)
        with pytest.raises(httpx.HTTPStatusError):
            await src.fetch(alert, START, END)


# --- Elasticsearch -------------------------------------------------------------


@respx.mock
async def test_elasticsearch_fetch(alert: Alert) -> None:
    route = respx.post(f"{ES}/logs-*/_search").mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_index": "logs-2026.10.20",
                            "_source": {
                                "@timestamp": "2026-10-20T14:31:00Z",
                                "message": "MongoTimeoutError: no primary",
                            },
                        },
                        {"_index": "logs-2026.10.20", "_source": {"other": 1}},
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as client:
        src = ElasticsearchSource(
            client,
            ES,
            index="logs-*",
            query_string="error",
            label_fields={"namespace": "kubernetes.namespace"},
            timestamp_field="@timestamp",
            message_field="message",
            limit=50,
            api_key="abc",
        )
        lines = await src.fetch(alert, START, END)
    assert len(lines) == 1
    assert lines[0].message.startswith("MongoTimeoutError")
    body = json.loads(route.calls.last.request.content)
    assert {"term": {"kubernetes.namespace": "shop"}} in body["query"]["bool"]["filter"]
    assert route.calls.last.request.headers["Authorization"] == "ApiKey abc"


# --- Prometheus ----------------------------------------------------------------


def test_expr_from_generator_url(alert: Alert) -> None:
    expr = expr_from_generator_url(alert.generator_url)
    assert expr is not None and expr.startswith("sum by (app)")
    assert expr_from_generator_url("") is None


def test_render_template() -> None:
    t = 'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m]))'
    assert render_template(t, {"namespace": "shop"}) == (
        'sum(rate(container_cpu_usage_seconds_total{namespace="shop"}[5m]))'
    )
    assert render_template(t, {}) is None


@respx.mock
async def test_prometheus_fetch_summarizes(alert: Alert) -> None:
    route = respx.get(f"{PROM}/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=prom_response([0.01] * 20 + [0.2] * 15))
    )
    async with httpx.AsyncClient() as client:
        src = PrometheusSource(
            client,
            PROM,
            step_seconds=60,
            max_series=5,
            extra_queries=['up{{namespace="{namespace}"}}', "needs {missing_label}"],
        )
        assert len(src.queries_for(alert)) == 2
        series = await src.fetch(alert, START, END)
    assert route.call_count == 2
    s = series[0]
    assert s.min == 0.01 and s.max == 0.2 and s.last == 0.2
    assert len(s.samples) <= 13


# --- Kubernetes helpers ------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ({"deployment": "api"}, ("Deployment", "api")),
        ({"pod": "checkout-api-7d9f8b6c5d-x2k4p"}, ("Deployment", "checkout-api")),
        ({"pod": "rabbitmq-2"}, ("StatefulSet", "rabbitmq")),
        ({"app": "worker"}, ("Deployment", "worker")),
        ({}, (None, None)),
    ],
)
def test_workload_from_labels(
    labels: dict[str, str], expected: tuple[str | None, str | None]
) -> None:
    assert workload_from_labels(labels) == expected
