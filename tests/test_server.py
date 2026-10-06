"""Tests for `backend/server.py`: the HTTP routes with the whole app running, and MCP over real HTTP."""

from __future__ import annotations

import hashlib
import io
import shutil
import threading
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx2
import logfire
import pytest
import uvicorn
from conftest import DEV_TOKEN
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from fastmcp import Client
from logfire.testing import CaptureLogfire

import auth
import db
import server
import upload
import workspace

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / 'examples' / 'starter'
T = TypeVar('T')


@pytest.fixture
def client(server_env: None) -> Iterator[TestClient]:
    with TestClient(server.app) as client:
        yield client


async def seed_starter() -> workspace.Artifact:
    """Import the starter deck as a new user's artifact; runs in the app's loop."""
    async with db.pool().acquire() as conn, conn.transaction():
        principal = await auth.upsert_user(conn, sub='seed', email='seed@example.com', name=None, picture=None)
    return await workspace.import_directory(principal.workspace_id, 'Starter', 'deck', STARTER)


def in_app(client: TestClient, fn: Callable[[], Awaitable[T]]) -> T:
    """Run a coroutine in the app's loop, so it uses the app's pool, store and checkout cache."""
    assert client.portal is not None
    return client.portal.call(fn)


def starter(client: TestClient) -> workspace.Artifact:
    """Seed the starter deck through the running app."""
    return in_app(client, seed_starter)


def test_index(client: TestClient):
    assert client.get('/').json() == {'mcp': '/mcp/', 'runtime': '/openartifact.js'}


def test_health(client: TestClient):
    assert client.get('/health/').json() == {'status': 'ok'}


def test_runtime_js(client: TestClient):
    response = client.get('/openartifact.js')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/javascript')
    assert response.content == server.RUNTIME_JS_PATH.read_bytes()


def test_unknown_artifacts_are_404(client: TestClient):
    assert client.get(f'/artifacts/{uuid.uuid4()}/').status_code == 404
    assert client.get(f'/artifacts/{uuid.uuid4()}/assets/logo.svg').status_code == 404
    assert client.get('/artifacts/Bad%20Name/').status_code == 404
    assert client.get('/artifacts/not-a-uuid/assets/logo.svg').status_code == 404


def test_artifact_page_is_built_on_demand(client: TestClient):
    artifact = starter(client)
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    assert not (directory / 'dist').exists()
    page = client.get(f'/artifacts/{artifact.id}/')
    assert page.status_code == 200
    assert page.headers['content-type'].startswith('text/html')
    assert '<script src="/openartifact.js"></script>' in page.text
    assert 'data:' not in page.text
    assert (directory / 'dist' / 'index.html').is_file()
    # A later request serves the built file; deleting it triggers another build.
    shutil.rmtree(directory / 'dist')
    assert client.get(f'/artifacts/{artifact.id}/').status_code == 200


def test_artifact_build_failure_is_422(client: TestClient):
    artifact = starter(client)
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)

    async def break_it() -> None:
        async with workspace.edit(artifact.workspace_id, 'break') as tx:
            (tx.artifact_dir(artifact.id) / 'main.md').write_text('# hi\n\n<component src="Nope.html"></component>\n')

    in_app(client, break_it)
    response = client.get(f'/artifacts/{artifact.id}/')
    assert response.status_code == 422
    assert 'component not found' in response.json()['detail']
    assert not (directory / 'dist').exists()


def test_artifact_images_are_served(client: TestClient):
    artifact = starter(client)
    logo = client.get(f'/artifacts/{artifact.id}/assets/logo.svg')
    assert logo.status_code == 200
    assert logo.headers['content-type'].startswith('image/svg+xml')
    assert logo.content == (STARTER / 'assets' / 'logo.svg').read_bytes()
    # Fonts declared in styles.css are served too (any file in the directory with an allowed extension).
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    (directory / 'assets' / 'body.woff2').write_bytes(b'wOF2')
    assert client.get(f'/artifacts/{artifact.id}/assets/body.woff2').status_code == 200


