"""No-LLM provider: a deterministic summary built from the context alone.

Useful for trying the pipeline without an API key, and as a degraded mode when
you do not want any data leaving the cluster.
"""

from __future__ import annotations

import json
import re

from ..models import IncidentSummary

_CONTEXT = re.compile(r"<context>\n(.*)\n</context>", re.DOTALL)


class OfflineProvider:
    name = "none"
    model = "heuristic"

    async def summarize(self, system: str, prompt: str) -> IncidentSummary:
        match = _CONTEXT.search(prompt)
        ctx = json.loads(match.group(1)) if match else {}
        alert = ctx.get("alert", {})
        templates = ctx.get("logs", {}).get("templates", [])
        metrics = ctx.get("metrics", [])
        events = ctx.get("kubernetes", {}).get("events", [])

        evidence = [f"{t['count']}x {t['template']}" for t in templates[:5]]
        evidence += [
            f"{m['query']}: min={m['min']} max={m['max']} last={m['last']}" for m in metrics[:3]
        ]
        evidence += [f"{e['reason']} on {e['object']}: {e['message']}" for e in events[:3]]
        top = templates[0]["template"] if templates else "no matching error logs"
        labels = alert.get("labels", {})
        scope = ", ".join(
            f"{k}={labels[k]}" for k in ("namespace", "service", "app") if k in labels
        )

        return IncidentSummary(
            title=f"{alert.get('name', 'alert')}: {scope or 'unknown scope'}",
            summary=(
                "Offline mode (no LLM): this is a mechanical digest of the collected "
                "context, not an analysis."
            ),
            probable_cause=f"Most frequent error template: {top}",
            evidence=evidence,
            blast_radius=scope or "unknown",
            suggested_next_checks=[
                "Open the log query for the alert window and read the top template examples.",
                "Compare the metric range against the previous day at the same time.",
                "Check for a rollout or config change shortly before the alert start.",
            ],
            confidence="low",
            confidence_rationale="No model reasoning applied; frequency ranking only.",
        )
