"""HTTP server: the MCP server plus serving of the browser runtime and built artifacts.

Routes:

    /mcp/                       the MCP endpoint (streamable HTTP) from `mcp_server.py`, behind Google login
    /openartifact.js            the browser runtime, `frontend/dist/openartifact.js`, which every built page links
    /favicon.svg, /favicon.ico  the platform's mark, the tab icon of every page here (`frontend/app/public/`)
    /artifacts/{id}/            an artifact's page, built on demand from its checkout; the markdown
                                export instead when the Accept header prefers text/markdown or text/plain
    /artifacts/{id}/{path}      an image or font from the artifact directory, referenced relatively by the page
    PUT /artifacts/{id}/{path}  an upload to the artifact directory, with a token from the `upload_url` tool
    /artifacts/{id}.md          the markdown source behind a frontmatter summary of the artifact
    /artifacts/{id}.zip         the artifact as a git repository (its files and history) in a zip
    /artifacts/{id}.pdf         the page printed to PDF by the chrome service (`chrome/`)
    /artifacts/{id}.png?page=N  one page of the artifact as a PNG, by the same service (`render.py`)
    /artifacts/{id}.pptx        a deck as an editable PowerPoint file (`powerpoint.py`), by the same service
    /artifacts/{id}.docx        a document or page as a Word file (`word.py`), by the same service
    /artifacts/{id}.json        the artifact's placement, permissions and the viewer's rights, for the toolbar
    POST /artifacts/{id}/fork   copy the artifact into the signed-in viewer's own space
    /print/{token}/artifacts/{id}/...   the page and its media for the chrome service, by a short-lived pass
    /login, /login/google, /login/callback, /logout, /login/dev   browser sign-in (`login.py`)
    /authorize, /token, /consent, /auth/callback, /.well-known/*  the MCP OAuth routes (FastMCP, root mount)
    /, /edit/{id}               the web app's shell (`frontend/app/`, assets under /app/), signed-in users only
    /api/...                    the JSON API behind it, including the editing chat (`api.py`)

Who may see an artifact is decided by `access.py` from its placement and permissions: a public artifact by
anyone, an organisation's by its members, a private one by its owner. The browser session (`login.py`) says who
the viewer is; `load_artifact` applies the rules to the page, its media and sources (`main.md`, `artifact.toml`,
`styles.css` and `components/*`, served as text next to the page) and the exports alike. Only `dist/` is never
served. A visitor without access is sent to sign in; a signed-in user without access gets a 403 page.

Run by `main.py`. Configuration is by environment: `DATABASE_URL`, `OPENARTIFACT_STORE_URL`,
`OPENARTIFACT_CACHE_DIR` (one per process, never shared), `OPENARTIFACT_BASE_URL`, and for auth `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` / `OPENARTIFACT_SECRET_KEY` or `OPENARTIFACT_DEV_TOKEN`. The image needs `git`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import mimetypes
import re
import tempfile
import tomllib
import uuid
import zipfile
from collections.abc import AsyncGenerator, Coroutine
from pathlib import Path
from typing import Any
from urllib.parse import quote

import powerpoint
import render
import word
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import access
import api
import build
import config
import db
import login
import mcp_server
import pages
import signing
import store
import upload
import workspace

# openartifact.js is not packaged; it is read from the frontend build output in this checkout.
RUNTIME_JS_PATH = config.ROOT / 'frontend' / 'dist' / 'openartifact.js'
# The web app (`frontend/app/`, built by Vite): its shell is served at `/` and `/edit/{id}`, its assets under `/app/`.
APP_DIR = config.ROOT / 'frontend' / 'dist' / 'app'
APP_INDEX = APP_DIR / 'index.html'
# The brand mark, the tab icon of every page on the platform: the app shell and the server's own pages link it, and
# `build.py` writes it into every artifact page that names no favicon of its own. Served at the root so that a
# browser looking for `/favicon.ico` on its own (a markdown export, a JSON answer, a 404) finds it too.
FAVICON_PATH = APP_DIR / 'favicon.svg'
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
# The print pass the chrome service uses to fetch a page that may be private: a token over the artifact id that
# lives for five minutes, minted by the `.pdf` route for a viewer who may see the artifact.


# The MCP endpoint is `/mcp/`; the app is mounted at the root (last, below) so the OAuth routes FastMCP registers
# beside it (`/authorize`, `/token`, `/consent`, `/auth/callback`, `/.well-known/*`) sit where the metadata built
# from `base_url()` says they are. Mounted under `/mcp` they would be advertised at paths nothing serves.
mcp_app = mcp_server.mcp.http_app(path='/mcp/')


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


class LoginRequired(Exception):
    """A visitor asked for something only a signed-in user may see."""


class Forbidden(Exception):
    """A signed-in user asked for something not shared with them."""


def wants_html(request: Request) -> bool:
    """A browser navigating (as opposed to a script or the toolbar's fetch): send pages and redirects, not JSON."""
    return request.method == 'GET' and 'text/html' in request.headers.get('accept', '')


ARTIFACT_PATH_RE = re.compile(r'^/artifacts/([0-9a-f-]{36})(?:[/.]|$)')


def page_path(path: str) -> str:
    """Where to come back to after signing in: the artifact's page for any of its URLs, else the path itself."""
    match = ARTIFACT_PATH_RE.match(path)
    return f'/artifacts/{match.group(1)}/' if match else path


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired) -> Response:
    target = login.login_url(page_path(request.url.path))
    if wants_html(request):
        return RedirectResponse(target, status_code=303)
    return JSONResponse({'detail': 'sign in required', 'login_url': target}, status_code=401)


