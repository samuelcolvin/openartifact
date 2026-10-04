"""Tests for `backend/server.py`: the HTTP routes with the whole app running, and MCP over real HTTP."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx2
import pytest
import uvicorn
from conftest import DEV_TOKEN
from fastapi.testclient import TestClient
from fastmcp import Client
from logfire.testing import CaptureLogfire

import auth
import db
import server
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
            (tx.artifact_dir(artifact.id) / 'main.md').write_text('# preamble\n<slide/>\n')

    in_app(client, break_it)
    response = client.get(f'/artifacts/{artifact.id}/')
    assert response.status_code == 422
    assert 'content before the first' in response.json()['detail']
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


def test_artifact_sources_stay_private(client: TestClient):
    artifact = starter(client)
    for path in ('main.md', 'artifact.toml', 'styles.css', 'components/Hero.html', 'dist/index.html'):
        assert client.get(f'/artifacts/{artifact.id}/{path}').status_code == 404, path
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
        assert tools == {'new_artifact', 'run_code', 'build', 'list_artifacts'}
        created = await client.call_tool('new_artifact', {'title': 'Demo', 'content': '<slide/>\n# Demo\n'})
        name = created.data.partition('\n')[0].removeprefix('artifact: ')
        result = await client.call_tool(
            'run_code',
            {'artifact': name, 'code': "from pathlib import Path\nPath('main.md').write_text('<slide/>\\n')"},
        )
        assert result.data == '9\n'
        built = await client.call_tool('build', {'artifact': name})
        assert built.data.endswith(f'page: http://127.0.0.1:8000/artifacts/{name}/\n')
        listed = await client.call_tool('list_artifacts', {})
        assert listed.data.startswith(f'{name}  deck  Demo  ')
    # The page the tool pointed at is served by the same process.
    async with httpx2.AsyncClient() as http:
        page = await http.get(f'{live_server}/artifacts/{name}/')
        assert page.status_code == 200
        assert 'id="artifact-data"' in page.text


@pytest.mark.anyio
async def test_native_telemetry_reaches_logfire(live_server: str, capfire: CaptureLogfire):
    """FastAPI, FastMCP, asyncpg and monty spans all land in Logfire with no `instrument_*` beyond the two hooks."""
    async with httpx2.AsyncClient() as http:
        assert (await http.get(f'{live_server}/')).status_code == 200
    async with Client(f'{live_server}/mcp/', auth=DEV_TOKEN) as client:
        created = await client.call_tool('new_artifact', {'title': 'T', 'content': '<slide/>\n# T\n'})
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
