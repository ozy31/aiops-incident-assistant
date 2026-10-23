"""Fetch recent error logs from Elasticsearch / OpenSearch via ``_search``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ..models import Alert, LogLine


def _get_path(doc: dict[str, Any], dotted: str) -> Any:
    """Read ``a.b.c`` from a nested ``_source`` (also handles flattened dotted keys)."""
    if dotted in doc:
        return doc[dotted]
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def build_query(
    alert: Alert,
    start: datetime,
    end: datetime,
    *,
    query_string: str,
    label_fields: dict[str, str],
    timestamp_field: str,
    limit: int,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {
            "range": {
                timestamp_field: {
                    "gte": start.isoformat(),
                    "lte": end.isoformat(),
                    "format": "strict_date_optional_time",
                }
            }
        }
    ]
    for label, field in label_fields.items():
        if value := alert.labels.get(label):
            filters.append({"term": {field: value}})
    must: list[dict[str, Any]] = []
    if query_string:
        must.append({"query_string": {"query": query_string}})
    return {
        "size": limit,
        "sort": [{timestamp_field: {"order": "desc"}}],
        "query": {"bool": {"filter": filters, "must": must}},
    }


class ElasticsearchSource:
    name = "elasticsearch"

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        index: str,
        query_string: str,
        label_fields: dict[str, str],
        timestamp_field: str,
        message_field: str,
        limit: int,
        api_key: str | None = None,
        basic_auth: tuple[str, str] | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._index = index
        self._query_string = query_string
        self._label_fields = label_fields
        self._ts_field = timestamp_field
        self._msg_field = message_field
        self._limit = limit
        self._headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
        self._auth = basic_auth

    async def fetch(self, alert: Alert, start: datetime, end: datetime) -> list[LogLine]:
        body = build_query(
            alert,
            start,
            end,
            query_string=self._query_string,
            label_fields=self._label_fields,
            timestamp_field=self._ts_field,
            limit=self._limit,
        )
        resp = await self._client.post(
            f"{self._base_url}/{self._index}/_search",
            json=body,
            headers=self._headers,
            auth=self._auth or httpx.USE_CLIENT_DEFAULT,
        )
        resp.raise_for_status()
        lines: list[LogLine] = []
        for hit in resp.json().get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            message = _get_path(src, self._msg_field)
            if not message:
                continue
            ts_raw = _get_path(src, self._ts_field)
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else None
            lines.append(
                LogLine(
                    timestamp=ts,
                    source=f"es:{hit.get('_index', self._index)}",
                    message=str(message),
                )
            )
        return lines