def test_artifact_sources_are_served_as_text(client: TestClient):
    artifact = starter(client)
    for path, media_type in (
        ('main.md', 'text/markdown; charset=utf-8'),
        ('artifact.toml', 'text/plain; charset=utf-8'),
        ('styles.css', 'text/plain; charset=utf-8'),
        ('components/Hero.html', 'text/plain; charset=utf-8'),
    ):
        response = client.get(f'/artifacts/{artifact.id}/{path}')
        assert response.status_code == 200, path
        assert response.headers['content-type'] == media_type, path
        assert response.content == (STARTER / path).read_bytes(), path
    # The page advertises the markdown export, with a note telling agents to read it rather than the HTML.
    page = client.get(f'/artifacts/{artifact.id}/')
    assert (
        f'<link rel="alternate" type="text/markdown" href="../{artifact.id}.md" title="Markdown source">' in page.text
    )
    assert 'do not parse this HTML' in page.text and f'\n      ../{artifact.id}.md\n' in page.text
    assert 'id="artifact-markdown"' in page.text
    # The build output is not a source file: the page is served at the directory URL only.
    for path in ('dist/index.html', 'assets/../dist/index.html'):
        assert client.get(f'/artifacts/{artifact.id}/{path}').status_code == 404, path


def test_artifact_media_cannot_reach_other_artifacts(client: TestClient):
    artifact = starter(client)
    other = starter(client)
    # `..` that leaves the artifact directory is refused, even towards another artifact's image. The segments
    # are percent-encoded because the HTTP client collapses a literal `..` before sending.
    assert client.get(f'/artifacts/{artifact.id}/%2e%2e/{other.id}/assets/logo.svg').status_code == 404
    assert client.get(f'/artifacts/{artifact.id}/assets/%2e%2e/%2e%2e/{other.id}/assets/logo.svg').status_code == 404
    # ...while `..` that stays inside it is just a path to the same file.
    assert client.get(f'/artifacts/{artifact.id}/assets/../assets/logo.svg').status_code == 200


def test_artifact_media_cannot_escape_via_symlink(client: TestClient, tmp_path: Path):
    artifact = starter(client)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'png')
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    (directory / 'assets' / 'link.png').symlink_to(outside)
    assert client.get(f'/artifacts/{artifact.id}/assets/link.png').status_code == 404


def test_artifact_redirects_to_trailing_slash(client: TestClient):
    response = client.get(f'/artifacts/{uuid.uuid4()}', follow_redirects=False)
    assert response.status_code == 307


def starter_sources() -> list[str]:
    """The starter deck's source files, relative, as the server lists them."""
    return sorted(
        p.relative_to(STARTER).as_posix() for p in STARTER.rglob('*') if p.is_file() and 'dist' not in p.parts
    )


def test_artifact_markdown_has_a_frontmatter_summary(client: TestClient):
    artifact = starter(client)
    response = client.get(f'/artifacts/{artifact.id}.md')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'text/markdown; charset=utf-8'
    empty, front, body = response.text.split('---\n', 2)
    assert empty == ''
    lines = front.splitlines()
    # Title and theme come from artifact.toml, which outranks the row's title ("Starter").
    assert lines[:4] == [
        f'id: "{artifact.id}"',
        'title: "OpenArtifact Starter"',
        'type: "deck"',
        'theme: "markdown-dark"',
    ]
    assert f'url: "http://127.0.0.1:8765/artifacts/{artifact.id}/"' in lines
    assert any(line.startswith('created_at: "') for line in lines)
    files = [line.removeprefix('  - ').strip('"') for line in lines[lines.index('files:') + 1 :]]
    assert files == starter_sources()
    assert body == (STARTER / 'main.md').read_text(encoding='utf-8')
    # The summary does not need the artifact to build: a broken artifact.toml falls back to the row.
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    (directory / 'artifact.toml').write_text('this is not toml')
    response = client.get(f'/artifacts/{artifact.id}.md')
    assert response.status_code == 200
    assert response.text.splitlines()[1:4] == [f'id: "{artifact.id}"', 'title: "Starter"', 'type: "deck"']


def last_commit(client: TestClient, artifact: workspace.Artifact) -> str:
    """Short sha of the last commit that touched the artifact, as the download names use it."""
    root = workspace.checkout_path(artifact.workspace_id)
    log = in_app(client, lambda: workspace.git('log', '-1', '--format=%H', '--', f'artifacts/{artifact.id}', cwd=root))
    return log.strip()[:7]


