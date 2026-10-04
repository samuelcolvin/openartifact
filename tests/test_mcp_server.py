"""Tests for `backend/mcp_server.py`: the tools called directly as the test user, and once over MCP in memory."""

from __future__ import annotations

import re
import tomllib
import uuid
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import auth
import build
import db
import mcp_server
import workspace

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / 'examples' / 'starter'
UUID_RE = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

pytestmark = pytest.mark.anyio


@pytest.fixture
async def pool() -> AsyncGenerator[None]:
    """The worker pool `run_code` needs; in production the app lifespan opens it."""
    async with mcp_server.monty_pool():
        yield


@pytest.fixture
def me(principal: auth.Principal) -> Iterator[auth.Principal]:
    """Run the test as the signed-in user, the way a verified token would."""
    with auth.as_principal(principal):
        yield principal


def artifact_id(output: str) -> str:
    """Pull the identifier out of `new_artifact`'s result."""
    first, _, _ = output.partition('\n')
    assert first.startswith('artifact: ')
    return first.removeprefix('artifact: ')


def files_of(me: auth.Principal, artifact: str) -> Path:
    return workspace.checkout_path(me.workspace_id) / 'artifacts' / artifact


@pytest.fixture
async def demo(me: auth.Principal) -> str:
    """An existing deck with one slide; returns its id."""
    return artifact_id(await mcp_server.new_artifact('Demo', '# Demo\n'))


async def head_sha(ws: uuid.UUID) -> str | None:
    return await db.pool().fetchval('SELECT head_sha FROM workspaces WHERE id = $1', ws)


# --- new_artifact ----------------------------------------------------------


async def test_new_artifact_builds_and_commits(me: auth.Principal):
    out = await mcp_server.new_artifact('My Deck!', '# Hello\n', theme='dark')
    artifact = artifact_id(out)
    assert re.fullmatch(UUID_RE, artifact)
    assert out.endswith(f'page: http://127.0.0.1:8765/artifacts/{artifact}/\n')
    directory = files_of(me, artifact)
    assert (directory / 'main.md').read_text() == '# Hello\n'
    assert (directory / 'artifact.toml').read_text() == ('title = "My Deck!"\ntype = "deck"\ntheme = "dark"\n')
    assert (directory / 'dist' / 'index.html').is_file()
    # One commit, recorded as the head, bundled; the build output is not in it.
    path = workspace.checkout_path(me.workspace_id)
    assert (await workspace.git('log', '--format=%s', cwd=path)).splitlines() == [f'new_artifact: {artifact}']
    assert await head_sha(me.workspace_id) == (await workspace.git('rev-parse', 'HEAD', cwd=path)).strip()
    assert 'dist' not in await workspace.git('ls-files', cwd=path)
    row = await workspace.get_artifact(uuid.UUID(artifact))
    assert row is not None and (row.title, row.type, row.workspace_id) == ('My Deck!', 'deck', me.workspace_id)


async def test_new_artifact_reports_build_errors_and_keeps_files(me: auth.Principal):
    with pytest.raises(ToolError, match=r'main\.md:2: put a blank line before ---'):
        await mcp_server.new_artifact('Broken', '# heading\n---\n# next\n')
    [row] = await workspace.list_artifacts(me.workspace_id)
    directory = files_of(me, str(row.id))
    assert (directory / 'main.md').read_text() == '# heading\n---\n# next\n'
    assert not (directory / 'dist').exists()
    assert await head_sha(me.workspace_id) is not None


async def test_new_artifact_page_takes_plain_markdown(me: auth.Principal):
    artifact = artifact_id(await mcp_server.new_artifact('Notes', '# Notes\n\nSome text.\n', type='page'))
    assert (files_of(me, artifact) / 'artifact.toml').read_text() == 'title = "Notes"\ntype = "page"\ntheme = "light"\n'
    assert (files_of(me, artifact) / 'dist' / 'index.html').is_file()


async def test_new_artifact_rejects_old_slide_markers(me: auth.Principal):
    with pytest.raises(ToolError, match=r'main\.md:1: <slide \.\.\./> is no longer supported'):
        await mcp_server.new_artifact('Doc', '<slide/>\n# Doc\n', type='document')


