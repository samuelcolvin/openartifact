"""HTTP server: the MCP server plus serving of the browser runtime and built artifacts.

Routes:

    /mcp/                       the MCP endpoint (streamable HTTP) from `mcp_server.py`, behind Google login
    /openartifact.js            the browser runtime, `frontend/dist/openartifact.js`, which every built page links
    /artifacts/{id}/            an artifact's page, built on demand from its workspace checkout
    /artifacts/{id}/{path}      an image or font from the artifact directory, referenced relatively by the page
    /                           JSON index of the above

Pages are public to anyone holding the artifact's UUID, and so are their sources: `main.md`, `artifact.toml`,
`styles.css` and `components/*` are served as text next to the page (they are in the page anyway), so an agent can
read the markdown directly. Only `dist/` is withheld.

Run by `main.py`. Configuration is by environment: `DATABASE_URL`, `OPENARTIFACT_STORE_URL`,
`OPENARTIFACT_CACHE_DIR` (one per process, never shared), `OPENARTIFACT_BASE_URL`, and for auth `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` / `OPENARTIFACT_SECRET_KEY` or `OPENARTIFACT_DEV_TOKEN`. The image needs `git`.
"""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import config
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import build
import db
import mcp_server
import store
import workspace

# openartifact.js is not packaged; it is read from the frontend build output in this checkout.
RUNTIME_JS_PATH = config.ROOT / 'frontend' / 'dist' / 'openartifact.js'
# What `/artifacts/{id}/{path}` will hand out from the artifact directory: images the build checked, fonts that
# `styles.css` may declare with `@font-face`, and the source files themselves (`main.md`, `artifact.toml`,
# `styles.css`, `components/*`), so a reader can fetch the markdown instead of parsing the page. The build output
# under `dist/` is not served this way: the page itself is `/artifacts/{id}/`.
SOURCE_EXTS = ('.md', '.toml', '.css', '.html')
SERVED_EXTS = (*build.IMAGE_EXTS, '.woff', '.woff2', *SOURCE_EXTS)
# Source files are sent as text so a browser shows them rather than rendering an HTML fragment.
SOURCE_MEDIA_TYPES = {
    '.md': 'text/markdown; charset=utf-8',
    '.toml': 'text/plain; charset=utf-8',
    '.css': 'text/plain; charset=utf-8',
    '.html': 'text/plain; charset=utf-8',
}


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
# and both would double-send. `/health/` is the container health check; tracing it would only add noise.
app = FastAPI(
    title='openartifact',
    lifespan=lifespan,
    telemetry={'auto_configure': False, 'exclude': lambda scope: scope['path'] == '/health/'},
)
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
    root = directory.resolve()
    path = (directory / relative).resolve()
    if root not in path.parents or path.suffix.lower() not in allowed or not path.is_file():
        raise HTTPException(404, f'{relative!r} not found')
    if path.relative_to(root).parts[0] == 'dist':
        raise HTTPException(404, f'{relative!r} not found')
    return path


@app.get('/')
def index() -> dict[str, object]:
    """The MCP endpoint and the runtime; artifacts are listed per user by the `list_artifacts` tool."""
    return {'mcp': '/mcp/', 'runtime': '/openartifact.js'}


@app.get('/health/')
def health() -> dict[str, str]:
    """Liveness probe for the container health check; touches nothing, so it answers while the app is up."""
    return {'status': 'ok'}


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
    """An image or font the page references relatively, or one of the source files, from the artifact directory."""
    found = await load_artifact(artifact_id)
    async with workspace.open_artifact(found) as directory:
        file = contained_file(directory, path, SERVED_EXTS)
        data = file.read_bytes()
    media_type = SOURCE_MEDIA_TYPES.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0]
    return Response(data, media_type=media_type or 'application/octet-stream')