@app.exception_handler(Forbidden)
async def forbidden(request: Request, exc: Forbidden) -> Response:
    if wants_html(request):
        viewer = await login.current_viewer(request)
        body = pages.forbidden_html(viewer.email if viewer else None, page_path(request.url.path))
        return pages.page_response('Private artifact', body, status=403)
    return JSONResponse({'detail': 'this artifact is not shared with you'}, status_code=403)


def parse_artifact_id(artifact_id: str) -> uuid.UUID:
    """The UUID in a URL segment, or 404; a value that is not a UUID is a 404 too, not a validation error."""
    try:
        return uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(404, f'invalid artifact id {artifact_id!r}') from None


async def find_artifact(artifact_id: str) -> workspace.Artifact:
    """The row for a URL segment, or 404; no access check (the print pass has its own)."""
    found = await workspace.get_artifact(parse_artifact_id(artifact_id))
    if found is None:
        raise HTTPException(404, f'artifact {artifact_id} not found')
    return found


async def load_artifact(artifact_id: str, request: Request, *, edit: bool = False) -> workspace.Artifact:
    """The artifact for a URL segment, if the viewer may see it (and change it, with `edit`).

    Missing is a 404. Not allowed is `LoginRequired` for a visitor and `Forbidden` for a signed-in user, which
    the handlers above turn into a redirect to sign-in or the 403 page for a browser, and JSON otherwise.
    """
    found = await find_artifact(artifact_id)
    viewer = await login.current_viewer(request)
    allowed = access.can_edit(found, viewer) if edit else access.can_view(found, viewer)
    if not allowed:
        raise LoginRequired() if viewer is None else Forbidden()
    return found


def private(response: Response) -> Response:
    """Mark a response that depends on who is asking, so shared caches keep out of it."""
    response.headers['Cache-Control'] = 'private'
    vary = response.headers.get('Vary')
    response.headers['Vary'] = f'{vary}, Cookie' if vary else 'Cookie'
    return response


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


async def app_shell(request: Request) -> Response:
    """The web app's HTML shell, for a signed-in user; a visitor is sent to sign in first."""
    if await login.current_viewer(request) is None:
        raise LoginRequired()
    if not APP_INDEX.is_file():
        raise HTTPException(404, f'{APP_INDEX} is missing: run `pnpm -C frontend build`')
    return FileResponse(APP_INDEX, media_type='text/html', headers={'Cache-Control': 'no-store'})


@app.get('/')
async def index(request: Request) -> Response:
    """The web app: the artifact list. The JSON index moved to `/api/`."""
    return await app_shell(request)


@app.get('/edit/{artifact_id}')
async def edit_page(artifact_id: str, request: Request) -> Response:
    """The web app: the editor for one artifact (the app itself checks access and loads it)."""
    return await app_shell(request)


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


