"""Pluggable LLM providers."""

from __future__ import annotations

from ..config import Settings
from .base import LLMError, LLMProvider, parse_summary
from .offline import OfflineProvider
from .openai_compat import OpenAICompatProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
            max_tokens=settings.anthropic_max_tokens,
            server_fallback=settings.anthropic_server_fallback,
            timeout=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "openai":
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        return OpenAICompatProvider(
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            api_key=key,
            timeout=settings.llm_timeout_seconds,
        )
    return OfflineProvider()


__all__ = ["LLMError", "LLMProvider", "OfflineProvider", "build_provider", "parse_summary"]
