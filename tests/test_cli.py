from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aiops_assistant.cli import app

from .conftest import SAMPLE

runner = CliRunner()


def test_analyze_offline_provider() -> None:
    result = runner.invoke(app, ["analyze", "--alert-file", str(SAMPLE), "--provider", "none"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["alert_name"] == "HighErrorRate"
    assert data["provider"] == "none"
    assert data["summary"]["confidence"] == "low"


def test_show_prompt_redacts(tmp_path: Path) -> None:
    payload = json.loads(SAMPLE.read_text())
    payload["alerts"][0]["annotations"]["description"] = "db url postgres://u:hunter2@db/x"
    f = tmp_path / "alert.json"
    f.write_text(json.dumps(payload))
    result = runner.invoke(app, ["analyze", "-f", str(f), "--show-prompt"])
    assert result.exit_code == 0, result.output
    assert "--- system ---" in result.stdout
    assert "hunter2" not in result.stdout
    assert "<REDACTED>" in result.stdout


def test_single_alert_object(tmp_path: Path) -> None:
    alert = json.loads(SAMPLE.read_text())["alerts"][0]
    f = tmp_path / "one.json"
    f.write_text(json.dumps(alert))
    result = runner.invoke(app, ["analyze", "-f", str(f), "--provider", "none"])
    assert result.exit_code == 0, result.output


def test_missing_file() -> None:
    result = runner.invoke(app, ["analyze", "-f", "/nonexistent.json"])
    assert result.exit_code != 0
