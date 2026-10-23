.DEFAULT_GOAL := help
PY      ?= python3.12
VENV    ?= .venv
BIN     := $(VENV)/bin
IMAGE   ?= aiops-incident-assistant:dev
CHART   := deploy/helm/aiops-incident-assistant

.PHONY: help venv install lint fmt typecheck test check run analyze docker helm-lint demo-up demo-down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

venv: ## Create virtualenv
	$(PY) -m venv $(VENV)

install: venv ## Install package with dev extras
	$(BIN)/pip install -U pip
	$(BIN)/pip install -e ".[dev]"

lint: ## Ruff lint + format check
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

fmt: ## Auto-format
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

typecheck: ## mypy
	$(BIN)/mypy

test: ## pytest
	$(BIN)/pytest

check: lint typecheck test ## All local checks

run: ## Run the service on :8080
	$(BIN)/aiops-assist serve --port 8080

analyze: ## Analyze the sample alert offline (no LLM)
	$(BIN)/aiops-assist analyze --alert-file examples/alert-sample.json --provider none

docker: ## Build container image
	docker build -t $(IMAGE) .

helm-lint: ## Lint and render the Helm chart
	helm lint $(CHART)
	helm template test $(CHART) > /dev/null

demo-up: ## Start the docker compose demo
	docker compose -f deploy/compose/docker-compose.yml up -d --build

demo-down: ## Stop the demo
	docker compose -f deploy/compose/docker-compose.yml down -v

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
