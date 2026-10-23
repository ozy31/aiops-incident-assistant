"""Minimal OpenAI-compatible ``/chat/completions`` provider.

Works with any server that speaks that API (Ollama, vLLM, llama.cpp server,
LiteLLM, LM Studio, hosted gateways). Uses plain httpx to avoid another SDK.
"""

from __future__ import annotations

import httpx

from ..models import INCIDENT_SUMMARY_JSON_SCHEMA, IncidentSummary
from .base import LLMError, parse_summary


class OpenAICompatProvider:
    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def summarize(self, system: str, prompt: str) -> IncidentSummary:
        body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            # Servers that don't support json_schema usually ignore it; the
            # schema is also enforced client-side by parse_summary().
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "incident_summary",
                    "schema": INCIDENT_SUMMARY_JSON_SCHEMA,
                    "strict": True,
                },
            },
        }
        try:
            resp = await self._client.post(self._url, json=body, headers=self._headers)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise LLMError(f"openai-compatible endpoint error: {exc}") from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape from openai-compatible endpoint") from exc
        return parse_summary(text or "")
