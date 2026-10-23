# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# EXTRAS=k8s pulls in the kubernetes client for the optional events/rollouts source.
ARG EXTRAS=k8s
RUN pip install ".[${EXTRAS}]"

FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="aiops-incident-assistant" \
      org.opencontainers.image.description="Alertmanager webhook -> logs/metrics/k8s context -> LLM incident summary" \
      org.opencontainers.image.source="https://github.com/ozy31/aiops-incident-assistant" \
      org.opencontainers.image.licenses="MIT"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
USER 10001:10001
WORKDIR /app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["aiops-assist"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
