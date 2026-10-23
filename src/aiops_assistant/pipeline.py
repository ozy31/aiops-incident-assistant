"""The enrichment -> clustering -> redaction -> LLM pipeline."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any, Protocol

import httpx
import structlog

from . import metrics
from .config import Settings
from .llm import LLMError, LLMProvider, build_provider
from .llm.prompt import SYSTEM_PROMPT, build_prompt
from .logmining import mine_templates
from .models import (
    Alert,
    AlertmanagerWebhook,
    AnalysisResult,
    IncidentContext,
    K8sEvent,
    LogLine,
    MetricSeries,
    Rollout,
)
from .notify import SlackNotifier
from .redaction import Redactor
from .sources import ElasticsearchSource, LokiSource, PrometheusSource

log = structlog.get_logger(__name__)


class LogSource(Protocol):
    name: str

    async def fetch(self, alert: Alert, start: Any, end: Any) -> list[LogLine]: ...


class MetricSource(Protocol):
    name: str

    async def fetch(self, alert: Alert, start: Any, end: Any) -> list[MetricSeries]: ...


class K8sSource(Protocol):
    name: str

    async def fetch(
        self, alert: Alert, start: Any, end: Any
    ) -> tuple[list[K8sEvent], list[Rollout]]: ...


class Analyzer:
    """Wires sources, redaction, the LLM provider and Slack together."""

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        provider: LLMProvider | None = None,
        log_sources: list[LogSource] | None = None,
        metric_sources: list[MetricSource] | None = None,
        k8s_source: K8sSource | None = None,
    ) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=30.0)
        self.provider = provider or build_provider(settings)
        self.log_sources = log_sources if log_sources is not None else self._default_log_sources()
        self.metric_sources = (
            metric_sources if metric_sources is not None else self._default_metric_sources()
        )
        self.k8s_source = k8s_source if k8s_source is not None else self._default_k8s_source()
        self.slack = (
            SlackNotifier(self.http, settings.slack_webhook_url.get_secret_value())
            if settings.slack_webhook_url
            else None
        )

    # -- construction helpers ------------------------------------------------

    def _default_log_sources(self) -> list[LogSource]:
        s = self.settings
        sources: list[LogSource] = []
        if s.loki_url:
            sources.append(
                LokiSource(
                    self.http,
                    s.loki_url,
                    selector_labels=s.loki_selector_labels,
                    line_filter=s.loki_line_filter,
                    limit=s.loki_limit,
                    tenant_id=s.loki_tenant_id,
                )
            )
        if s.elasticsearch_url:
            basic = (
                (s.elasticsearch_username, s.elasticsearch_password.get_secret_value())
                if s.elasticsearch_username and s.elasticsearch_password
                else None
            )
            sources.append(
                ElasticsearchSource(
                    self.http,
                    s.elasticsearch_url,
                    index=s.elasticsearch_index,
                    query_string=s.elasticsearch_query,
                    label_fields=s.elasticsearch_label_fields,
                    timestamp_field=s.elasticsearch_timestamp_field,
                    message_field=s.elasticsearch_message_field,
                    limit=s.elasticsearch_limit,
                    api_key=(
                        s.elasticsearch_api_key.get_secret_value()
                        if s.elasticsearch_api_key
                        else None
                    ),
                    basic_auth=basic,
                )
            )
        return sources

    def _default_metric_sources(self) -> list[MetricSource]:
        s = self.settings
        if not s.prometheus_url:
            return []
        return [
            PrometheusSource(
                self.http,
                s.prometheus_url,
                step_seconds=s.prometheus_step_seconds,
                max_series=s.prometheus_max_series,
                extra_queries=s.prometheus_extra_queries,
            )
        ]

    def _default_k8s_source(self) -> K8sSource | None:
        if not self.settings.k8s_enabled:
            return None
        try:
            from .sources.kubernetes import KubernetesSource

            return KubernetesSource(max_events=self.settings.k8s_max_events)
        except Exception as exc:  # missing extra, no kubeconfig, ...
            log.warning("kubernetes source disabled", error=str(exc))
            return None

    async def aclose(self) -> None:
        await self.http.aclose()

    # -- pipeline ------------------------------------------------------------

    async def gather_context(self, alert: Alert) -> IncidentContext:
        s = self.settings
        start = alert.starts_at - timedelta(minutes=s.lookback_minutes)
        end = alert.starts_at + timedelta(minutes=s.lookahead_minutes)
        redactor = Redactor(redact_emails=s.redact_emails, redact_ips=s.redact_ips)
        errors: list[str] = []

        async def guarded(name: str, coro: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                metrics.SOURCE_ERRORS.labels(source=name).inc()
                log.warning("context source failed", source=name, error=str(exc))
                errors.append(f"{name}: {type(exc).__name__}: {exc}"[:300])
                return None

        log_tasks = [guarded(src.name, src.fetch(alert, start, end)) for src in self.log_sources]
        metric_tasks = [
            guarded(src.name, src.fetch(alert, start, end)) for src in self.metric_sources
        ]
        k8s_task = (
            [guarded(self.k8s_source.name, self.k8s_source.fetch(alert, start, end))]
            if self.k8s_source
            else []
        )
        results = await asyncio.gather(*log_tasks, *metric_tasks, *k8s_task)
        log_results = results[: len(log_tasks)]
        metric_results = results[len(log_tasks) : len(log_tasks) + len(metric_tasks)]
        k8s_result = results[-1] if k8s_task else None

        # Redact BEFORE clustering so template examples never carry secrets.
        lines: list[LogLine] = [
            line.model_copy(update={"message": redactor.redact(line.message)})
            for batch in log_results
            if batch
            for line in batch
        ]
        templates = mine_templates(
            lines, max_templates=s.max_templates, similarity=s.template_similarity
        )
        series: list[MetricSeries] = [
            m.model_copy(update={"labels": redactor.redact_mapping(m.labels)})
            for batch in metric_results
            if batch
            for m in batch
        ]
        events: list[K8sEvent] = []
        rollouts: list[Rollout] = []
        if k8s_result:
            events = [
                e.model_copy(update={"message": redactor.redact(e.message)}) for e in k8s_result[0]
            ]
            rollouts = k8s_result[1]

        safe_alert = alert.model_copy(
            update={
                "labels": redactor.redact_mapping(alert.labels),
                "annotations": redactor.redact_mapping(alert.annotations),
            }
        )
        metrics.LOG_LINES.inc(len(lines))
        metrics.LOG_TEMPLATES.inc(len(templates))
        for rule, n in redactor.counts.items():
            metrics.REDACTIONS.labels(rule=rule).inc(n)

        return IncidentContext(
            alert=safe_alert,
            window_start=start,
            window_end=end,
            log_templates=templates,
            raw_log_lines=len(lines),
            metrics=series,
            k8s_events=events,
            rollouts=rollouts,
            redactions=dict(redactor.counts),
            errors=errors,
        )

    async def analyze(self, alert: Alert, *, notify: bool = True) -> AnalysisResult:
        started = time.perf_counter()
        structlog.contextvars.bind_contextvars(alert=alert.name, fingerprint=alert.fingerprint)
        try:
            ctx = await self.gather_context(alert)
            prompt = build_prompt(ctx)
            try:
                summary = await self.provider.summarize(SYSTEM_PROMPT, prompt)
            except LLMError:
                metrics.ANALYSES.labels(provider=self.provider.name, outcome="llm_error").inc()
                raise
            result = AnalysisResult(
                alert_name=alert.name,
                fingerprint=alert.fingerprint,
                provider=self.provider.name,
                model=self.provider.model,
                summary=summary,
                context_stats={
                    "raw_log_lines": ctx.raw_log_lines,
                    "log_templates": len(ctx.log_templates),
                    "metric_series": len(ctx.metrics),
                    "k8s_events": len(ctx.k8s_events),
                    "rollouts": len(ctx.rollouts),
                    "redactions": ctx.redactions,
                    "redactions_total": sum(ctx.redactions.values()),
                    "prompt_chars": len(prompt),
                },
                errors=ctx.errors,
            )
            metrics.ANALYSES.labels(provider=self.provider.name, outcome="ok").inc()
            log.info(
                "analysis complete",
                confidence=summary.confidence,
                **{k: v for k, v in result.context_stats.items() if k != "redactions"},
            )
            if notify and self.slack:
                await self._post_slack(result)
            return result
        finally:
            metrics.ANALYSIS_DURATION.observe(time.perf_counter() - started)
            structlog.contextvars.unbind_contextvars("alert", "fingerprint")

    async def _post_slack(self, result: AnalysisResult) -> None:
        assert self.slack is not None
        try:
            await self.slack.post(result)
            metrics.SLACK_POSTS.labels(outcome="ok").inc()
        except httpx.HTTPError as exc:
            metrics.SLACK_POSTS.labels(outcome="error").inc()
            log.warning("slack post failed", error=str(exc))
            result.errors.append(f"slack: {exc}")

    def select_alerts(self, payload: AlertmanagerWebhook) -> list[Alert]:
        alerts = payload.alerts
        for a in alerts:
            metrics.ALERTS_RECEIVED.labels(status=a.status).inc()
        if self.settings.only_firing:
            alerts = [a for a in alerts if a.status == "firing"]
        return alerts[: self.settings.max_alerts_per_webhook]
