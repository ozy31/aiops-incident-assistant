"""Prometheus metrics for the assistant itself."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

ALERTS_RECEIVED = Counter(
    "aiops_alerts_received_total", "Alerts received from Alertmanager", ["status"]
)
ANALYSES = Counter("aiops_analyses_total", "Completed analyses", ["provider", "outcome"])
ANALYSIS_DURATION = Histogram(
    "aiops_analysis_duration_seconds",
    "End-to-end analysis time per alert",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
SOURCE_ERRORS = Counter("aiops_source_errors_total", "Context source failures", ["source"])
LOG_LINES = Counter("aiops_log_lines_total", "Raw log lines collected before clustering")
LOG_TEMPLATES = Counter("aiops_log_templates_total", "Log templates sent to the LLM")
REDACTIONS = Counter("aiops_redactions_total", "Values redacted before LLM call", ["rule"])
SLACK_POSTS = Counter("aiops_slack_posts_total", "Slack notifications", ["outcome"])