def test_page_url_serves_markdown_to_clients_that_prefer_text(client: TestClient):
    artifact = starter(client)
    url = f'/artifacts/{artifact.id}/'
    markdown = client.get(f'{url[:-1]}.md').text
    for accept in ('text/markdown', 'text/plain', 'text/plain;q=0.9, text/html;q=0.8', 'text/markdown, */*;q=0.1'):
        response = client.get(url, headers={'accept': accept})
        assert response.headers['content-type'] == 'text/markdown; charset=utf-8', accept
        assert response.headers['vary'] == 'Accept'
        assert response.text == markdown
    # Browsers, `*/*`, `text/*` and no header at all get the page: HTML wins ties.
    browser = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    for headers in ({'accept': browser}, {'accept': '*/*'}, {'accept': 'text/*'}, {}):
        response = client.get(url, headers=headers)
        assert response.headers['content-type'].startswith('text/html'), headers
        assert response.headers['vary'] == 'Accept'
        assert 'id="artifact-markdown"' in response.text


def test_prefers_text():
    assert server.prefers_text('text/markdown')
    assert server.prefers_text('text/plain, text/html;q=0.5')
    assert not server.prefers_text(None)
    assert not server.prefers_text('')
    assert not server.prefers_text('text/html, text/plain')
    assert not server.prefers_text('text/plain;q=0, text/html;q=0')
    assert not server.prefers_text('text/plain;q=nonsense')
    assert server.prefers_text('TEXT/PLAIN ; q=0.5')


