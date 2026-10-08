.DEFAULT_GOAL := main

# Bearer token the dev server and `docker-up` accept when Google login is not configured.
OPENARTIFACT_DEV_TOKEN ?= dev
export OPENARTIFACT_DEV_TOKEN
# Where `make dev` finds the chrome service: `make chrome-dev` on the host, or the compose container, which
# reaches the host server as host.docker.internal (so set OPENARTIFACT_INTERNAL_URL=http://host.docker.internal:8765
# when using the container).
OPENARTIFACT_CHROME_URL ?= http://127.0.0.1:8766
export OPENARTIFACT_CHROME_URL

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
test: ## Run the Python tests (needs `make pg-start`)
	uv run pytest

.PHONY: main
main: format lint test ## Run formatting, linting and tests

.PHONY: build
build: ## Bundle the browser runtime to frontend/dist/openartifact.js
	pnpm -C frontend build

.PHONY: pg-start
pg-start: ## Run Postgres in Docker on port 5432 (docker-compose.yml; required by `make dev` and `make test`)
	docker compose up -d --wait postgres

.PHONY: pg-stop
pg-stop: ## Stop the database
	docker compose stop postgres

.PHONY: dev
dev: ## Start the server on the host at http://127.0.0.1:8765 with reload, MCP at /mcp/ with the dev token
	uv run uvicorn --app-dir backend main:app --reload --port 8765

.PHONY: chrome-dev
chrome-dev: ## Start the chrome service on the host at http://127.0.0.1:8766, using the local Chrome
	uv run uvicorn chrome.main:app --reload --port 8766

.PHONY: up
up: ## Build the images and start the server, the chrome service and the database via docker compose
	docker compose up --build -d --wait

.PHONY: down
down: ## Stop the server and database
	docker compose down

.PHONY: docker-logs
docker-logs: ## Tail logs from all services
	docker compose logs -f

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
