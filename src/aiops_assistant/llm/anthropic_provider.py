"""Anthropic (Claude) provider using the official ``anthropic`` Python SDK.

* Structured output: ``output_config.format`` with a JSON schema, so the model
  returns a single JSON object that matches :class:`IncidentSummary`.
* Effort: ``output_config.effort`` (``AIOPS_ANTHROPIC_EFFORT``; empty = model default).
* Server-side fallback: when enabled, requests go through the beta Messages
  endpoint with ``fallbacks="default"`` so a policy decline on the primary model
  is retried server-side on the model's default fallback.
* Credentials are resolved by the SDK (``ANTHROPIC_API_KEY`` and friends).
"""

from __future__ import annotations

from typing import Any, cast

import anthropic
from anthropic.types import OutputConfigParam
from anthropic.types.beta import BetaOutputConfigParam

from ..models import INCIDENT_SUMMARY_JSON_SCHEMA, IncidentSummary
from .base import LLMError, parse_summary

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        effort: str = "medium",
        max_tokens: int = 16000,
        server_fallback: bool = True,
        timeout: float = 120.0,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._server_fallback = server_fallback
        self._client = client or anthropic.AsyncAnthropic(timeout=timeout, max_retries=2)

    def _output_config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": INCIDENT_SUMMARY_JSON_SCHEMA}
        }
        if self._effort:
            cfg["effort"] = self._effort
        return cfg

    async def summarize(self, system: str, prompt: str) -> IncidentSummary:
        try:
            if self._server_fallback:
                beta_resp = await self._client.beta.messages.create(
                    model=self.model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    output_config=cast(BetaOutputConfigParam, self._output_config()),
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                )
                stop_reason: str | None = beta_resp.stop_reason
                text = "".join(b.text for b in beta_resp.content if b.type == "text")
            else:
                resp = await self._client.messages.create(
                    model=self.model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    output_config=cast(OutputConfigParam, self._output_config()),
                )
                stop_reason = resp.stop_reason
                text = "".join(b.text for b in resp.content if b.type == "text")
        except anthropic.RateLimitError as exc:
            raise LLMError(f"anthropic rate limited: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"anthropic connection error: {exc}") from exc

        if stop_reason == "refusal":
            raise LLMError("model declined the request (stop_reason=refusal)")
        if stop_reason == "max_tokens":
            raise LLMError("model output truncated (max_tokens); raise AIOPS_ANTHROPIC_MAX_TOKENS")
        return parse_summary(text)
