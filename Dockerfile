# The application image: the FastAPI server with the MCP endpoint, openartifact.js and artifact pages.
#
# Two build stages feed the final one: node builds frontend/dist/ (openartifact.js and the web app), uv installs the locked Python
# dependencies into a virtualenv. The final image is python:3.14-slim plus git (artifacts are git repositories).
# Chrome is deliberately not here: PDF export is the chrome image (chrome/Dockerfile), which the app calls over HTTP.
#
#   docker build -t openartifact .
#   docker compose up app          # with the Postgres from docker-compose.yml
#
# Runtime configuration is by environment, see backend/server.py. Inside the container the checkout cache and the
# local object store default to /data, so mount a volume there unless OPENARTIFACT_STORE_URL points at S3.

# --- openartifact.js and the web app -----------------------------------------
FROM node:24-slim AS frontend
WORKDIR /build
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/app ./app
RUN pnpm build

# --- Python dependencies ----------------------------------------------------
FROM python:3.14-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /usr/local/bin/uv
# git: uv fetches the dependencies that pyproject.toml sources from git (see [tool.uv.sources]) with it.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
# The project is not a package (`tool.uv.package = false`), so this installs exactly the locked dependencies.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# --- The server -------------------------------------------------------------
FROM python:3.14-slim
# apt over https: deb.debian.org serves it, and some networks block plain http.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data/store /data/cache && chown -R app:app /data
WORKDIR /app
COPY --from=deps /app/.venv ./.venv
COPY --from=frontend /build/dist ./frontend/dist
COPY backend ./backend
COPY skills ./skills
USER app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8765 \
    OPENARTIFACT_CACHE_DIR=/data/cache \
    OPENARTIFACT_STORE_URL=file:///data/store
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
    CMD python -c "import os, httpx2; httpx2.get(f'http://127.0.0.1:{os.environ['PORT']}/health/').raise_for_status()" || exit 1
CMD ["python", "backend/main.py"]
