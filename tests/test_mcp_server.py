"""Tests for `backend/mcp_server.py`. Run with `uv run pytest`."""

from __future__ import annotations

import re
import shutil
import tomllib
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import build
import mcp_server

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / 'examples' / 'starter'

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
def artifacts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the server at a temporary artifacts root."""
    root = tmp_path / 'artifacts'
    monkeypatch.setenv('OPENARTIFACT_ROOT', str(root))
    return root


@pytest.fixture
async def pool() -> AsyncGenerator[None]:
    """The worker pool `run_code` needs; in production the app lifespan opens it."""
    async with mcp_server.monty_pool():
        yield


@pytest.fixture
def demo(artifacts_root: Path) -> Path:
    """An existing, empty artifact called `demo`, as `new_artifact` would have left one."""
    path = artifacts_root / 'demo'
    path.mkdir(parents=True)
    return path


def artifact_id(output: str) -> str:
    """Pull the identifier out of `new_artifact`'s result."""
    first, _, _ = output.partition('\n')
    assert first.startswith('artifact: ')
    return first.removeprefix('artifact: ')


# --- new_artifact ----------------------------------------------------------


async def test_new_artifact_builds(artifacts_root: Path):
    out = await mcp_server.new_artifact('My Deck!', '<slide/>\n# Hello\n', theme='dark', footer='ACME')
    name = artifact_id(out)
    assert re.fullmatch(r'my-deck-[a-z0-9]{6}', name)
    assert out.endswith(f'page: http://127.0.0.1:8000/artifacts/{name}/\n')
    directory = artifacts_root / name
    assert (directory / 'deck.md').read_text() == '<slide/>\n# Hello\n'
    assert (directory / 'artifact.toml').read_text() == (
        'title = "My Deck!"\ntype = "deck"\ntheme = "dark"\nfooter = "ACME"\n'
    )
    assert (directory / 'dist' / 'index.html').is_file()
    assert mcp_server.ARTIFACT_NAME_RE.fullmatch(name)


async def test_new_artifact_reports_build_errors_and_keeps_files(artifacts_root: Path):
    with pytest.raises(ToolError, match=r'deck\.md:1: content before the first') as exc_info:
        await mcp_server.new_artifact('Broken', '# preamble\n<slide/>\n')
    assert exc_info.type is ToolError
    [directory] = artifacts_root.iterdir()
    assert (directory / 'deck.md').read_text() == '# preamble\n<slide/>\n'
    assert not (directory / 'dist').exists()


def test_new_artifact_id_handles_odd_titles():
    assert re.fullmatch(r'artifact-[a-z0-9]{6}', mcp_server.new_artifact_id('!!!'))
    assert re.fullmatch(r'caf-au-lait-[a-z0-9]{6}', mcp_server.new_artifact_id('  Café au lait  '))
    long = mcp_server.new_artifact_id('x' * 100)
    assert mcp_server.ARTIFACT_NAME_RE.fullmatch(long)
    assert len(long) == mcp_server.SLUG_MAX_LEN + 1 + mcp_server.SUFFIX_LEN


def test_theme_literal_matches_builder():
    assert set(mcp_server.THEMES) == set(build.THEMES)


def test_type_literal_matches_builder():
    assert set(mcp_server.TYPES) == set(build.TYPES)


async def test_new_artifact_page_takes_plain_markdown(artifacts_root: Path):
    out = await mcp_server.new_artifact('Notes', '# Notes\n\nSome text.\n', type='page')
    name = artifact_id(out)
    assert (artifacts_root / name / 'artifact.toml').read_text() == 'title = "Notes"\ntype = "page"\ntheme = "light"\n'
    assert (artifacts_root / name / 'dist' / 'index.html').is_file()


async def test_new_artifact_document_rejects_slide_markers(artifacts_root: Path):
    with pytest.raises(ToolError, match=r'markers are only used when type = "deck"; this artifact is a document'):
        await mcp_server.new_artifact('Doc', '<slide/>\n# Doc\n', type='document')


def test_render_toml_escapes():
    text = mcp_server.render_toml({'title': 'He said "hi"\\ \n done'})
    assert tomllib.loads(text) == {'title': 'He said "hi"\\ \n done'}


# --- run_code --------------------------------------------------------------


async def test_run_code_writes_files(artifacts_root: Path, demo: Path, pool: None):
    out = await mcp_server.run_code(
        'demo',
        "from pathlib import Path\nPath('deck.md').write_text('<slide/>\\n# hi\\n')\nprint('done')\nlen('abc')",
    )
    assert out == 'done\n3\n'
    assert (artifacts_root / 'demo' / 'deck.md').read_text() == '<slide/>\n# hi\n'