def test_artifact_zip_packs_the_sources(client: TestClient):
    artifact = starter(client)
    # Build first, so there is a dist/ to leave out.
    assert client.get(f'/artifacts/{artifact.id}/').status_code == 200
    response = client.get(f'/artifacts/{artifact.id}.zip')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    # Named after the artifact.toml title and the commit it came from.
    sha = last_commit(client, artifact)
    assert response.headers['content-disposition'] == (
        f'attachment; filename="OpenArtifact Starter {sha}.zip"; filename*=UTF-8\'\'OpenArtifact%20Starter%20{sha}.zip'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [f'{artifact.id}/{path}' for path in starter_sources()]
        assert archive.read(f'{artifact.id}/main.md') == (STARTER / 'main.md').read_bytes()


def test_download_names_survive_awkward_titles(client: TestClient):
    artifact = starter(client)
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    (directory / 'artifact.toml').write_text('title = "Zoë \\"Q\\" / 2026 <v1>"\n')
    sha = last_commit(client, artifact)
    disposition = client.get(f'/artifacts/{artifact.id}.zip').headers['content-disposition']
    # Quotes, slashes and brackets become spaces; the ASCII fallback drops the diaeresis, the UTF-8 name keeps it.
    assert disposition == (
        f'attachment; filename="Zo Q 2026 v1 {sha}.zip"; filename*=UTF-8\'\'Zo%C3%AB%20Q%202026%20v1%20{sha}.zip'
    )
    # No title anywhere usable: the row's title, then the id.
    (directory / 'artifact.toml').write_text('title = ""\n')
    assert f'filename="Starter {sha}.zip"' in client.get(f'/artifacts/{artifact.id}.zip').headers['content-disposition']


def test_artifact_pdf_needs_the_chrome_service(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)
    monkeypatch.delenv('OPENARTIFACT_CHROME_URL', raising=False)
    response = client.get(f'/artifacts/{artifact.id}.pdf')
    assert response.status_code == 503
    assert 'OPENARTIFACT_CHROME_URL' in response.json()['detail']


def stub_chrome(monkeypatch: pytest.MonkeyPatch, status: int, body: bytes) -> list[str]:
    """Point the server at an in-process stand-in for the chrome service; returns the URLs it was asked to print."""
    asked: list[str] = []
    stub = FastAPI()

    @stub.post('/pdf/')
    async def print_pdf(request: Request) -> Response:
        asked.append((await request.json())['url'])
        return Response(body, status_code=status, media_type='application/pdf' if status == 200 else 'text/plain')

    monkeypatch.setenv('OPENARTIFACT_CHROME_URL', 'http://chrome:8766/')
    monkeypatch.setenv('OPENARTIFACT_INTERNAL_URL', 'http://app:8765')
    monkeypatch.setattr(server, 'chrome_client', lambda: httpx2.AsyncClient(transport=httpx2.ASGITransport(app=stub)))
    return asked


def test_artifact_pdf_is_printed_by_the_chrome_service(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)
    asked = stub_chrome(monkeypatch, 200, b'%PDF-1.4 stub')
    response = client.get(f'/artifacts/{artifact.id}.pdf')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    sha = last_commit(client, artifact)
    assert response.headers['content-disposition'].startswith(f'attachment; filename="OpenArtifact Starter {sha}.pdf"')
    assert response.content == b'%PDF-1.4 stub'
    # The chrome service was given the internal address, and the page had been built for it to fetch.
    assert asked == [f'http://app:8765/artifacts/{artifact.id}/']
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    assert (directory / 'dist' / 'index.html').is_file()


def test_artifact_pdf_reports_chrome_failures(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)
    stub_chrome(monkeypatch, 502, b'Chrome exited with code 3')
    response = client.get(f'/artifacts/{artifact.id}.pdf')
    assert response.status_code == 502
    assert response.json()['detail'] == 'chrome service failed (502): Chrome exited with code 3'
    # A page that does not build is reported before anything is sent to the chrome service.
    asked = stub_chrome(monkeypatch, 200, b'%PDF-1.4 stub')
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    shutil.rmtree(directory / 'dist', ignore_errors=True)
    (directory / 'main.md').write_text('# a\n---\n# b\n')
    response = client.get(f'/artifacts/{artifact.id}.pdf')
    assert response.status_code == 422
    assert asked == []


# The stub stands in for the chrome service, which accepts incoming trace context (`distributed_tracing=True` in
# `chrome/main.py`); here it runs under the test's Logfire configuration, which warns about it instead.
@pytest.mark.filterwarnings('ignore:Found propagated trace context')
def test_artifact_pdf_request_carries_the_trace(
    capfire: CaptureLogfire, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """With httpx instrumented (as `main.py` does), the chrome service is called inside the request's trace."""
    artifact = starter(client)
    seen: list[dict[str, str]] = []
    stub = FastAPI()

    @stub.post('/pdf/')
    async def print_pdf(request: Request) -> Response:
        seen.append(dict(request.headers))
        return Response(b'%PDF-1.4 stub', media_type='application/pdf')

    def instrumented() -> httpx2.AsyncClient:
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=stub))
        # Logfire's signature mentions `httpx.Client`, and only httpx2 is installed here, so pyright sees Unknown.
        logfire.instrument_httpx(http)  # pyright: ignore[reportUnknownMemberType]
        return http

    monkeypatch.setenv('OPENARTIFACT_CHROME_URL', 'http://chrome:8766')
    monkeypatch.setattr(server, 'chrome_client', instrumented)
    assert client.get(f'/artifacts/{artifact.id}.pdf').status_code == 200
    [headers] = seen
    assert 'traceparent' in headers
    # One trace: the request span, the httpx client span inside it (named by method alone, as the stable HTTP
    # conventions say for a client; FastAPI's `fastapi.endpoint` span sits between them), and the stub's own
    # server span as the client span's child, since the header reached it.
    spans = {span['name']: span for span in capfire.exporter.exported_spans_as_dict()}
    request_span = spans['GET /artifacts/{artifact_id}.pdf']
    client_span = spans['POST']
    stub_span = spans['POST /pdf/']
    trace_id = request_span['context']['trace_id']
    assert client_span['context']['trace_id'] == trace_id
    # The stub's parent came in over the wire, so it is the client span's context marked remote.
    assert stub_span['parent'] == {**client_span['context'], 'is_remote': True}
    assert headers['traceparent'].split('-')[1] == format(trace_id, '032x')


def test_exports_of_unknown_artifacts_are_404(client: TestClient):
    missing = uuid.uuid4()
    for suffix in ('.md', '.zip', '.pdf'):
        assert client.get(f'/artifacts/{missing}{suffix}').status_code == 404, suffix
        assert client.get(f'/artifacts/not-a-uuid{suffix}').status_code == 404, suffix


FAR_FUTURE = 4_000_000_000


def upload_to(artifact: workspace.Artifact, path: str, size: int, expires: int = FAR_FUTURE) -> str:
    """The URL the `upload_url` tool would mint, built directly so these tests need no MCP call."""
    return f'/artifacts/{artifact.id}/{path}?token={upload.make_token(artifact.id, path, size, expires)}'


