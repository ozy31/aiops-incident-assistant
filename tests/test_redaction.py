from __future__ import annotations

import pytest

from aiops_assistant.redaction import Redactor


@pytest.mark.parametrize(
    ("raw", "leaked", "rule"),
    [
        ("Authorization: Bearer abcdef0123456789abcdef", "abcdef0123456789abcdef", "bearer"),
        ("using key AKIAIOSFODNN7EXAMPLE for s3", "AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuv", "abcdefghijklmnop", None),
        ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghij", "github_token"),
        ("slack xoxb-1234567890-abcdefghij", "xoxb-1234567890", "slack_token"),
        ("connect mongodb://app:hunter2@mongo-0:27017/db", "hunter2", "url_credentials"),
        ("amqp://guest:guest@rabbitmq:5672/", ":guest@", "url_credentials"),
        ('{"password": "s3cr3t!", "user": "bob"}', "s3cr3t!", "key_value"),
        ("db_password=topsecret retry=3", "topsecret", "key_value"),
        ("client_secret: 9f8e7d6c5b", "9f8e7d6c5b", "key_value"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
            "jwt",
        ),
        ("user jane.doe@example.com failed login", "jane.doe@example.com", "email"),
    ],
)
def test_redacts_secrets(raw: str, leaked: str, rule: str | None) -> None:
    r = Redactor()
    out = r.redact(raw)
    assert leaked not in out
    assert "REDACTED" in out
    if rule:
        assert r.counts[rule] >= 1


def test_private_key_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    assert Redactor().redact(f"loaded {pem} ok") == "loaded <REDACTED:private_key> ok"


def test_ips_are_opt_in() -> None:
    line = "upstream 10.12.0.4:5672 refused"
    assert Redactor().redact(line) == line
    assert "10.12.0.4" not in Redactor(redact_ips=True).redact(line)


def test_emails_can_be_disabled() -> None:
    assert "a@b.io" in Redactor(redact_emails=False).redact("from a@b.io")


def test_plain_log_line_untouched() -> None:
    line = "GET /api/orders 200 12ms"
    assert Redactor().redact(line) == line


def test_redact_mapping_counts() -> None:
    r = Redactor()
    out = r.redact_mapping({"a": "password=x1", "b": "fine"})
    assert out == {"a": "password=<REDACTED>", "b": "fine"}
    assert sum(r.counts.values()) == 1


def test_authorization_header_counted_once() -> None:
    r = Redactor()
    out = r.redact("Authorization: Bearer demo0000fake0000token0000 status=502")
    assert out == "Authorization: Bearer <REDACTED:token> status=502"
    assert dict(r.counts) == {"bearer": 1}
