# The render image: Chromium plus the small FastAPI service in render/, which prints artifact pages to PDF for the
# application server's /artifacts/{id}.pdf. Built from the repository root so it can use the lock file:
#
#   docker build -f render/Dockerfile -t openartifact-render .
#
# The service prints any URL it is given, so it is internal: docker-compose.yml puts it on the private network
# and only binds it to 127.0.0.1 for `make dev` on the host.

# --- Python dependencies ----------------------------------------------------
FROM python:3.14-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
# Only the `render` dependency group: FastAPI, uvicorn and httpx2, not the application server's stack.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --only-group render

# --- The service --------------------------------------------------------------
FROM python:3.14-slim
# apt over https: deb.debian.org serves it, and some networks block plain http.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-liberation fonts-noto-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=deps /app/.venv ./.venv
COPY render ./render
USER app
# RENDER_NO_SANDBOX: Chromium's sandbox needs user namespaces, which Docker does not grant; see render/pdf.py.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8766 \
    RENDER_NO_SANDBOX=1
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
    CMD python -c "import os, httpx2; httpx2.get(f'http://127.0.0.1:{os.environ['PORT']}/health/').raise_for_status()" || exit 1
CMD ["python", "-m", "render.server"]