def test_upload_writes_and_commits(client: TestClient):
    artifact = starter(client)
    body = b'\x89PNG not really'
    response = client.put(upload_to(artifact, 'assets/new/pic.png', len(body)), content=body)
    assert response.status_code == 200, response.text
    assert response.json() == {
        'path': 'assets/new/pic.png',
        'size': len(body),
        'sha256': hashlib.sha256(body).hexdigest(),
    }
    # The file is in the checkout, committed on its own, and served back.
    checkout = workspace.checkout_path(artifact.workspace_id)
    assert (checkout / 'artifacts' / str(artifact.id) / 'assets' / 'new' / 'pic.png').read_bytes() == body
    log = in_app(client, lambda: workspace.git('log', '--format=%s', cwd=checkout)).splitlines()
    assert log[0] == 'upload: assets/new/pic.png'
    assert client.get(f'/artifacts/{artifact.id}/assets/new/pic.png').content == body
    # Overwriting an existing file works the same way.
    again = client.put(upload_to(artifact, 'main.md', 6), content=b'# new\n')
    assert again.status_code == 200
    assert client.get(f'/artifacts/{artifact.id}/main.md').text == '# new\n'


def test_upload_rejects_bad_tokens(client: TestClient):
    artifact = starter(client)
    # Wrong size, wrong path, wrong artifact, expired, garbage, missing.
    url = upload_to(artifact, 'assets/x.png', 3)
    assert client.put(url, content=b'four').status_code == 403
    token = url.partition('?token=')[2]
    assert client.put(f'/artifacts/{artifact.id}/assets/y.png?token={token}', content=b'abc').status_code == 403
    other = starter(client)
    assert client.put(f'/artifacts/{other.id}/assets/x.png?token={token}', content=b'abc').status_code == 403
    expired = client.put(upload_to(artifact, 'assets/x.png', 3, expires=1_000), content=b'abc')
    assert expired.status_code == 403 and 'expired' in expired.json()['detail']
    assert client.put(f'/artifacts/{artifact.id}/assets/x.png?token=junk', content=b'abc').status_code == 403
    assert client.put(f'/artifacts/{artifact.id}/assets/x.png', content=b'abc').status_code == 403
    # Nothing was written or committed.
    checkout = workspace.checkout_path(artifact.workspace_id)
    assert not (checkout / 'artifacts' / str(artifact.id) / 'assets' / 'x.png').exists()
    assert not (checkout / 'artifacts' / str(artifact.id) / 'assets' / 'y.png').exists()
    log = in_app(client, lambda: workspace.git('log', '--format=%s', cwd=checkout)).splitlines()
    assert not any(line.startswith('upload:') for line in log)


def test_upload_path_is_checked_even_with_a_valid_token(client: TestClient):
    """A token for a bad path cannot be minted by the tool; the route refuses one anyway."""
    artifact = starter(client)
    for path in ('dist/index.html', 'assets/.gitignore'):
        response = client.put(upload_to(artifact, path, 3), content=b'abc')
        assert response.status_code == 403, path
    # A directory in the way is a conflict, not a crash.
    response = client.put(upload_to(artifact, 'components', 3), content=b'abc')
    assert response.status_code == 409
    response = client.put(upload_to(artifact, 'main.md/x.txt', 3), content=b'abc')
    assert response.status_code == 409


def test_upload_cannot_follow_a_symlink_out(client: TestClient, tmp_path: Path):
    artifact = starter(client)
    outside = tmp_path / 'outside'
    outside.mkdir()
    directory = workspace.checkout_path(artifact.workspace_id) / 'artifacts' / str(artifact.id)
    (directory / 'assets' / 'link').symlink_to(outside)
    response = client.put(upload_to(artifact, 'assets/link/x.png', 3), content=b'abc')
    assert response.status_code == 403
    assert not (outside / 'x.png').exists()


def test_upload_needs_a_content_length(client: TestClient):
    artifact = starter(client)

    def chunks() -> Iterator[bytes]:
        yield b'abc'

    # A generator body is sent chunked, without Content-Length.
    response = client.put(upload_to(artifact, 'assets/x.png', 3), content=chunks())
    assert response.status_code == 411


