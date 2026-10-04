"""Tests for `backend/server.py`. Run with `uv run pytest`."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient
from fastmcp import Client
from logfire.testing import CaptureLogfire

import mcp_server
import server

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / 'examples' / 'starter'


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
def artifacts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / 'artifacts'
    monkeypatch.setenv('OPENARTIFACT_ROOT', str(root))
    return root


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as client:
        yield client


def build_starter(artifacts_root: Path) -> None:
    shutil.copytree(STARTER, artifacts_root / 'starter', ignore=shutil.ignore_patterns('dist'))
    mcp_server.build.build_html(artifacts_root / 'starter')


def test_index_lists_built_artifacts(client: TestClient, artifacts_root: Path):
    assert client.get('/').json() == {'mcp': '/mcp/', 'runtime': '/openartifact.js', 'artifacts': {}}
    build_starter(artifacts_root)
    assert client.get('/').json()['artifacts'] == {'starter': 'http://127.0.0.1:8000/artifacts/starter/'}


def test_runtime_js(client: TestClient):
    response = client.get('/openartifact.js')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/javascript')
    assert response.content == server.RUNTIME_JS_PATH.read_bytes()


def test_artifact_not_built(client: TestClient, artifacts_root: Path):
    assert client.get('/artifacts/starter/').status_code == 404
    assert client.get('/artifacts/starter/assets/logo.svg').status_code == 404


def test_artifact_page_links_the_hosted_runtime(client: TestClient, artifacts_root: Path):
    build_starter(artifacts_root)
    page = client.get('/artifacts/starter/')
    assert page.status_code == 200
    assert page.headers['content-type'].startswith('text/html')
    assert '<script src="/openartifact.js"></script>' in page.text
    assert 'data:' not in page.text
    # Nothing is copied next to the page any more.
    assert client.get('/artifacts/starter/dist/openartifact.js').status_code == 404


def test_artifact_images_are_served(client: TestClient, artifacts_root: Path):
    build_starter(artifacts_root)
    logo = client.get('/artifacts/starter/assets/logo.svg')
    assert logo.status_code == 200
    assert logo.headers['content-type'].startswith('image/svg+xml')
    assert logo.content == (artifacts_root / 'starter' / 'assets' / 'logo.svg').read_bytes()
    # Fonts declared in styles.css are served too.
    (artifacts_root / 'starter' / 'assets' / 'body.woff2').write_bytes(b'wOF2')
    assert client.get('/artifacts/starter/assets/body.woff2').status_code == 200


def test_artifact_sources_stay_private(client: TestClient, artifacts_root: Path):
    build_starter(artifacts_root)
    for path in ('deck.md', 'artifact.toml', 'styles.css', 'components/Hero.html', 'dist/index.html'):
        assert client.get(f'/artifacts/starter/{path}').status_code == 404, path
    assert client.get('/artifacts/starter/..').status_code == 404
    assert client.get('/artifacts/Bad%20Name/').status_code == 404
    # `..` that leaves the artifact directory is refused, even when the target is another artifact's image. The
    # segments are percent-encoded because the HTTP client collapses a literal `..` before sending.
    (artifacts_root / 'other' / 'assets').mkdir(parents=True)
    (artifacts_root / 'other' / 'assets' / 'x.png').write_bytes(b'png')
    assert client.get('/artifacts/starter/%2e%2e/other/assets/x.png').status_code == 404
    assert client.get('/artifacts/starter/assets/%2e%2e/%2e%2e/other/assets/x.png').status_code == 404
    # ...while `..` that stays inside it is just a path to the same file.
    assert client.get('/artifacts/starter/assets/../assets/logo.svg').status_code == 200


def test_artifact_media_cannot_escape_via_symlink(client: TestClient, artifacts_root: Path, tmp_path: Path):
    build_starter(artifacts_root)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'png')
    (artifacts_root / 'starter' / 'assets' / 'link.png').symlink_to(outside)
    assert client.get('/artifacts/starter/assets/link.png').status_code == 404


def test_artifact_redirects_to_trailing_slash(client: TestClient, artifacts_root: Path):
    response = client.get('/artifacts/starter', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/artifacts/starter/'


@pytest.fixture
def live_server() -> Iterator[str]:
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
async def test_mcp_over_http(live_server: str, artifacts_root: Path):
    async with Client(f'{live_server}/mcp/') as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert tools == {'new_artifact', 'run_code', 'build'}
        created = await client.call_tool('new_artifact', {'title': 'Demo', 'content': '<slide/>\n# Demo\n'})
        name = created.data.partition('\n')[0].removeprefix('artifact: ')
        result = await client.call_tool(
            'run_code',
            {'artifact': name, 'code': "from pathlib import Path\nPath('deck.md').write_text('<slide/>\\n')"},
        )
        assert result.data == '9\n'
        built = await client.call_tool('build', {'artifact': name})
        assert built.data.endswith(f'page: http://127.0.0.1:8000/artifacts/{name}/\n')


@pytest.mark.anyio
async def test_native_telemetry_reaches_logfire(live_server: str, artifacts_root: Path, capfire: CaptureLogfire):
    """FastAPI, FastMCP and monty spans all land in Logfire with no `instrument_*` calls beyond monty's hook."""
    async with httpx2.AsyncClient() as http:
        assert (await http.get(f'{live_server}/')).status_code == 200
    (artifacts_root / 'demo').mkdir(parents=True)
    async with Client(f'{live_server}/mcp/') as client:
        await client.call_tool('run_code', {'artifact': 'demo', 'code': "print('hi')"})
    names = {span['name'] for span in capfire.exporter.exported_spans_as_dict()}
    # Exported names are message templates: FastAPI's route, FastMCP's MCP method and tool, monty's session and run.
    assert {
        'GET /',
        'fastapi.endpoint',
        'POST /mcp/{path}',
        'tools/call run_code',
        'session {script_name}',
        'run code',
    } <= names