@app.get('/favicon.svg')
@app.get('/favicon.ico')
def favicon() -> FileResponse:
    """The platform's mark, for every tab on it; the `.ico` route answers browsers' own lookup with the same SVG."""
    if not FAVICON_PATH.is_file():
        raise HTTPException(404, f'{FAVICON_PATH} is missing: run `pnpm -C frontend build`')
    return FileResponse(FAVICON_PATH, media_type='image/svg+xml')


def source_files(directory: Path) -> list[Path]:
    """Every file under an artifact directory except build output (`dist/`) and `.git*` entries, relative, sorted."""
    files: list[Path] = []
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory)
        if not path.is_file() or relative.parts[0] == 'dist' or any(p.startswith('.git') for p in relative.parts):
            continue
        files.append(relative)
    return files


def frontmatter(fields: dict[str, str | list[str]]) -> str:
    """A YAML frontmatter block: scalars written as JSON strings, which YAML reads as-is, lists as block sequences."""
    lines = ['---']
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f'{key}:')
            lines.extend(f'  - {json.dumps(item, ensure_ascii=False)}' for item in value)
        else:
            lines.append(f'{key}: {json.dumps(value, ensure_ascii=False)}')
    lines.append('---')
    return '\n'.join(lines) + '\n'


def artifact_summary(found: workspace.Artifact, directory: Path) -> tuple[dict[str, str | list[str]], str]:
    """The `.md` frontmatter fields and the markdown source.

    `artifact.toml` is the source of truth where it parses; the row supplies the title and type otherwise, so a
    broken config still gets a summary rather than an error.
    """
    raw: dict[str, object] = {}
    toml_path = directory / 'artifact.toml'
    if toml_path.is_file():
        try:
            raw = tomllib.loads(toml_path.read_text(encoding='utf-8'))
        except tomllib.TOMLDecodeError:
            pass

    def text(key: str, default: str) -> str:
        value = raw.get(key)
        return value if isinstance(value, str) else default

    markdown_path = directory / text('markdown', 'main.md')
    markdown = markdown_path.read_text(encoding='utf-8') if markdown_path.is_file() else ''
    fields: dict[str, str | list[str]] = {
        'id': str(found.id),
        'title': text('title', found.title),
        'type': text('type', found.type),
        'theme': text('theme', 'light'),
        'url': mcp_server.artifact_url(found.id),
        'created_at': found.created_at.isoformat(),
        'updated_at': found.updated_at.isoformat(),
        'files': [path.as_posix() for path in source_files(directory)],
    }
    return fields, markdown


async def markdown_response(found: workspace.Artifact) -> Response:
    """The markdown export: the frontmatter summary followed by the markdown source."""
    async with workspace.open_artifact(found) as directory:
        fields, markdown = await asyncio.to_thread(artifact_summary, found, directory)
    return Response(frontmatter(fields) + markdown, media_type='text/markdown; charset=utf-8')


@app.get('/artifacts/{artifact_id}.md')
async def artifact_markdown(artifact_id: str, request: Request) -> Response:
    """The markdown source behind a frontmatter summary: what an agent should read instead of parsing the page."""
    return private(await markdown_response(await load_artifact(artifact_id, request)))


@app.get('/artifacts/{artifact_id}.json')
async def artifact_json(artifact_id: str, request: Request) -> Response:
    """The artifact's placement and permissions, and what the viewer may do; what the toolbar renders from."""
    found = await load_artifact(artifact_id, request)
    viewer = await login.current_viewer(request)
    organization = None
    if found.organization_id is not None:
        org = await workspace.get_organization(found.organization_id)
        organization = {'domain': org.domain, 'name': org.name} if org else None
    body: dict[str, object] = {
        'id': str(found.id),
        'title': found.title,
        'type': found.type,
        'visibility': found.visibility,
        'org_editable': found.org_editable,
        'organization': organization,
        'forked_from': str(found.forked_from) if found.forked_from else None,
        'viewer': {'name': viewer.name, 'email': viewer.email, 'picture': viewer.picture} if viewer else None,
        'can_edit': access.can_edit(found, viewer),
        'can_fork': access.can_fork(found, viewer),
        'login_url': login.login_url(f'/artifacts/{found.id}/'),
    }
    return private(JSONResponse(body))


