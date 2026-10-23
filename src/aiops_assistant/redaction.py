"""Best-effort redaction of secrets and PII before anything is sent to an LLM.

This is a regex-based safety net, not a guarantee. Patterns are ordered so
that the most specific ones (PEM blocks, JWTs, vendor key formats) run before
the generic ``key=value`` rule.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]
    replacement: str


_SENSITIVE_FIELD_NAMES = (
    r"pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key|session[_-]?id|auth"
)

_BASE_RULES: tuple[_Rule, ...] = (
    _Rule(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        "<REDACTED:private_key>",
    ),
    _Rule(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "<REDACTED:jwt>",
    ),
    _Rule(
        "bearer",
        re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 <REDACTED:token>",
    ),
    _Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<REDACTED:aws_key>"),
    _Rule("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"), "<REDACTED:api_key>"),
    _Rule("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "<REDACTED:api_key>"),
    _Rule(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
        "<REDACTED:github_token>",
    ),
    _Rule("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "<REDACTED:slack_token>"),
    _Rule(
        "slack_webhook",
        re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),
        "<REDACTED:slack_webhook>",
    ),
    _Rule(
        "url_credentials",
        re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"),
        r"\1:<REDACTED>@",
    ),
    _Rule(
        "key_value",
        re.compile(
            rf"(?i)([\"']?\b[\w-]*?(?:{_SENSITIVE_FIELD_NAMES})[\w-]*[\"']?\s*[:=]\s*[\"']?)"
            r"(?!<REDACTED|(?:bearer|basic)\s)([^\s\"',;&}]+)"
        ),
        r"\1<REDACTED>",
    ),
)

_EMAIL = _Rule(
    "email",
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "<REDACTED:email>",
)
_IPV4 = _Rule(
    "ipv4",
    re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    "<REDACTED:ip>",
)


@dataclass
class Redactor:
    """Applies redaction rules and keeps a per-rule hit counter."""

    redact_emails: bool = True
    redact_ips: bool = False
    counts: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        rules = list(_BASE_RULES)
        if self.redact_emails:
            rules.append(_EMAIL)
        if self.redact_ips:
            rules.append(_IPV4)
        self._rules = tuple(rules)

    def redact(self, text: str) -> str:
        for rule in self._rules:
            text, n = rule.pattern.subn(rule.replacement, text)
            if n:
                self.counts[rule.name] += n
        return text

    def redact_mapping(self, data: dict[str, str]) -> dict[str, str]:
        return {k: self.redact(v) for k, v in data.items()}
