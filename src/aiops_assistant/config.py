"""Runtime configuration, loaded from environment variables (prefix ``AIOPS_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["anthropic", "openai", "none"]
Effort = Literal["", "low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- service -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    webhook_token: SecretStr | None = Field(
        default=None,
        description="If set, POST /webhook/alertmanager requires 'Authorization: Bearer <token>'.",
    )
    max_alerts_per_webhook: int = 5
    only_firing: bool = True

    # --- time window ---------------------------------------------------------
    lookback_minutes: int = 30
    lookahead_minutes: int = 5

    # --- Loki ----------------------------------------------------------------
    loki_url: str | None = None
    loki_tenant_id: str | None = None
    loki_selector_labels: list[str] = ["namespace", "app", "service", "job", "pod", "container"]
    loki_line_filter: str = "(?i)(error|exception|fatal|panic|timeout|refused|denied)"
    loki_limit: int = 500

    # --- Elasticsearch -------------------------------------------------------
    elasticsearch_url: str | None = None
    elasticsearch_index: str = "logs-*"
    elasticsearch_api_key: SecretStr | None = None
    elasticsearch_username: str | None = None
    elasticsearch_password: SecretStr | None = None
    elasticsearch_timestamp_field: str = "@timestamp"
    elasticsearch_message_field: str = "message"
    elasticsearch_query: str = "error OR exception OR fatal OR panic OR timeout"
    elasticsearch_label_fields: dict[str, str] = {
        "namespace": "kubernetes.namespace",
        "app": "kubernetes.labels.app",
    }
    elasticsearch_limit: int = 500

    # --- Prometheus / VictoriaMetrics -----------------------------------------
    prometheus_url: str | None = None
    prometheus_step_seconds: int = 60
    prometheus_max_series: int = 10
    prometheus_extra_queries: list[str] = Field(
        default_factory=list,
        description="Extra PromQL templates; {label} placeholders are filled from alert labels.",
    )

    # --- Kubernetes ----------------------------------------------------------
    k8s_enabled: bool = False
    k8s_max_events: int = 30

    # --- log mining / redaction ------------------------------------------------
    max_templates: int = 25
    template_similarity: float = 0.7
    redact_emails: bool = True
    redact_ips: bool = False

    # --- LLM -----------------------------------------------------------------
    llm_provider: LLMProviderName = "anthropic"
    llm_timeout_seconds: float = 120.0
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Effort = "medium"
    anthropic_max_tokens: int = 16000
    anthropic_server_fallback: bool = True
    openai_base_url: str = "http://localhost:11434/v1"
    openai_api_key: SecretStr | None = None
    openai_model: str = "llama3.1:8b"

    # --- Slack ---------------------------------------------------------------
    slack_webhook_url: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