@app.post('/artifacts/{artifact_id}/fork')
async def artifact_fork(artifact_id: str, request: Request) -> Response:
    """Copy the artifact, history included, into the signed-in viewer's personal space as a private artifact."""
    if not login.same_origin(request):
        raise HTTPException(403, 'cross-site request')
    found = await load_artifact(artifact_id, request)
    viewer = await login.current_viewer(request)
    if viewer is None:
        raise LoginRequired()
    fork = await workspace.fork_artifact(
        found, viewer.workspace_id, organization_id=None, visibility='private', org_editable=False
    )
    return RedirectResponse(f'/artifacts/{fork.id}/', status_code=303)


def prefers_text(accept: str | None) -> bool:
    """Whether an `Accept` header rates `text/markdown` or `text/plain` above `text/html`.

    A browser's `text/html,...,*/*;q=0.8`, a bare `*/*` or `text/*`, or no header at all, all mean HTML: the page
    wins ties. An agent or a terminal asking for `text/markdown` or `text/plain` gets the markdown export.
    """
    if not accept:
        return False
    html = text = 0.0
    for part in accept.split(','):
        media, _, params = part.strip().partition(';')
        quality = 1.0
        for param in params.split(';'):
            key, _, value = param.strip().partition('=')
            if key == 'q':
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        media = media.strip().lower()
        if media in ('text/html', 'text/*', '*/*'):
            html = max(html, quality)
        if media in ('text/markdown', 'text/plain', 'text/*', '*/*'):
            text = max(text, quality)
    return text > html


def zip_files(directory: Path, files: list[Path], prefix: str) -> bytes:
    """A zip of `files` (relative to `directory`) under `prefix/`, built in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(directory / relative, f'{prefix}/{relative.as_posix()}')
    return buffer.getvalue()


def all_files(directory: Path) -> list[Path]:
    """Every file under `directory`, relative, sorted; for an exported repository, `.git` included."""
    return [p.relative_to(directory) for p in sorted(directory.rglob('*')) if p.is_file()]


def config_title(directory: Path, fallback: str) -> str:
    """`title` from `artifact.toml` where it parses, else `fallback` (the row's title)."""
    toml_path = directory / 'artifact.toml'
    if toml_path.is_file():
        try:
            title = tomllib.loads(toml_path.read_text(encoding='utf-8')).get('title')
        except tomllib.TOMLDecodeError:
            return fallback
        if isinstance(title, str) and title.strip():
            return title
    return fallback


async def export_filename(found: workspace.Artifact, directory: Path, extension: str) -> str:
    """`{title} {commit}.{extension}` for a download: the title and the short sha of the artifact's last change.

    Call it under `open_artifact`, so the checkout is at the head and the commit is the one the content came from.
    """
    title = config_title(directory, found.title)
    sha = (await workspace.artifact_git(found.id, 'rev-parse', 'HEAD')).strip()
    stem = f'{title} {sha[:7]}'
    # Characters some file systems refuse, plus controls and the quote that would end the header value.
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', stem)
    stem = ' '.join(stem.split()) or str(found.id)
    return f'{stem}.{extension}'


def attachment(filename: str) -> dict[str, str]:
    """Headers that make a browser save the response as `filename`: RFC 6266, an ASCII fallback plus the UTF-8 name."""
    fallback = filename.encode('ascii', 'ignore').decode() or 'download'
    return {'Content-Disposition': f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename)}'}


@app.get('/artifacts/{artifact_id}.zip')
async def artifact_zip(artifact_id: str, request: Request) -> Response:
    """The artifact as a git repository in a zip, inside a folder named by the artifact id.

    The folder is a clone to work in: the source files (not `dist/`) checked out at the head, and `.git` holding
    the artifact's history, cloned from the checkout by `workspace.clone_repository`.
    """
    found = await load_artifact(artifact_id, request)
    with tempfile.TemporaryDirectory(prefix='openartifact-zip-') as tmp:
        exported = Path(tmp) / str(found.id)
        async with workspace.open_artifact(found) as directory:
            await workspace.clone_repository(found.id, exported)
            filename = await export_filename(found, directory, 'zip')
        data = await asyncio.to_thread(zip_files, exported, all_files(exported), str(found.id))
    return private(Response(data, media_type='application/zip', headers=attachment(filename)))


def verify_print_token(token: str, artifact_id: uuid.UUID) -> None:
    """403 unless `token` is a live pass for this artifact."""
    try:
        render.verify_print_token(token, artifact_id)
    except signing.SignatureError as exc:
        raise HTTPException(403, f'invalid print pass: {exc}') from exc


