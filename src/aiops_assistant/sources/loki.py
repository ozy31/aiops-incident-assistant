"""Fetch recent error logs from Grafana Loki via LogQL."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from ..models import Alert, LogLine

ANNOTATION_OVERRIDE = "aiops_logql"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_logql(alert: Alert, selector_labels: list[str], line_filter: str) -> str | None:
    """Build a LogQL query for an alert.

    An ``aiops_logql`` annotation on the alert rule wins. Otherwise a stream
    selector is built from whichever of ``selector_labels`` the alert carries,
    followed by a case-insensitive regex line filter.
    """
    override = alert.annotations.get(ANNOTATION_OVERRIDE)
    if override:
        return override
    matchers = [
        f'{label}="{_escape(alert.labels[label])}"'
        for label in selector_labels
        if alert.labels.get(label)
    ]
    if not matchers:
        return None
    query = "{" + ", ".join(matchers) + "}"
    if line_filter:
        query += f' |~ "{_escape(line_filter)}"'
    return query


class LokiSource:
    name = "loki"

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        selector_labels: list[str],
        line_filter: str,
        limit: int,
        tenant_id: str | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._selector_labels = selector_labels
        self._line_filter = line_filter
        self._limit = limit
        self._headers = {"X-Scope-OrgID": tenant_id} if tenant_id else {}

    async def fetch(self, alert: Alert, start: datetime, end: datetime) -> list[LogLine]:
        query = build_logql(alert, self._selector_labels, self._line_filter)
        if query is None:
            return []
        resp = await self._client.get(
            f"{self._base_url}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": str(int(start.timestamp() * 1e9)),
                "end": str(int(end.timestamp() * 1e9)),
                "limit": str(self._limit),
                "direction": "backward",
            },
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        lines: list[LogLine] = []
        for stream in data.get("result", []):
            labels = stream.get("stream", {})
            for ts_ns, message in stream.get("values", []):
                lines.append(
                    LogLine(
                        timestamp=datetime.fromtimestamp(int(ts_ns) / 1e9, tz=UTC),
                        source=f"loki:{labels.get('app') or labels.get('job') or 'stream'}",
                        message=message,
                        labels=labels,
                    )
                )
        return lines
