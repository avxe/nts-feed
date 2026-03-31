.PHONY: help setup ensure-env env-check runtime-bootstrap quickstart quickstart-dev install install-dev test lint format clean \
       docker-check docker-check-dev docker-build docker-up docker-down docker-prod docker-dev docker-logs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup: ## Interactive setup wizard — generates .env with feature selection
	@bash ./setup.sh

ensure-env: ## Create .env if missing, otherwise reuse the existing file
	@if [ -f .env ]; then \
		printf "Using existing .env\\n"; \
	else \
		bash ./setup.sh; \
	fi

env-check: ## Validate the current .env before startup
	@bash ./scripts/check-env.sh --for-docker

runtime-bootstrap: ## Ensure local runtime files and directories exist
	@bash ./scripts/bootstrap-runtime.sh

quickstart: ensure-env env-check runtime-bootstrap docker-build docker-up ## Create .env if needed, validate it, then start the production-style local stack

quickstart-dev: ensure-env env-check runtime-bootstrap docker-build docker-dev ## Create .env if needed, validate it, then start the hot-reload dev stack

# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

install: ## Install production dependencies
	python -m pip install .

install-dev: ## Install dev + production dependencies
	python -m pip install -e .[dev]

test: ## Run tests with pytest
	python -m pytest tests/ -v

lint: ## Run ruff linter
	ruff check .

format: ## Auto-format code with ruff
	ruff check --fix .
	ruff format .

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .pytest_cache/ .ruff_cache/

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-check: ## Verify Docker, Compose/buildx, and the daemon are ready
	@bash ./scripts/check-docker.sh

docker-check-dev: ## Verify Docker prerequisites for the dev overlay
	@bash ./scripts/check-docker.sh --dev

docker-build: docker-check ## Build Docker image
	docker compose build

docker-up: env-check runtime-bootstrap docker-check ## Start the production-style local stack
	docker compose -f docker-compose.yml up -d

docker-down: ## Stop containers
	docker compose down

docker-prod: docker-up ## Alias for the production-style local stack

docker-dev: env-check runtime-bootstrap docker-check-dev ## Foreground dev stack with hot reload
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

docker-logs: docker-check ## Tail container logs
	docker compose logs -f --tail=100