async def test_run_code_mounts_at_virtual_path(demo: Path, pool: None):
    await mcp_server.run_code('demo', "open('/artifact/a.txt', 'w').write('x')")
    out = await mcp_server.run_code('demo', "import os\nprint(os.getcwd())\nprint(sorted(os.listdir('.')))")
    assert out == "/artifact\n['a.txt']\n"


async def test_run_code_binds_inputs(artifacts_root: Path, demo: Path, pool: None):
    out = await mcp_server.run_code(
        'demo', "from pathlib import Path\nPath('deck.md').write_text(body * n)", inputs={'body': 'ab', 'n': 3}
    )
    # `write_text` returns the character count, which is the trailing expression.
    assert out == '6\n'
    assert (artifacts_root / 'demo' / 'deck.md').read_text() == 'ababab'


async def test_run_code_cannot_escape_mount(demo: Path, pool: None):
    with pytest.raises(ToolError, match='PermissionError'):
        await mcp_server.run_code('demo', "open('/etc/hosts').read()")


async def test_run_code_reports_exceptions_with_output(demo: Path, pool: None):
    with pytest.raises(ToolError) as exc_info:
        await mcp_server.run_code('demo', "print('before')\n1 / 0")
    message = str(exc_info.value)
    assert message.startswith('before\n')
    assert 'ZeroDivisionError: division by zero' in message


async def test_run_code_reports_syntax_errors(demo: Path, pool: None):
    with pytest.raises(ToolError, match='SyntaxError'):
        await mcp_server.run_code('demo', 'def (')


async def test_run_code_rejects_bad_names(artifacts_root: Path, pool: None):
    with pytest.raises(ToolError, match='invalid artifact name'):
        await mcp_server.run_code('../escape', 'pass')
    assert not artifacts_root.exists()


async def test_run_code_requires_existing_artifact(artifacts_root: Path, pool: None):
    with pytest.raises(ToolError, match='does not exist; create one with `new_artifact`'):
        await mcp_server.run_code('demo', 'pass')
    assert not artifacts_root.exists()


async def test_run_code_without_pool(demo: Path):
    with pytest.raises(RuntimeError, match='monty pool is not running'):
        await mcp_server.run_code('demo', 'pass')


# --- build -----------------------------------------------------------------


async def test_build_missing_artifact(artifacts_root: Path):
    with pytest.raises(ToolError, match='does not exist'):
        await mcp_server.build_artifact('nope')


async def test_build_starter(artifacts_root: Path, pool: None):
    shutil.copytree(STARTER, artifacts_root / 'starter', ignore=shutil.ignore_patterns('dist'))
    out = await mcp_server.build_artifact('starter')
    assert 'index.html' in out
    assert out.endswith('page: http://127.0.0.1:8000/artifacts/starter/\n')
    assert (artifacts_root / 'starter' / 'dist' / 'index.html').is_file()
    # The sandbox sees the output under the mount.
    listing = await mcp_server.run_code('starter', "import os\nprint(sorted(os.listdir('/artifact/dist')))")
    assert listing == "['index.html']\n"


async def test_build_reports_validation_errors(demo: Path, pool: None):
    await mcp_server.run_code(
        'demo', "from pathlib import Path\nPath('deck.md').write_text('# preamble\\n<slide/>\\n')"
    )
    with pytest.raises(ToolError, match=r'deck\.md:1: content before the first'):
        await mcp_server.build_artifact('demo')


# --- MCP wiring ------------------------------------------------------------


async def test_tools_over_mcp(artifacts_root: Path, pool: None):
    async with Client(mcp_server.mcp) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert tools == {'new_artifact', 'run_code', 'build'}

        created = await client.call_tool('new_artifact', {'title': 'Demo', 'content': '<slide/>\n# Demo\n'})
        name = artifact_id(created.data)

        result = await client.call_tool(
            'run_code',
            {
                'artifact': name,
                'code': "from pathlib import Path\nPath('deck.md').write_text(md)",
                'inputs': {'md': 'x'},
            },
        )
        assert result.data == '1\n'
        assert (artifacts_root / name / 'deck.md').read_text() == 'x'

        failed = await client.call_tool('build', {'artifact': name}, raise_on_error=False)
        assert failed.is_error

        # `theme` is a Literal, so a bad value is rejected by the schema before the tool runs.
        bad_theme = await client.call_tool(
            'new_artifact', {'title': 'T', 'content': '<slide/>\n', 'theme': 'neon'}, raise_on_error=False
        )
        assert bad_theme.is_error
        bad_type = await client.call_tool(
            'new_artifact', {'title': 'T', 'content': '# T\n', 'type': 'scroll'}, raise_on_error=False
        )
        assert bad_type.is_error
        assert [p.name for p in artifacts_root.iterdir()] == [name]
