"""FastAPI service: Alertmanager webhook receiver + metrics + health."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .config import Settings, get_settings
from .llm import LLMError
from .logsetup import configure_logging
from .models import Alert, AlertmanagerWebhook, AnalysisResult
from .pipeline import Analyzer

log = structlog.get_logger(__name__)


def get_analyzer(request: Request) -> Analyzer:
    return request.app.state.analyzer  # type: ignore[no-any-return]


AnalyzerDep = Annotated[Analyzer, Depends(get_analyzer)]


def check_token(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    settings: Settings = request.app.state.settings
    if settings.webhook_token is None:
        return
    expected = f"Bearer {settings.webhook_token.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


async def run_all(analyzer: Analyzer, alerts: list[Alert]) -> list[AnalysisResult | str]:
    async def one(alert: Alert) -> AnalysisResult | str:
        try:
            return await analyzer.analyze(alert)
        except LLMError as exc:
            log.error("analysis failed", alert=alert.name, error=str(exc))
            return f"{alert.name}: {exc}"

    return list(await asyncio.gather(*(one(a) for a in alerts)))


def create_app(settings: Settings | None = None, analyzer: Analyzer | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.analyzer = analyzer or Analyzer(settings)
        log.info(
            "service started",
            version=__version__,
            provider=app.state.analyzer.provider.name,
            model=app.state.analyzer.provider.model,
            log_sources=[s.name for s in app.state.analyzer.log_sources],
            metric_sources=[s.name for s in app.state.analyzer.metric_sources],
            k8s=app.state.analyzer.k8s_source is not None,
            slack=app.state.analyzer.slack is not None,
        )
        yield
        await app.state.analyzer.aclose()

    app = FastAPI(
        title="aiops-incident-assistant",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(analyzer: AnalyzerDep) -> dict[str, Any]:
        return {"status": "ready", "provider": analyzer.provider.name}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/webhook/alertmanager", dependencies=[Depends(check_token)], response_model=None)
    async def alertmanager_webhook(
        payload: AlertmanagerWebhook,
        analyzer: AnalyzerDep,
        background: BackgroundTasks,
        wait: bool = False,
    ) -> JSONResponse:
        """Receive an Alertmanager webhook.

        By default the analysis runs in the background and 202 is returned at
        once (Alertmanager's webhook timeout is short). ``?wait=true`` runs it
        inline and returns the results - handy for curl and tests.
        """
        alerts = analyzer.select_alerts(payload)
        if not alerts:
            return JSONResponse({"accepted": 0, "results": []})
        if not wait:
            background.add_task(run_all, analyzer, alerts)
            return JSONResponse({"accepted": len(alerts)}, status_code=202)
        results = await run_all(analyzer, alerts)
        return JSONResponse(
            {
                "accepted": len(alerts),
                "results": [r.model_dump(mode="json") for r in results if not isinstance(r, str)],
                "errors": [r for r in results if isinstance(r, str)],
            }
        )

    @app.post("/analyze", dependencies=[Depends(check_token)])
    async def analyze_one(alert: Alert, analyzer: AnalyzerDep) -> AnalysisResult:
        """Analyze a single alert synchronously (no Slack post)."""
        try:
            return await analyzer.analyze(alert, notify=False)
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory aiops_assistant.api:app_factory``."""
    return create_app()
