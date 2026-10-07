"""HTTP server: the MCP server plus serving of the browser runtime and built artifacts.

Routes:

    /mcp/                       the MCP endpoint (streamable HTTP) from `mcp_server.py`, behind Google login
    /openartifact.js            the browser runtime, `frontend/dist/openartifact.js`, which every built page links
    /artifacts/{id}/            an artifact's page, built on demand from its checkout; the markdown
                                export instead when the Accept header prefers text/markdown or text/plain
    /artifacts/{id}/{path}      an image or font from the artifact directory, referenced relatively by the page
    PUT /artifacts/{id}/{path}  an upload to the artifact directory, with a token from the `upload_url` tool
    /artifacts/{id}.md          the markdown source behind a frontmatter summary of the artifact
    /artifacts/{id}.zip         the artifact as a git repository (its files and history) in a zip
    /artifacts/{id}.pdf         the page printed to PDF by the chrome service (`chrome/`)
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
import hashlib
import io
import json
import mimetypes
import re
import tempfile
import tomllib
import uuid
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import quote

import httpx2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import build
import config
import db
import mcp_server
import store
import upload
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
async def artifact_markdown(artifact_id: str) -> Response:
    """The markdown source behind a frontmatter summary: what an agent should read instead of parsing the page."""
    return await markdown_response(await load_artifact(artifact_id))


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
async def artifact_zip(artifact_id: str) -> Response:
    """The artifact as a git repository in a zip, inside a folder named by the artifact id.

    The folder is a clone to work in: the source files (not `dist/`) checked out at the head, and `.git` holding
    the artifact's history, cloned from the checkout by `workspace.clone_repository`.
    """
    found = await load_artifact(artifact_id)
    with tempfile.TemporaryDirectory(prefix='openartifact-zip-') as tmp:
        exported = Path(tmp) / str(found.id)
        async with workspace.open_artifact(found) as directory:
            await workspace.clone_repository(found.id, exported)
            filename = await export_filename(found, directory, 'zip')
        data = await asyncio.to_thread(zip_files, exported, all_files(exported), str(found.id))
    return Response(data, media_type='application/zip', headers=attachment(filename))


def chrome_client() -> httpx2.AsyncClient:
    """The HTTP client for the chrome service; tests swap it for one wired to a stub app."""
    return httpx2.AsyncClient(timeout=60)


@app.get('/artifacts/{artifact_id}.pdf')
async def artifact_pdf(artifact_id: str) -> Response:
    """The page printed to PDF by the chrome service, which fetches it from this server.

    The page is built first, so a broken artifact is a 422 here rather than a PDF of an error page; and the
    artifact lock is released before the chrome service is called, because it fetches `/artifacts/{id}/` from
    this process, which would wait on the same lock.
    """
    found = await load_artifact(artifact_id)
    chrome_url = config.chrome_url()
    if chrome_url is None:
        raise HTTPException(503, 'PDF export is not configured: OPENARTIFACT_CHROME_URL names the chrome service')
    async with workspace.open_artifact(found) as directory:
        await build_if_missing(found, directory)
        filename = await export_filename(found, directory, 'pdf')
    page_url = f'{config.internal_url()}/artifacts/{found.id}/'
    try:
        async with chrome_client() as client:
            response = await client.post(f'{chrome_url}/pdf/', json={'url': page_url})
    except httpx2.HTTPError as exc:
        raise HTTPException(502, f'chrome service unreachable: {exc}') from exc
    if response.status_code != 200:
        raise HTTPException(502, f'chrome service failed ({response.status_code}): {response.text}')
    return Response(response.content, media_type='application/pdf', headers=attachment(filename))


@app.get('/artifacts/{artifact_id}')
def artifact_redirect(artifact_id: str) -> RedirectResponse:
    """Send `/artifacts/x` to `/artifacts/x/` so the page's relative image references resolve under it."""
    return RedirectResponse(f'/artifacts/{artifact_id}/')


async def build_if_missing(found: workspace.Artifact, directory: Path) -> Path:
    """`dist/index.html`, built now if this process has not built the current version yet; a failure is a 422.

    The page is served at `/artifacts/<id>/`, so the `.md` export it advertises is `../<id>.md`.
    """
    page = directory / 'dist' / 'index.html'
    if not page.is_file():
        try:
            await asyncio.to_thread(build.build_html, directory, markdown_url=f'../{found.id}.md')
        except build.BuildError as exc:
            raise HTTPException(422, f'build failed: {exc}') from exc
    return page


async def built_page(found: workspace.Artifact) -> str:
    """The artifact's page HTML, built if needed."""
    async with workspace.open_artifact(found) as directory:
        page = await build_if_missing(found, directory)
        # Read under the lock: a sync for a newer head could delete dist/ between here and the response otherwise.
        return page.read_text(encoding='utf-8')


@app.get('/artifacts/{artifact_id}/')
async def artifact_index(artifact_id: str, request: Request) -> Response:
    """The artifact's page; or its markdown export when the client asks for text rather than HTML."""
    found = await load_artifact(artifact_id)
    if prefers_text(request.headers.get('accept')):
        response = await markdown_response(found)
    else:
        response = HTMLResponse(await built_page(found))
    # The same URL answers two ways, so caches must key on the header that decides.
    response.headers['Vary'] = 'Accept'
    return response


@app.get('/artifacts/{artifact_id}/{path:path}')
async def artifact_media(artifact_id: str, path: str) -> Response:
    """An image or font the page references relatively, or one of the source files, from the artifact directory."""
    found = await load_artifact(artifact_id)
    async with workspace.open_artifact(found) as directory:
        file = contained_file(directory, path, SERVED_EXTS)
        data = file.read_bytes()
    media_type = SOURCE_MEDIA_TYPES.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0]
    return Response(data, media_type=media_type or 'application/octet-stream')


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
    found = await load_artifact(artifact_id)
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


# Last: the MCP app answers everything no route above matched (the endpoint, the OAuth routes, and 404s).
app.mount('/', mcp_app)