@app.get('/artifacts/{artifact_id}.pdf')
async def artifact_pdf(artifact_id: str, request: Request) -> Response:
    """The page printed to PDF by the chrome service, which fetches it from this server by print pass.

    `render.pdf` builds the page first, so a broken artifact is a 422 here rather than a PDF of an error page.
    The pass is minted for a viewer who may see the artifact, and lands in the error text of a failed print,
    which that same viewer reads.
    """
    found = await load_artifact(artifact_id, request)
    async with workspace.open_artifact(found) as directory:
        await build_if_missing(found, directory)
        filename = await export_filename(found, directory, 'pdf')
    data = await rendered(render.pdf(found))
    return private(Response(data, media_type='application/pdf', headers=attachment(filename)))


@app.get('/artifacts/{artifact_id}.pptx')
async def artifact_pptx(artifact_id: str, request: Request) -> Response:
    """A deck as a PowerPoint file with editable text: the chrome service measures the page and pictures each slide,
    and `powerpoint.export` assembles them. Decks only; a document or page artifact is a 422."""
    found = await load_artifact(artifact_id, request)
    if found.type != 'deck':
        raise HTTPException(422, f'only a deck exports to PowerPoint; this artifact is a {found.type}')
    async with workspace.open_artifact(found) as directory:
        await build_if_missing(found, directory)
        filename = await export_filename(found, directory, 'pptx')
    data = await rendered(powerpoint.export(found))
    return private(Response(data, media_type=powerpoint.MEDIA_TYPE, headers=attachment(filename)))


@app.get('/artifacts/{artifact_id}.docx')
async def artifact_docx(artifact_id: str, request: Request) -> Response:
    """A document or page as a Word file: the chrome service reads the outline of the page and `word.export` writes
    it as Word paragraphs. A deck, whose text is laid out, exports to PowerPoint instead and is a 422 here."""
    found = await load_artifact(artifact_id, request)
    if found.type == 'deck':
        raise HTTPException(422, 'only a document or page exports to Word; this artifact is a deck')
    async with workspace.open_artifact(found) as directory:
        await build_if_missing(found, directory)
        filename = await export_filename(found, directory, 'docx')
    data = await rendered(word.export(found))
    return private(Response(data, media_type=word.MEDIA_TYPE, headers=attachment(filename)))


@app.get('/artifacts/{artifact_id}.png')
async def artifact_png(artifact_id: str, request: Request, page: int = 1) -> Response:
    """One page of the artifact as a PNG, as a viewer sees it: `page` is 1-based, the deck's slide or the
    document's sheet scrolled into view. Rendered by the chrome service like the PDF; shown inline, not downloaded."""
    if page < 1:
        raise HTTPException(422, 'page is 1-based')
    found = await load_artifact(artifact_id, request)
    async with workspace.open_artifact(found) as directory:
        await build_if_missing(found, directory)
    data = await rendered(render.screenshot(found, page))
    return private(Response(data, media_type='image/png'))


async def rendered(job: Coroutine[Any, Any, bytes]) -> bytes:
    """Await a `render` job, turning its errors into the HTTP status they name."""
    try:
        return await job
    except build.BuildError as exc:
        raise HTTPException(422, f'build failed: {exc}') from exc
    except render.RenderError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.get('/artifacts/{artifact_id}')
def artifact_redirect(artifact_id: str) -> RedirectResponse:
    """Send `/artifacts/x` to `/artifacts/x/` so the page's relative image references resolve under it."""
    return RedirectResponse(f'/artifacts/{artifact_id}/')


async def build_if_missing(found: workspace.Artifact, directory: Path) -> Path:
    """`dist/index.html`, built now if this process has not built the current version yet; a failure is a 422."""
    try:
        return await render.ensure_built(found, directory)
    except build.BuildError as exc:
        raise HTTPException(422, f'build failed: {exc}') from exc


async def built_page(found: workspace.Artifact) -> str:
    """The artifact's page HTML, built if needed."""
    async with workspace.open_artifact(found) as directory:
        page = await build_if_missing(found, directory)
        # Read under the lock: a sync for a newer head could delete dist/ between here and the response otherwise.
        return page.read_text(encoding='utf-8')


