"""HTTP server: the MCP server plus static serving of the browser runtime and built artifacts.

Routes:

    /mcp/                       the MCP endpoint (streamable HTTP) from `mcp_server.py`
    /deck.js                    the browser runtime, `frontend/dist/deck.js`
    /artifacts/{name}/          a built artifact's `dist/index.html`
    /artifacts/{name}/{file}    other files in that artifact's `dist/` (`deck.js`)
    /                           JSON index of the above

Only `dist/` is exposed; the source files an agent writes through `run_code` are never served. Run with
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


def dist_file(name: str, file: str) -> Path:
    """Resolve `/artifacts/{name}/{file}` to a file inside that artifact's `dist/`, or raise 404."""
    if not mcp_server.ARTIFACT_NAME_RE.fullmatch(name):
        raise HTTPException(404, f'invalid artifact name {name!r}')
    dist = mcp_server.artifacts_root() / name / 'dist'
    path = (dist / file).resolve()
    # `file` is a single path segment, but `..` would still be one; the containment check closes that.
    if path.parent != dist or not path.is_file():
        raise HTTPException(404, f'{file!r} not found for artifact {name!r}; has it been built?')
    return path


@app.get('/')
def index() -> dict[str, object]:
    """List the MCP endpoint, the runtime and every artifact that has been built."""
    root = mcp_server.artifacts_root()
    built = sorted(p.parent.parent.name for p in root.glob('*/dist/index.html')) if root.is_dir() else []
    return {
        'mcp': '/mcp/',
        'deck_js': '/deck.js',
        'artifacts': {name: mcp_server.artifact_url(name) for name in built},
    }


@app.get('/deck.js')
def deck_js() -> FileResponse:
    """The browser runtime, for pages that want to reference it rather than carry their own copy."""
    if not build.DECK_JS_PATH.is_file():
        raise HTTPException(404, f'{build.DECK_JS_PATH} is missing: run `pnpm -C frontend build`')
    return FileResponse(build.DECK_JS_PATH, media_type='text/javascript')


@app.get('/artifacts/{name}')
def artifact_redirect(name: str) -> RedirectResponse:
    """Send `/artifacts/x` to `/artifacts/x/` so the page's relative `deck.js` reference resolves."""
    return RedirectResponse(f'/artifacts/{name}/')


@app.get('/artifacts/{name}/')
def artifact_index(name: str) -> FileResponse:
    """The built page."""
    return FileResponse(dist_file(name, 'index.html'), media_type='text/html')


@app.get('/artifacts/{name}/{file}')
def artifact_file(name: str, file: str) -> FileResponse:
    """Any other build output, such as `deck.js`."""
    return FileResponse(dist_file(name, file))


if __name__ == '__main__':
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')))
    sys.exit(0)
