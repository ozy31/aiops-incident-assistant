from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiops_assistant.logmining import mine_templates, normalize
from aiops_assistant.models import LogLine


def _lines(messages: list[str]) -> list[LogLine]:
    t0 = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
    return [
        LogLine(timestamp=t0 + timedelta(seconds=i), source="loki:test", message=m)
        for i, m in enumerate(messages)
    ]


def test_normalize_variable_tokens() -> None:
    a = normalize(
        "timeout after 3012ms talking to 10.0.4.7:5672 req=5f0c9a2e-1b7d-4c1e-9a43-2f6b8d0e1c3a"
    )
    b = normalize(
        "timeout after 87ms talking to 10.0.9.1:5672 req=0b8e1f6a-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
    )
    assert a == b
    assert "<DUR>" in a and "<IP>" in a and "<UUID>" in a


def test_normalize_timestamps_and_numbers() -> None:
    out = normalize('2026-10-20T14:00:01.123Z ERROR order 12345 failed: "card declined"')
    assert out == "<TS> ERROR order <NUM> failed: <STR>"


def test_exact_grouping_and_counts() -> None:
    msgs = [f"payment failed for order {i}" for i in range(50)] + ["cache miss key=abc"] * 3
    templates = mine_templates(_lines(msgs))
    assert templates[0].count == 50
    assert templates[0].template == "payment failed for order <NUM>"
    assert templates[0].first_seen is not None
    assert templates[0].last_seen is not None
    assert templates[0].first_seen < templates[0].last_seen
    assert templates[1].count == 3


def test_drain_style_merge_of_differing_words() -> None:
    msgs = (
        ["connection refused by payments-db"] * 5
        + ["connection refused by inventory-db"] * 3
        + ["connection reset by peer"] * 2
    )
    templates = mine_templates(_lines(msgs), similarity=0.7)
    assert len(templates) == 2
    merged = templates[0]
    assert merged.template == "connection refused by <*>"
    assert merged.count == 8


def test_max_templates_and_ordering() -> None:
    msgs = []
    for i in range(30):
        msgs += [f"error kind{i}a happened in module{i}b with code{i}c"] * (i + 1)
    templates = mine_templates(_lines(msgs), max_templates=5, similarity=1.0)
    assert len(templates) == 5
    assert [t.count for t in templates] == sorted((t.count for t in templates), reverse=True)


def test_large_input_shrinks() -> None:
    msgs = [
        f"2026-10-20T14:{i % 60:02d}:00Z WARN slow query {i}ms on shard {i % 4}"
        for i in range(2000)
    ]
    templates = mine_templates(_lines(msgs))
    assert len(templates) == 1
    assert templates[0].count == 2000
