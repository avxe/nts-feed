.PHONY: help setup quickstart install install-dev test lint format clean \
       docker-build docker-up docker-down docker-prod docker-dev docker-logs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup: ## Interactive setup wizard — generates .env with feature selection
	@bash ./setup.sh

quickstart: setup docker-build docker-up ## Run setup, build, and start containers

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

docker-build: ## Build Docker image
	docker compose build

docker-up: ## Start tracked dev containers
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

docker-down: ## Stop containers
	docker compose down

docker-prod: ## Start the production compose file only
	docker compose -f docker-compose.yml up -d

docker-dev: ## Foreground tracked dev stack
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

docker-logs: ## Tail container logs
	docker compose logs -f --tail=100