async def test_new_artifact_reports_unknown_placeholders(me: auth.Principal):
    with pytest.raises(ToolError, match=r'main\.md:3: unknown placeholder \{\{ AUTHOR \}\}; built-ins and \[context\]'):
        await mcp_server.new_artifact('Doc', '# Doc\n\nBy {{ AUTHOR }}\n', type='document')


def test_literals_match_builder():
    assert set(mcp_server.THEMES) == set(build.THEMES)
    assert set(mcp_server.TYPES) == set(build.TYPES)


def test_render_toml_escapes():
    text = mcp_server.render_toml({'title': 'He said "hi"\\ \n done'})
    assert tomllib.loads(text) == {'title': 'He said "hi"\\ \n done'}


# --- run_code --------------------------------------------------------------


async def test_run_code_writes_and_commits(me: auth.Principal, demo: str, pool: None):
    out = await mcp_server.run_code(
        demo, "from pathlib import Path\nPath('main.md').write_text('# hi\\n')\nprint('done')\nlen('abc')"
    )
    assert out == 'done\n3\n'
    assert (files_of(me, demo) / 'main.md').read_text() == '# hi\n'
    log = (await workspace.git('log', '--format=%s', cwd=workspace.checkout_path(me.workspace_id))).splitlines()
    assert log == [f'run_code: {demo}', f'new_artifact: {demo}']


async def test_run_code_mounts_at_virtual_path(me: auth.Principal, demo: str, pool: None):
    await mcp_server.run_code(demo, "open('/artifact/a.txt', 'w').write('x')")
    out = await mcp_server.run_code(demo, "import os\nprint(os.getcwd())\nprint(sorted(os.listdir('.')))")
    assert out == "/artifact\n['a.txt', 'artifact.toml', 'dist', 'main.md']\n"


async def test_run_code_binds_inputs(me: auth.Principal, demo: str, pool: None):
    out = await mcp_server.run_code(
        demo, "from pathlib import Path\nPath('main.md').write_text(body * n)", inputs={'body': 'ab', 'n': 3}
    )
    # `write_text` returns the character count, which is the trailing expression.
    assert out == '6\n'
    assert (files_of(me, demo) / 'main.md').read_text() == 'ababab'


async def test_run_code_cannot_escape_mount(me: auth.Principal, demo: str, pool: None):
    with pytest.raises(ToolError, match='PermissionError'):
        await mcp_server.run_code(demo, "open('/etc/hosts').read()")


async def test_run_code_commits_even_when_the_code_fails(me: auth.Principal, demo: str, pool: None):
    with pytest.raises(ToolError) as exc_info:
        await mcp_server.run_code(
            demo, "from pathlib import Path\nPath('partial.txt').write_text('p')\nprint('before')\n1 / 0"
        )
    message = str(exc_info.value)
    assert message.startswith('before\n')
    assert 'ZeroDivisionError: division by zero' in message
    assert (files_of(me, demo) / 'partial.txt').read_text() == 'p'
    log = (await workspace.git('log', '--format=%s', cwd=workspace.checkout_path(me.workspace_id))).splitlines()
    assert log[0] == f'run_code (failed): {demo}'


async def test_run_code_reports_syntax_errors(me: auth.Principal, demo: str, pool: None):
    with pytest.raises(ToolError, match='SyntaxError'):
        await mcp_server.run_code(demo, 'def (')


async def test_run_code_rejects_bad_ids(me: auth.Principal, pool: None):
    with pytest.raises(ToolError, match='invalid artifact id'):
        await mcp_server.run_code('../escape', 'pass')
    with pytest.raises(ToolError, match='does not exist'):
        await mcp_server.run_code(str(uuid.uuid4()), 'pass')


async def test_run_code_without_pool(me: auth.Principal, demo: str):
    with pytest.raises(RuntimeError, match='monty pool is not running'):
        await mcp_server.run_code(demo, 'pass')


# --- build and list ----------------------------------------------------------


async def test_build_missing_artifact(me: auth.Principal):
    with pytest.raises(ToolError, match='does not exist'):
        await mcp_server.build_artifact(str(uuid.uuid4()))


async def test_build_imported_starter(me: auth.Principal, pool: None):
    row = await workspace.import_directory(me.workspace_id, 'Starter', 'deck', STARTER)
    out = await mcp_server.build_artifact(str(row.id))
    assert out.endswith(f'page: http://127.0.0.1:8765/artifacts/{row.id}/\n')
    # The sandbox sees the output under the mount.
    listing = await mcp_server.run_code(str(row.id), "import os\nprint(sorted(os.listdir('/artifact/dist')))")
    assert listing == "['index.html']\n"


