.DEFAULT_GOAL := help

.PHONY: .uv
.uv:
	@uv --version || echo 'Please install uv: https://docs.astral.sh/uv/getting-started/installation/'

.PHONY: install
install: .uv ## Install Python and JS dependencies, build openartifact.js and install prek hooks
	uv sync
	pnpm -C frontend install
	pnpm -C frontend build
	uvx prek install --install-hooks

.PHONY: format
format: ## Format Python and TypeScript code
	uv run ruff format
	uv run ruff check --fix --fix-only
	pnpm -C frontend format

.PHONY: lint
lint: ## Lint Python with ruff and basedpyright (strict), TypeScript with biome and tsc
	uv run ruff format --check
	uv run ruff check
	uv run basedpyright
	pnpm -C frontend lint
	pnpm -C frontend typecheck

.PHONY: test
test: ## Run the Python tests
	uv run pytest

.PHONY: build
build: ## Bundle the browser runtime to frontend/dist/openartifact.js
	pnpm -C frontend build

.PHONY: serve
serve: ## Run the HTTP server (MCP endpoint, openartifact.js and built artifacts) on http://127.0.0.1:8000
	uv run backend/server.py

.PHONY: main
main: format lint test ## Run formatting, linting and tests

# (must stay last!)
.PHONY: help
help: ## Show this help (usage: make help)
	@echo "Usage: make [recipe]"
	@echo "Recipes:"
	@awk '/^[a-zA-Z0-9_-]+:.*?##/ { \
	    helpMessage = match($$0, /## (.*)/); \
	        if (helpMessage) { \
	            recipe = $$1; \
	            sub(/:/, "", recipe); \
	            printf "  \033[36mmake %-20s\033[0m %s\n", recipe, substr($$0, RSTART + 3, RLENGTH); \
	    } \
	}' $(MAKEFILE_LIST)
