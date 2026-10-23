"""Data models: Alertmanager webhook payload, gathered context and the LLM summary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Alertmanager webhook (https://prometheus.io/docs/alerting/latest/configuration/#webhook_config)
# ---------------------------------------------------------------------------


class Alert(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: Literal["firing", "resolved"] = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt", default_factory=lambda: datetime.now(UTC))
    ends_at: datetime | None = Field(alias="endsAt", default=None)
    generator_url: str = Field(alias="generatorURL", default="")
    fingerprint: str = ""

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "unknown")


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    version: str = "4"
    group_key: str = Field(alias="groupKey", default="")
    status: Literal["firing", "resolved"] = "firing"
    receiver: str = ""
    group_labels: dict[str, str] = Field(alias="groupLabels", default_factory=dict)
    common_labels: dict[str, str] = Field(alias="commonLabels", default_factory=dict)
    common_annotations: dict[str, str] = Field(alias="commonAnnotations", default_factory=dict)
    external_url: str = Field(alias="externalURL", default="")
    alerts: list[Alert] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gathered context
# ---------------------------------------------------------------------------


class LogLine(BaseModel):
    timestamp: datetime | None = None
    source: str
    message: str
    labels: dict[str, str] = Field(default_factory=dict)


class LogTemplate(BaseModel):
    """A cluster of similar log lines, represented by one template."""

    template: str
    count: int
    example: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sources: list[str] = Field(default_factory=list)


class MetricSeries(BaseModel):
    query: str
    labels: dict[str, str] = Field(default_factory=dict)
    min: float
    max: float
    avg: float
    last: float
    samples: list[tuple[str, float]] = Field(
        default_factory=list, description="Down-sampled (HH:MM, value) points."
    )


class K8sEvent(BaseModel):
    timestamp: datetime | None = None
    type: str
    reason: str
    object: str
    message: str
    count: int = 1


class Rollout(BaseModel):
    kind: str
    name: str
    revision: str | None = None
    created: datetime | None = None
    images: list[str] = Field(default_factory=list)
    ready: str | None = None


class IncidentContext(BaseModel):
    alert: Alert
    window_start: datetime
    window_end: datetime
    log_templates: list[LogTemplate] = Field(default_factory=list)
    raw_log_lines: int = 0
    metrics: list[MetricSeries] = Field(default_factory=list)
    k8s_events: list[K8sEvent] = Field(default_factory=list)
    rollouts: list[Rollout] = Field(default_factory=list)
    redactions: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(
        default_factory=list, description="Non-fatal errors from context sources."
    )


# ---------------------------------------------------------------------------
# LLM output
# ---------------------------------------------------------------------------

Confidence = Literal["low", "medium", "high"]


class IncidentSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    summary: str
    probable_cause: str
    evidence: list[str] = Field(default_factory=list)
    blast_radius: str
    suggested_next_checks: list[str] = Field(default_factory=list)
    confidence: Confidence
    confidence_rationale: str = ""


# Hand-written JSON schema used for provider-side structured output. Kept in
# sync with IncidentSummary (tests assert that).
INCIDENT_SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "One-line incident title."},
        "summary": {"type": "string", "description": "2-4 sentence plain-language summary."},
        "probable_cause": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific log templates, metric values or events backing the cause.",
        },
        "blast_radius": {"type": "string", "description": "Affected services/users/regions."},
        "suggested_next_checks": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence_rationale": {"type": "string"},
    },
    "required": [
        "title",
        "summary",
        "probable_cause",
        "evidence",
        "blast_radius",
        "suggested_next_checks",
        "confidence",
        "confidence_rationale",
    ],
    "additionalProperties": False,
}


class AnalysisResult(BaseModel):
    alert_name: str
    fingerprint: str
    provider: str
    model: str
    summary: IncidentSummary
    context_stats: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
