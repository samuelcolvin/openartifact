"""HTTP server: the MCP server plus serving of the browser runtime and built artifacts.

Routes:

    /mcp/                       the MCP endpoint (streamable HTTP) from `mcp_server.py`, behind Google login
    /openartifact.js            the browser runtime, `frontend/dist/openartifact.js`, which every built page links
    /artifacts/{id}/            an artifact's page, built on demand from its workspace checkout
    /artifacts/{id}/{path}      an image or font from the artifact directory, referenced relatively by the page
    /                           JSON index of the above

Pages are public to anyone holding the artifact's UUID. Only the page and its media are exposed; `main.md`,
`artifact.toml`, `styles.css` and components are already in the page's JSON blob and are not served as files.

Run with `uv run backend/server.py` (`HOST` and `PORT` override the bind address). Configuration is by
environment: `DATABASE_URL`, `OPENARTIFACT_STORE_URL`, `OPENARTIFACT_CACHE_DIR` (one per process, never shared),
`OPENARTIFACT_BASE_URL`, and for auth `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `OPENARTIFACT_SECRET_KEY` or
`OPENARTIFACT_DEV_TOKEN`. The image needs `git`.

Observability is Logfire. FastAPI, FastMCP, asyncpg and pydantic-monty all emit OpenTelemetry; `logfire.configure()`
installs the global providers they look up, and asyncpg and monty are handed theirs explicitly. Data is sent when
`LOGFIRE_TOKEN` is set and printed to the console either way.
"""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import config
import logfire
import pydantic_monty
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from opentelemetry import _logs, metrics, trace

import build
import db
import mcp_server
import store
import workspace

# openartifact.js is not packaged; it is read from the frontend build output in this checkout.
RUNTIME_JS_PATH = config.ROOT / 'frontend' / 'dist' / 'openartifact.js'
# What `/artifacts/{id}/{path}` will hand out from the artifact directory: images the build checked, plus fonts
# that `styles.css` may declare with `@font-face`.
SERVED_EXTS = (*build.IMAGE_EXTS, '.woff', '.woff2')


def configure_telemetry() -> None:
    """Configure Logfire and hook up the libraries that need telling: asyncpg and monty.

    FastAPI (its own `telemetry` support) and FastMCP (`telemetry_mode='native'`) emit spans through the global
    OpenTelemetry providers that `logfire.configure()` installs, so they need nothing more. Monty wants its tracer,
    meter and logger handed over once per process; this is what `logfire.instrument_monty()` will do in newer
    Logfire releases.
    """
    logfire.configure(service_name='openartifact', send_to_logfire='if-token-present')
    logfire.instrument_asyncpg()
    pydantic_monty.instrument_telemetry(
        tracer=trace.get_tracer('pydantic_monty'),
        meter=metrics.get_meter('pydantic_monty'),
        logger=_logs.get_logger('pydantic_monty'),
    )


configure_telemetry()

# `path='/'` inside the mount makes the endpoint `/mcp/`.
mcp_app = mcp_server.mcp.http_app(path='/')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Start FastMCP's session manager, the database pool, the object store and the monty pool; migrate."""
    async with mcp_app.lifespan(app), db.db_pool() as pool, store.object_store(), mcp_server.monty_pool():
        async with pool.acquire() as conn:
            await db.migrate(conn)
        config.cache_dir().mkdir(parents=True, exist_ok=True)
        yield


# `auto_configure=False` stops FastAPI adding its own OTLP exporters from `OTEL_*` variables: Logfire owns export here,
# and both would double-send.
app = FastAPI(title='openartifact', lifespan=lifespan, telemetry={'auto_configure': False})
app.mount('/mcp', mcp_app)


async def load_artifact(artifact_id: str) -> workspace.Artifact:
    """The artifact for a URL segment, or 404; a value that is not a UUID is also a 404, not a validation error."""
    try:
        parsed = uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(404, f'invalid artifact id {artifact_id!r}') from None
    found = await workspace.get_artifact(parsed)
    if found is None:
        raise HTTPException(404, f'artifact {artifact_id} not found')
    return found


def contained_file(directory: Path, relative: str, allowed: tuple[str, ...]) -> Path:
    """Resolve a URL path under an artifact directory to an existing file with an allowed extension, or raise 404.

    `relative` comes from the URL, so the resolved path must stay inside the directory: `..` segments and
    symlinks pointing elsewhere are rejected by the containment check.
    """
    path = (directory / relative).resolve()
    if directory.resolve() not in path.parents or path.suffix.lower() not in allowed or not path.is_file():
        raise HTTPException(404, f'{relative!r} not found')
    return path


@app.get('/')
def index() -> dict[str, object]:
    """The MCP endpoint and the runtime; artifacts are listed per user by the `list_artifacts` tool."""
    return {'mcp': '/mcp/', 'runtime': '/openartifact.js'}


@app.get('/openartifact.js')
def runtime_js() -> FileResponse:
    """The browser runtime; every built page loads it from here."""
    if not RUNTIME_JS_PATH.is_file():
        raise HTTPException(404, f'{RUNTIME_JS_PATH} is missing: run `pnpm -C frontend build`')
    return FileResponse(RUNTIME_JS_PATH, media_type='text/javascript')


@app.get('/artifacts/{artifact_id}')
def artifact_redirect(artifact_id: str) -> RedirectResponse:
    """Send `/artifacts/x` to `/artifacts/x/` so the page's relative image references resolve under it."""
    return RedirectResponse(f'/artifacts/{artifact_id}/')


@app.get('/artifacts/{artifact_id}/')
async def artifact_index(artifact_id: str) -> HTMLResponse:
    """The artifact's page, built now if this process has not built the current version yet."""
    found = await load_artifact(artifact_id)
    async with workspace.open_artifact(found) as directory:
        page = directory / 'dist' / 'index.html'
        if not page.is_file():
            try:
                await asyncio.to_thread(build.build_html, directory)
            except build.BuildError as exc:
                raise HTTPException(422, f'build failed: {exc}') from exc
        # Read under the lock: a sync for a newer head could delete dist/ between here and the response otherwise.
        html = page.read_text(encoding='utf-8')
    return HTMLResponse(html)


@app.get('/artifacts/{artifact_id}/{path:path}')
async def artifact_media(artifact_id: str, path: str) -> Response:
    """An image or font the page references relatively, served from the artifact directory."""
    found = await load_artifact(artifact_id)
    async with workspace.open_artifact(found) as directory:
        file = contained_file(directory, path, SERVED_EXTS)
        data = file.read_bytes()
    media_type, _ = mimetypes.guess_type(file.name)
    return Response(data, media_type=media_type or 'application/octet-stream')


if __name__ == '__main__':
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')))
    sys.exit(0)
