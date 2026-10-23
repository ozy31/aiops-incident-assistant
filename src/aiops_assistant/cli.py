"""Command-line interface: ``aiops-assist``."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from .config import Settings
from .llm import LLMError, OfflineProvider
from .llm.prompt import SYSTEM_PROMPT, build_prompt
from .logsetup import configure_logging
from .models import Alert, AlertmanagerWebhook
from .pipeline import Analyzer

app = typer.Typer(
    help="Enrich Alertmanager alerts with logs/metrics/k8s context and summarize with an LLM.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_alerts(path: Path) -> list[Alert]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "alerts" in data:
        return AlertmanagerWebhook.model_validate(data).alerts
    if isinstance(data, list):
        return [Alert.model_validate(a) for a in data]
    return [Alert.model_validate(data)]


@app.command()
def analyze(
    alert_file: Annotated[
        Path,
        typer.Option(
            "--alert-file", "-f", exists=True, readable=True, help="Webhook payload or alert JSON"
        ),
    ],
    provider: Annotated[
        str | None, typer.Option(help="Override AIOPS_LLM_PROVIDER: anthropic | openai | none")
    ] = None,
    show_prompt: Annotated[
        bool, typer.Option("--show-prompt", help="Print the redacted prompt and exit (no LLM)")
    ] = False,
    slack: Annotated[bool, typer.Option(help="Also post the result to Slack")] = False,
) -> None:
    """Analyze alert(s) from a file and print the result as JSON."""
    overrides = {"llm_provider": provider} if provider else {}
    settings = Settings(**overrides)  # type: ignore[arg-type]
    configure_logging(settings.log_level, "console")
    alerts = _load_alerts(alert_file)
    if not alerts:
        typer.echo("no alerts in file", err=True)
        raise typer.Exit(1)

    async def run() -> int:
        analyzer = Analyzer(settings, provider=OfflineProvider() if show_prompt else None)
        rc = 0
        try:
            for alert in alerts:
                if show_prompt:
                    ctx = await analyzer.gather_context(alert)
                    typer.echo(
                        f"--- system ---\n{SYSTEM_PROMPT}\n--- user ---\n{build_prompt(ctx)}"
                    )
                    continue
                try:
                    result = await analyzer.analyze(alert, notify=slack)
                    typer.echo(result.model_dump_json(indent=2))
                except LLMError as exc:
                    typer.echo(f"error: {alert.name}: {exc}", err=True)
                    rc = 2
        finally:
            await analyzer.aclose()
        return rc

    raise typer.Exit(asyncio.run(run()))


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address")] = "0.0.0.0",  # noqa: S104
    port: Annotated[int, typer.Option(help="Port")] = 8080,
) -> None:
    """Run the webhook service."""
    import uvicorn

    uvicorn.run(
        "aiops_assistant.api:app_factory",
        factory=True,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    app()
