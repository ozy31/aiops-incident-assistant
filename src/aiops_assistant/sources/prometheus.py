"""Run PromQL range queries against Prometheus or VictoriaMetrics.

Both expose the same ``/api/v1/query_range`` HTTP API, so one client covers both.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from statistics import fmean
from urllib.parse import parse_qs, urlparse

import httpx

from ..models import Alert, MetricSeries

_MAX_POINTS = 12
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def expr_from_generator_url(url: str) -> str | None:
    """Prometheus puts the rule expression in ``generatorURL`` as ``g0.expr``."""
    if not url:
        return None
    qs = parse_qs(urlparse(url).query)
    values = qs.get("g0.expr")
    return values[0] if values else None


def render_template(template: str, labels: dict[str, str]) -> str | None:
    """Fill ``{label}`` placeholders; return None if any label is missing.

    PromQL braces for selectors are written as ``{{`` / ``}}`` in templates.
    """
    missing = [m for m in _PLACEHOLDER.findall(template) if m not in labels]
    if missing:
        return None
    return template.format(**labels)


def _summarize(query: str, labels: dict[str, str], values: list[list[str | float]]) -> MetricSeries:
    points = [(float(ts), float(v)) for ts, v in values]
    nums = [v for _, v in points]
    stride = max(1, math.ceil(len(points) / _MAX_POINTS))
    samples = [
        (datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M"), round(v, 4))
        for ts, v in points[::stride]
    ]
    return MetricSeries(
        query=query,
        labels=labels,
        min=round(min(nums), 4),
        max=round(max(nums), 4),
        avg=round(fmean(nums), 4),
        last=round(nums[-1], 4),
        samples=samples,
    )


class PrometheusSource:
    name = "prometheus"

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        step_seconds: int,
        max_series: int,
        extra_queries: list[str],
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._step = step_seconds
        self._max_series = max_series
        self._extra = extra_queries

    def queries_for(self, alert: Alert) -> list[str]:
        queries: list[str] = []
        if expr := expr_from_generator_url(alert.generator_url):
            queries.append(expr)
        for template in self._extra:
            if (q := render_template(template, alert.labels)) is not None:
                queries.append(q)
        return list(dict.fromkeys(queries))

    async def fetch(self, alert: Alert, start: datetime, end: datetime) -> list[MetricSeries]:
        out: list[MetricSeries] = []
        for query in self.queries_for(alert):
            resp = await self._client.get(
                f"{self._base_url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": f"{start.timestamp():.0f}",
                    "end": f"{end.timestamp():.0f}",
                    "step": f"{self._step}s",
                },
            )
            resp.raise_for_status()
            for result in resp.json().get("data", {}).get("result", [])[: self._max_series]:
                if result.get("values"):
                    out.append(_summarize(query, result.get("metric", {}), result["values"]))
        return out
