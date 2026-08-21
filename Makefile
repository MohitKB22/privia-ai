# PRIVIA - developer commands.
#
# Every target works offline. Nothing here contacts a network service except
# `install`, which fetches dependencies.

SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help install install-dev venv dev dev-api dev-ui doctor migrate db-reset \
        test test-unit test-integration test-security test-e2e test-ui coverage \
        lint format typecheck security audit build build-ui build-desktop \
        clean ci check

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
UI := apps/desktop

PKG_PATHS := packages/shared-types:packages/security:packages/storage:packages/observability:packages/memory:packages/tool-runtime:packages/agent-core:services/llm:services/embeddings:services/speech:services/integrations:apps/api
export PYTHONPATH := $(PKG_PATHS)

help: ## Show this help
	@echo "PRIVIA - private personal AI"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------

$(VENV): ## Create the virtual environment
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip setuptools wheel

venv: $(VENV) ## Create the virtual environment

install: $(VENV) ## Install runtime dependencies (Python + UI)
	$(BIN)/pip install -e .
	cd $(UI) && npm install --no-audit --no-fund
	@echo ""
	@echo "Installed. Next:  make migrate && make dev"

install-dev: $(VENV) ## Install everything including dev and optional extras
	$(BIN)/pip install -e ".[dev,speech,keychain]"
	cd $(UI) && npm install --no-audit --no-fund

# --- running -----------------------------------------------------------------

dev: ## Run the backend and the UI together
	@trap 'kill 0' EXIT; \
	$(BIN)/python -m privia_api --reload & \
	cd $(UI) && npm run dev & \
	wait

dev-api: ## Run only the backend
	$(BIN)/python -m privia_api --reload

dev-ui: ## Run only the desktop UI
	cd $(UI) && npm run dev

doctor: ## Diagnose this installation
	$(BIN)/python -m privia_api.doctor -v

# --- database ----------------------------------------------------------------

migrate: ## Apply pending database migrations
	$(BIN)/python -m privia_storage.cli up

db-status: ## Show the migration status
	$(BIN)/python -m privia_storage.cli status

db-reset: ## Drop every table and re-apply migrations (destructive)
	$(BIN)/python -m privia_storage.cli reset

# --- tests -------------------------------------------------------------------

test: ## Run the whole Python test suite
	$(BIN)/python -m pytest tests -q

test-unit: ## Unit tests only
	$(BIN)/python -m pytest tests/unit -q

test-integration: ## Integration tests only
	$(BIN)/python -m pytest tests/integration -q

test-security: ## Adversarial security tests only
	$(BIN)/python -m pytest tests/security -q

test-e2e: ## End-to-end API tests only
	$(BIN)/python -m pytest tests/e2e -q

test-ui: ## Frontend tests
	cd $(UI) && npm run test

coverage: ## Test suite with a coverage report
	$(BIN)/python -m pytest tests --cov --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

# --- quality -----------------------------------------------------------------

lint: ## Lint Python and TypeScript
	$(BIN)/ruff check .
	$(BIN)/black --check .
	cd $(UI) && npm run lint

format: ## Auto-format everything
	$(BIN)/ruff check --fix .
	$(BIN)/black .
	cd $(UI) && npm run format

typecheck: ## Type-check Python and TypeScript
	$(BIN)/mypy packages services apps/api
	cd $(UI) && npm run typecheck

security: ## Static security analysis
	$(BIN)/bandit -c pyproject.toml -r packages services apps/api -q
	$(BIN)/python -m pytest tests/security -q

audit: ## Dependency vulnerability audit
	$(BIN)/pip-audit || true
	cd $(UI) && npm audit --omit=dev || true

check: lint typecheck test security ## Everything CI runs

# --- build -------------------------------------------------------------------

build-ui: ## Build the web assets
	cd $(UI) && npm run build

build-desktop: build-ui ## Build the native desktop application (needs Rust)
	cd $(UI) && npm run tauri build

build: ## Build the Python distribution
	$(BIN)/pip install --quiet build
	$(BIN)/python -m build

# --- housekeeping ------------------------------------------------------------

clean: ## Remove build artefacts and caches
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(UI)/dist $(UI)/node_modules/.vite

ci: check ## Alias for check