def test_upload_to_unknown_artifact_is_404(client: TestClient):
    missing = uuid.uuid4()
    token = upload.make_token(missing, 'main.md', 3, FAR_FUTURE)
    assert client.put(f'/artifacts/{missing}/main.md?token={token}', content=b'abc').status_code == 404
    assert client.put('/artifacts/not-a-uuid/main.md?token=x', content=b'abc').status_code == 404


def test_mcp_requires_a_token(client: TestClient):
    response = client.post(
        '/mcp/',
        headers={'accept': 'application/json, text/event-stream'},
        json={
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 't', 'version': '0'},
            },
        },
    )
    assert response.status_code == 401


@pytest.fixture
def live_server(server_env: None) -> Iterator[str]:
    """Run the app in a uvicorn thread on a free port; the MCP transport needs a real HTTP connection."""
    config = uvicorn.Config(server.app, host='127.0.0.1', port=0, log_level='warning')
    live = uvicorn.Server(config)
    thread = threading.Thread(target=live.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not live.started:
        # A failing lifespan ends the thread without ever setting `started`; do not spin forever on it.
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError('uvicorn did not start')
        thread.join(0.01)
    port = live.servers[0].sockets[0].getsockname()[1]
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        live.should_exit = True
        thread.join(5)


@pytest.mark.anyio
async def test_mcp_over_http(live_server: str):
    async with Client(f'{live_server}/mcp/', auth=DEV_TOKEN) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert tools == {'new_artifact', 'run_code', 'build', 'upload_url', 'list_artifacts'}
        created = await client.call_tool('new_artifact', {'title': 'Demo', 'content': '# Demo\n'})
        name = created.data.partition('\n')[0].removeprefix('artifact: ')
        result = await client.call_tool(
            'run_code',
            {'artifact': name, 'code': "from pathlib import Path\nPath('main.md').write_text('# Demo\\n')"},
        )
        assert result.data == '7\n'
        built = await client.call_tool('build', {'artifact': name})
        assert built.data.endswith(f'page: http://127.0.0.1:8765/artifacts/{name}/\n')
        listed = await client.call_tool('list_artifacts', {})
        assert listed.data.startswith(f'{name}  deck  Demo  ')
        logo = (STARTER / 'assets' / 'logo.svg').read_bytes()
        minted = await client.call_tool('upload_url', {'artifact': name, 'files': [['assets/logo.svg', len(logo)]]})
        [upload_url] = minted.data
    async with httpx2.AsyncClient() as http:
        # The page the tool pointed at is served by the same process.
        page = await http.get(f'{live_server}/artifacts/{name}/')
        assert page.status_code == 200
        assert 'id="artifact-markdown"' in page.text
        # The minted URL carries the configured base URL; this server is on a free port.
        uploaded = await http.put(upload_url.replace('http://127.0.0.1:8765', live_server), content=logo)
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()['sha256'] == hashlib.sha256(logo).hexdigest()
        served = await http.get(f'{live_server}/artifacts/{name}/assets/logo.svg')
        assert served.content == logo


@pytest.mark.anyio
async def test_native_telemetry_reaches_logfire(live_server: str, capfire: CaptureLogfire):
    """FastAPI, FastMCP, asyncpg and monty spans all land in Logfire with no `instrument_*` beyond the two hooks."""
    logfire.instrument_asyncpg()
    logfire.instrument_monty()
    async with httpx2.AsyncClient() as http:
        assert (await http.get(f'{live_server}/')).status_code == 200
    async with Client(f'{live_server}/mcp/', auth=DEV_TOKEN) as client:
        created = await client.call_tool('new_artifact', {'title': 'T', 'content': '# T\n'})
        name = created.data.partition('\n')[0].removeprefix('artifact: ')
        await client.call_tool('run_code', {'artifact': name, 'code': "print('hi')"})
    spans = capfire.exporter.exported_spans_as_dict()
    names = {span['name'] for span in spans}
    # Exported names are message templates: FastAPI's route, FastMCP's tool, monty's session and run, our git spans.
    assert {
        'GET /',
        'fastapi.endpoint',
        'POST /mcp/{path}',
        'tools/call run_code',
        'session {script_name}',
        'run code',
        'git {argv}',
    } <= names
    assert any(span['attributes'].get('db.system') == 'postgresql' for span in spans)
