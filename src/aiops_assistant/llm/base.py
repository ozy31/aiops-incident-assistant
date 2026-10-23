"""Provider interface shared by all LLM backends."""

from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import ValidationError

from ..models import IncidentSummary


class LLMError(RuntimeError):
    """Raised when a provider fails or returns something unusable."""


class LLMProvider(Protocol):
    """Anything that can turn a prompt into an :class:`IncidentSummary`."""

    name: str
    model: str

    async def summarize(self, system: str, prompt: str) -> IncidentSummary: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_summary(text: str) -> IncidentSummary:
    """Parse and validate model output. Tolerates a surrounding Markdown fence."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("model output contained no JSON object")
    try:
        return IncidentSummary.model_validate(json.loads(cleaned[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMError(f"model output did not match the summary schema: {exc}") from exc
