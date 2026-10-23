"""Shrink thousands of log lines into a handful of templates.

Two passes:

1. **Normalization** - variable tokens (UUIDs, hex ids, IPs, numbers, durations,
   quoted strings, timestamps) are replaced with typed placeholders, so
   ``timeout after 3012ms talking to 10.0.4.7:5672`` and
   ``timeout after 87ms talking to 10.0.9.1:5672`` collapse to one key.
2. **Drain-style merge** - templates with the same token count and first token
   whose remaining tokens agree above a similarity threshold are merged, with
   differing positions replaced by ``<*>``. This is a simplified, single-level
   version of the Drain algorithm (He et al., ICWS 2017), good enough to cut
   prompt size without a dependency.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .models import LogLine, LogTemplate

_MAX_LINE_CHARS = 600

_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
        ),
        "<TS>",
    ),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<HEX>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|us|µs|ns|m|h)\b"), "<DUR>"),
    (re.compile(r"\"[^\"]{0,200}\"|'[^']{0,200}'"), "<STR>"),
    (re.compile(r"(?<![A-Za-z<])\d+(?:\.\d+)?\b"), "<NUM>"),
)


def normalize(message: str) -> str:
    """Replace variable parts of a log message with typed placeholders."""
    text = message.strip()[:_MAX_LINE_CHARS]
    for pattern, placeholder in _NORMALIZERS:
        text = pattern.sub(placeholder, text)
    return re.sub(r"\s+", " ", text)


@dataclass
class _Cluster:
    tokens: list[str]
    count: int
    example: str
    first_seen: datetime | None
    last_seen: datetime | None
    sources: set[str] = field(default_factory=set)

    def similarity(self, other: list[str]) -> float:
        # As in Drain, wildcard positions do not count as matches.
        same = sum(1 for a, b in zip(self.tokens, other, strict=True) if a == b != "<*>")
        return same / len(self.tokens)

    def absorb(self, tokens: list[str], other: _Cluster) -> None:
        self.tokens = [a if a == b else "<*>" for a, b in zip(self.tokens, tokens, strict=True)]
        self.count += other.count
        self.sources |= other.sources
        self.first_seen = _min_dt(self.first_seen, other.first_seen)
        self.last_seen = _max_dt(self.last_seen, other.last_seen)


def _min_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    return b if a is None else a if b is None else min(a, b)


def _max_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    return b if a is None else a if b is None else max(a, b)


def mine_templates(
    lines: Iterable[LogLine], *, max_templates: int = 25, similarity: float = 0.7
) -> list[LogTemplate]:
    """Cluster log lines and return the ``max_templates`` most frequent templates."""
    # pass 1: exact grouping on normalized text
    exact: dict[str, _Cluster] = {}
    for line in lines:
        key = normalize(line.message)
        if not key:
            continue
        cluster = exact.get(key)
        if cluster is None:
            exact[key] = _Cluster(
                tokens=key.split(" "),
                count=1,
                example=line.message.strip()[:_MAX_LINE_CHARS],
                first_seen=line.timestamp,
                last_seen=line.timestamp,
                sources={line.source},
            )
        else:
            cluster.count += 1
            cluster.sources.add(line.source)
            cluster.first_seen = _min_dt(cluster.first_seen, line.timestamp)
            cluster.last_seen = _max_dt(cluster.last_seen, line.timestamp)

    # pass 2: drain-style merge within (length, first token) buckets
    buckets: dict[tuple[int, str], list[_Cluster]] = {}
    for cluster in sorted(exact.values(), key=lambda c: c.count, reverse=True):
        bucket = buckets.setdefault((len(cluster.tokens), cluster.tokens[0]), [])
        for existing in bucket:
            if len(cluster.tokens) > 1 and existing.similarity(cluster.tokens) >= similarity:
                existing.absorb(cluster.tokens, cluster)
                break
        else:
            bucket.append(cluster)

    merged = [c for bucket in buckets.values() for c in bucket]
    merged.sort(key=lambda c: c.count, reverse=True)
    return [
        LogTemplate(
            template=" ".join(c.tokens),
            count=c.count,
            example=c.example,
            first_seen=c.first_seen,
            last_seen=c.last_seen,
            sources=sorted(c.sources),
        )
        for c in merged[:max_templates]
    ]