async def test_build_reports_validation_errors(me: auth.Principal, demo: str, pool: None):
    await mcp_server.run_code(demo, "from pathlib import Path\nPath('main.md').write_text('# a\\n---\\n# b\\n')")
    with pytest.raises(ToolError, match=r'main\.md:2: put a blank line before ---'):
        await mcp_server.build_artifact(demo)


async def test_list_artifacts(me: auth.Principal):
    assert await mcp_server.list_artifacts() == 'no artifacts yet; create one with `new_artifact`\n'
    first = artifact_id(await mcp_server.new_artifact('First', '# 1\n'))
    second = artifact_id(await mcp_server.new_artifact('Second', '# 2\n', type='page'))
    assert await mcp_server.list_artifacts() == (
        f'{first}  deck  First  http://127.0.0.1:8765/artifacts/{first}/\n'
        f'{second}  page  Second  http://127.0.0.1:8765/artifacts/{second}/\n'
    )


async def test_other_users_cannot_see_my_artifacts(me: auth.Principal, demo: str, db_pool: db.Pool, pool: None):
    async with db_pool.acquire() as conn, conn.transaction():
        other = await auth.upsert_user(conn, sub='user-b', email='b@example.com', name=None, picture=None)
    with auth.as_principal(other):
        assert await mcp_server.list_artifacts() == 'no artifacts yet; create one with `new_artifact`\n'
        with pytest.raises(ToolError, match='does not exist'):
            await mcp_server.build_artifact(demo)
        with pytest.raises(ToolError, match='does not exist'):
            await mcp_server.run_code(demo, 'pass')


async def test_tools_require_a_caller(db_pool: db.Pool, storage: object):
    with pytest.raises(ToolError, match='not authenticated'):
        await mcp_server.list_artifacts()


# --- MCP wiring ------------------------------------------------------------


async def test_skill_is_served(me: auth.Principal):
    skill_file = ROOT / 'skills' / 'openartifact' / 'SKILL.md'
    async with Client(mcp_server.mcp) as client:
        resources = {str(r.uri): r for r in await client.list_resources()}
        assert mcp_server.SKILL_URI in resources
        description = resources[mcp_server.SKILL_URI].description
        assert description is not None and description.startswith('Create a deck with OpenArtifact.')
        [content] = await client.read_resource(mcp_server.SKILL_URI)
        assert getattr(content, 'text', None) == skill_file.read_text(encoding='utf-8')
        # Supporting files are reachable through the template, and the manifest lists them.
        templates = [t.uri_template for t in await client.list_resource_templates()]
        assert 'skill://openartifact/{path*}' in templates
        [manifest] = await client.read_resource('skill://openartifact/_manifest')
        assert '"skill": "openartifact"' in getattr(manifest, 'text', '')
    assert mcp_server.mcp.instructions is not None and mcp_server.SKILL_URI in mcp_server.mcp.instructions


async def test_tools_over_mcp(me: auth.Principal, pool: None):
    async with Client(mcp_server.mcp) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert tools == {'new_artifact', 'run_code', 'build', 'list_artifacts'}

        created = await client.call_tool('new_artifact', {'title': 'Demo', 'content': '# Demo\n'})
        name = artifact_id(created.data)

        result = await client.call_tool(
            'run_code',
            {
                'artifact': name,
                'code': "from pathlib import Path\nPath('main.md').write_text(md)",
                'inputs': {'md': '<component src="Nope.html"></component>'},
            },
        )
        assert result.data == '39\n'
        assert (files_of(me, name) / 'main.md').read_text() == '<component src="Nope.html"></component>'

        failed = await client.call_tool('build', {'artifact': name}, raise_on_error=False)
        assert failed.is_error

        # `theme` and `type` are Literals, so bad values are rejected by the schema before the tool runs.
        for bad in ({'theme': 'neon'}, {'type': 'scroll'}):
            rejected = await client.call_tool(
                'new_artifact', {'title': 'T', 'content': '# T\n', **bad}, raise_on_error=False
            )
            assert rejected.is_error
        assert len(await workspace.list_artifacts(me.workspace_id)) == 1
