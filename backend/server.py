"""HTTP server: the MCP server plus static serving of the browser runtime and built artifacts.

Routes:

    /mcp/                       the MCP endpoint (streamable HTTP) from `mcp_server.py`
    /openartifact.js                    the browser runtime, `frontend/dist/openartifact.js`, which every built page links
    /artifacts/{name}/          a built artifact's `dist/index.html`
    /artifacts/{name}/{path}    an image or font from the artifact directory, referenced relatively by the page
    /                           JSON index of the above

Only the page and its media are exposed; `deck.md`, `artifact.toml`, `styles.css` and components are already in
the page's JSON blob and are not served as files. Run with
`uv run backend/server.py` (`HOST` and `PORT` override the bind address). `OPENARTIFACT_BASE_URL` is the public URL
the `build` tool reports artifacts under.

Observability is Logfire. FastAPI, FastMCP and pydantic-monty all emit OpenTelemetry natively, so `logfire.configure()`
is the only setup: it installs the global providers they look up. Data is sent when `LOGFIRE_TOKEN` is set and
printed to the console either way.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import logfire
import pydantic_monty
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from opentelemetry import _logs, metrics, trace

import build
import mcp_server

# openartifact.js is not packaged; it is read from the frontend build output in this checkout.
RUNTIME_JS_PATH = mcp_server.ROOT / 'frontend' / 'dist' / 'openartifact.js'
# What `/artifacts/{name}/{path}` will hand out from the artifact directory: images the build checked, plus fonts
# that `styles.css` may declare with `@font-face`.
SERVED_EXTS = (*build.IMAGE_EXTS, '.woff', '.woff2')


def configure_telemetry() -> None:
    """Configure Logfire and hook up the one library that needs telling: monty.

    FastAPI (its own `telemetry` support) and FastMCP (`telemetry_mode='native'`) emit spans through the global
    OpenTelemetry providers that `logfire.configure()` installs, so they need nothing more. Monty wants its tracer,
    meter and logger handed over once per process; this is what `logfire.instrument_monty()` will do in newer
    Logfire releases.
    """
    logfire.configure(service_name='openartifact', send_to_logfire='if-token-present')
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
    """Start FastMCP's session manager and the monty worker pool for the life of the app."""
    async with mcp_app.lifespan(app), mcp_server.monty_pool():
        yield


# `auto_configure=False` stops FastAPI adding its own OTLP exporters from `OTEL_*` variables: Logfire owns export here,
# and both would double-send.
app = FastAPI(title='openartifact', lifespan=lifespan, telemetry={'auto_configure': False})
app.mount('/mcp', mcp_app)


def artifact_file(name: str, relative: str, allowed: tuple[str, ...]) -> Path:
    """Resolve a path under an artifact directory to an existing file with an allowed extension, or raise 404.

    `relative` comes from the URL, so the resolved path must stay inside the artifact directory: `..` segments and
    symlinks pointing elsewhere are rejected by the containment check.
    """
    if not mcp_server.ARTIFACT_NAME_RE.fullmatch(name):
        raise HTTPException(404, f'invalid artifact name {name!r}')
    directory = mcp_server.artifacts_root() / name
    path = (directory / relative).resolve()
    if directory not in path.parents or path.suffix.lower() not in allowed or not path.is_file():
        raise HTTPException(404, f'{relative!r} not found for artifact {name!r}')
    return path


@app.get('/')
def index() -> dict[str, object]:
    """List the MCP endpoint, the runtime and every artifact that has been built."""
    root = mcp_server.artifacts_root()
    built = sorted(p.parent.parent.name for p in root.glob('*/dist/index.html')) if root.is_dir() else []
    return {
        'mcp': '/mcp/',
        'runtime': '/openartifact.js',
        'artifacts': {name: mcp_server.artifact_url(name) for name in built},
    }


@app.get('/openartifact.js')
def runtime_js() -> FileResponse:
    """The browser runtime; every built page loads it from here."""
    if not RUNTIME_JS_PATH.is_file():
        raise HTTPException(404, f'{RUNTIME_JS_PATH} is missing: run `pnpm -C frontend build`')
    return FileResponse(RUNTIME_JS_PATH, media_type='text/javascript')


@app.get('/artifacts/{name}')
def artifact_redirect(name: str) -> RedirectResponse:
    """Send `/artifacts/x` to `/artifacts/x/` so the page's relative image references resolve under it."""
    return RedirectResponse(f'/artifacts/{name}/')


@app.get('/artifacts/{name}/')
def artifact_index(name: str) -> FileResponse:
    """The built page, `dist/index.html`; a 404 means the artifact has not been built."""
    return FileResponse(artifact_file(name, 'dist/index.html', ('.html',)), media_type='text/html')


@app.get('/artifacts/{name}/{path:path}')
def artifact_media(name: str, path: str) -> FileResponse:
    """An image or font the page references relatively, served from the artifact directory."""
    return FileResponse(artifact_file(name, path, SERVED_EXTS))


if __name__ == '__main__':
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')))
    sys.exit(0)
