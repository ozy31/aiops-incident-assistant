"""Post incident summaries to Slack via an incoming webhook (Block Kit)."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import AnalysisResult

_CONFIDENCE_EMOJI = {
    "low": ":white_circle:",
    "medium": ":large_yellow_circle:",
    "high": ":large_green_circle:",
}


def _bullets(items: list[str], limit: int = 6) -> str:
    return "\n".join(f"• {item}" for item in items[:limit]) or "_none_"


def build_slack_payload(result: AnalysisResult) -> dict[str, Any]:
    s = result.summary
    stats = result.context_stats
    footer = (
        f"{result.provider}/{result.model} · {stats.get('raw_log_lines', 0)} log lines → "
        f"{stats.get('log_templates', 0)} templates · {stats.get('metric_series', 0)} series · "
        f"{stats.get('redactions_total', 0)} redactions"
    )
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": s.title[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": s.summary}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Probable cause*\n{s.probable_cause}"},
                {"type": "mrkdwn", "text": f"*Blast radius*\n{s.blast_radius}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence*\n{_CONFIDENCE_EMOJI[s.confidence]} {s.confidence}"
                    f" - {s.confidence_rationale}",
                },
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Evidence*\n{_bullets(s.evidence)}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Next checks*\n{_bullets(s.suggested_next_checks)}",
            },
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]},
    ]
    return {"text": f"[{result.alert_name}] {s.title}", "blocks": blocks}


class SlackNotifier:
    def __init__(self, client: httpx.AsyncClient, webhook_url: str) -> None:
        self._client = client
        self._url = webhook_url

    async def post(self, result: AnalysisResult) -> None:
        resp = await self._client.post(self._url, json=build_slack_payload(result))
        resp.raise_for_status()