async def page_response(found: workspace.Artifact, request: Request) -> Response:
    """The artifact's page; or its markdown export when the client asks for text rather than HTML."""
    if prefers_text(request.headers.get('accept')):
        response = await markdown_response(found)
    else:
        response = HTMLResponse(await built_page(found))
    # The same URL answers two ways, so caches must key on the header that decides.
    response.headers['Vary'] = 'Accept'
    return response


async def media_response(found: workspace.Artifact, path: str) -> Response:
    """An image or font the page references relatively, or one of the source files, from the artifact directory."""
    async with workspace.open_artifact(found) as directory:
        file = contained_file(directory, path, SERVED_EXTS)
        data = file.read_bytes()
    media_type = SOURCE_MEDIA_TYPES.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0]
    return Response(data, media_type=media_type or 'application/octet-stream')


@app.get('/artifacts/{artifact_id}/')
async def artifact_index(artifact_id: str, request: Request) -> Response:
    """The artifact's page, for a viewer who may see it."""
    return private(await page_response(await load_artifact(artifact_id, request), request))


@app.get('/artifacts/{artifact_id}/{path:path}')
async def artifact_media(artifact_id: str, path: str, request: Request) -> Response:
    """A file from the artifact directory, for a viewer who may see it."""
    return private(await media_response(await load_artifact(artifact_id, request), path))


@app.get('/print/{token}/artifacts/{artifact_id}/')
async def print_index(token: str, artifact_id: str, request: Request) -> Response:
    """The page for the chrome service, by print pass rather than session; relative media resolve below it."""
    found = await find_artifact(artifact_id)
    verify_print_token(token, found.id)
    return await page_response(found, request)


@app.get('/print/{token}/artifacts/{artifact_id}/{path:path}')
async def print_media(token: str, artifact_id: str, path: str) -> Response:
    """A file from the artifact directory for the chrome service, by print pass."""
    found = await find_artifact(artifact_id)
    verify_print_token(token, found.id)
    return await media_response(found, path)


async def read_body(request: Request, size: int) -> bytes:
    """The request body, which must be exactly `size` bytes: a longer one is a 413, a shorter one a 400."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > size:
            raise HTTPException(413, f'the body is longer than the declared {size} bytes')
        chunks.append(chunk)
    if total != size:
        raise HTTPException(400, f'the body is {total} bytes but Content-Length says {size}')
    return b''.join(chunks)


@app.put('/artifacts/{artifact_id}/{path:path}')
async def artifact_upload(artifact_id: str, path: str, request: Request, token: str = '') -> dict[str, object]:
    """Write a file into the artifact directory and commit it, for a URL minted by the `upload_url` tool.

    The token signs the artifact, the path, the size and an expiry (`upload.py`), so nothing about the mint is
    stored: `Content-Length` must be the signed size and the body exactly that long. The body is read before the
    workspace is locked, so a slow upload does not hold up other edits. The answer carries the file's SHA-256 for
    the uploader to compare with the local file.
    """
    found = await find_artifact(artifact_id)
    length = request.headers.get('content-length')
    if length is None:
        raise HTTPException(411, 'Content-Length is required: send the file as a plain body, not chunked')
    try:
        size = int(length)
    except ValueError:
        raise HTTPException(400, f'invalid Content-Length {length!r}') from None
    try:
        upload.validate_path(path)
        upload.verify_token(token, found.id, path, size)
    except upload.UploadError as exc:
        raise HTTPException(403, str(exc)) from exc
    body = await read_body(request, size)
    try:
        async with workspace.edit(found.id, f'upload: {path}') as tx:
            file = tx.path / path
            # The path was validated, but a symlink the agent wrote earlier could still point outside the artifact.
            if tx.path.resolve() not in file.resolve().parents:
                raise HTTPException(403, f'{path!r} resolves outside the artifact')
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(body)
            except (IsADirectoryError, NotADirectoryError, FileExistsError) as exc:
                raise HTTPException(409, f'{path!r} cannot be written: a directory is in the way') from exc
    except workspace.ArtifactBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'path': path, 'size': size, 'sha256': hashlib.sha256(body).hexdigest()}


app.include_router(login.router)
app.include_router(api.router)
app.mount('/app', StaticFiles(directory=APP_DIR, check_dir=False), name='app')
# Last: the MCP app answers everything no route above matched (the endpoint, the OAuth routes, and 404s).
app.mount('/', mcp_app)
