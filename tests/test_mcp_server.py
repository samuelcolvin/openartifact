"""Tests for `backend/mcp_server.py`: the tools called directly as the test user, and once over MCP in memory."""

from __future__ import annotations

import re
import tomllib
import uuid
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import render
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastmcp import Client
from fastmcp.exceptions import ToolError

import auth
import build
import db
import mcp_server
import store
import upload
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


async def new_artifact(title: str, content: str, **kwargs: Any) -> str:
    """A private personal artifact: what most tests need."""
    return await mcp_server.new_personal_artifact(title, content, False, **kwargs)


def artifact_id(output: str) -> str:
    """Pull the identifier out of a creation tool's result."""
    first, _, _ = output.partition('\n')
    assert first.startswith('artifact: ')
    return first.removeprefix('artifact: ')


def files_of(me: auth.Principal, artifact: str) -> Path:
    return workspace.checkout_path(uuid.UUID(artifact))


async def git_log(artifact: str) -> list[str]:
    return (await workspace.artifact_git(uuid.UUID(artifact), 'log', '--format=%s')).splitlines()


@pytest.fixture
async def demo(me: auth.Principal) -> str:
    """An existing deck with one slide; returns its id."""
    return artifact_id(await new_artifact('Demo', '# Demo\n'))


async def head_sha(artifact: str) -> str | None:
    return await db.pool().fetchval('SELECT head_sha FROM artifacts WHERE id = $1', uuid.UUID(artifact))


# --- new_artifact ----------------------------------------------------------


async def test_new_artifact_builds_and_commits(me: auth.Principal):
    out = await new_artifact('My Deck!', '# Hello\n', theme='dark')
    artifact = artifact_id(out)
    assert re.fullmatch(UUID_RE, artifact)
    assert out.endswith(f'page: http://127.0.0.1:8765/artifacts/{artifact}/\n')
    directory = files_of(me, artifact)
    assert (directory / 'main.md').read_text() == '# Hello\n'
    assert (directory / 'artifact.toml').read_text() == ('title = "My Deck!"\ntype = "deck"\ntheme = "dark"\n')
    assert (directory / 'dist' / 'index.html').is_file()
    # One commit, recorded as the head, bundled; the build output is not in it.
    assert await git_log(artifact) == [f'new_artifact: {artifact}']
    assert await head_sha(artifact) == (await workspace.artifact_git(uuid.UUID(artifact), 'rev-parse', 'HEAD')).strip()
    assert 'dist' not in await workspace.artifact_git(uuid.UUID(artifact), 'ls-files')
    row = await workspace.get_artifact(uuid.UUID(artifact))
    assert row is not None and (row.title, row.type, row.workspace_id) == ('My Deck!', 'deck', me.workspace_id)


async def test_new_artifact_reports_build_errors_and_keeps_files(me: auth.Principal):
    with pytest.raises(ToolError, match=r'main\.md:2: put a blank line before ---'):
        await new_artifact('Broken', '# heading\n---\n# next\n')
    [row] = await workspace.list_artifacts(me.workspace_id)
    directory = files_of(me, str(row.id))
    assert (directory / 'main.md').read_text() == '# heading\n---\n# next\n'
    assert not (directory / 'dist').exists()
    assert await head_sha(str(row.id)) is not None


async def test_new_artifact_page_takes_plain_markdown(me: auth.Principal):
    artifact = artifact_id(await new_artifact('Notes', '# Notes\n\nSome text.\n', type='page'))
    assert (files_of(me, artifact) / 'artifact.toml').read_text() == 'title = "Notes"\ntype = "page"\ntheme = "light"\n'
    assert (files_of(me, artifact) / 'dist' / 'index.html').is_file()


async def test_new_artifact_without_build(me: auth.Principal):
    # Content that needs a component not yet written: with `build=False` creating it is not an error.
    content = '<component src="Card.html"></component>\n'
    out = await new_artifact('Later', content, build=False)
    artifact = artifact_id(out)
    assert out == f'artifact: {artifact}\npage (after `build`): http://127.0.0.1:8765/artifacts/{artifact}/\n'
    directory = files_of(me, artifact)
    assert (directory / 'main.md').read_text() == content
    assert not (directory / 'dist').exists()
    assert await head_sha(artifact) is not None
    with pytest.raises(ToolError, match='components directory not found'):
        await mcp_server.build_artifact(artifact)


async def test_new_artifact_rejects_old_slide_markers(me: auth.Principal):
    with pytest.raises(ToolError, match=r'main\.md:1: <slide \.\.\./> is no longer supported'):
        await new_artifact('Doc', '<slide/>\n# Doc\n', type='document')


async def test_new_artifact_reports_unknown_placeholders(me: auth.Principal):
    with pytest.raises(ToolError, match=r'main\.md:3: unknown placeholder \{\{ AUTHOR \}\}; built-ins and \[context\]'):
        await new_artifact('Doc', '# Doc\n\nBy {{ AUTHOR }}\n', type='document')


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
    assert await git_log(demo) == [f'run_code: {demo}', f'new_artifact: {demo}']


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
    assert (await git_log(demo))[0] == f'run_code (failed): {demo}'


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


# --- screenshot ----------------------------------------------------------------


async def test_screenshot_returns_the_page_as_an_image(me: auth.Principal, demo: str, monkeypatch: pytest.MonkeyPatch):
    asked: list[dict[str, Any]] = []
    stub = FastAPI()

    @stub.post('/screenshot/')
    async def take_screenshot(request: Request) -> Response:
        asked.append(await request.json())
        return Response(b'\x89PNG stub', media_type='image/png')

    monkeypatch.setenv('OPENARTIFACT_CHROME_URL', 'http://chrome:8766')
    monkeypatch.setattr(render, 'chrome_client', lambda: httpx2.AsyncClient(transport=httpx2.ASGITransport(app=stub)))
    image = await mcp_server.screenshot(demo, page=2)
    assert image.data == b'\x89PNG stub' and image._mime_type == 'image/png'  # pyright: ignore[reportPrivateUsage]
    # The page was built for Chrome to fetch, by print pass, with the page as the hash, at the deck's window size.
    assert (files_of(me, demo) / 'dist' / 'index.html').is_file()
    [sent] = asked
    assert re.fullmatch(rf'http://127.0.0.1:8765/print/[^/]+/artifacts/{demo}/#2', sent['url'])
    assert (sent['width'], sent['height']) == (1600, 900)
    with pytest.raises(ToolError, match='1-based'):
        await mcp_server.screenshot(demo, page=0)
    monkeypatch.delenv('OPENARTIFACT_CHROME_URL')
    with pytest.raises(ToolError, match='OPENARTIFACT_CHROME_URL'):
        await mcp_server.screenshot(demo)


# --- upload_url ----------------------------------------------------------------


async def test_upload_url_signs_one_url_per_file(me: auth.Principal, demo: str):
    urls = await mcp_server.upload_url(demo, [('assets/logo.png', 1234), ('components/A b.html', 0)])
    assert len(urls) == 2
    base = f'http://127.0.0.1:8765/artifacts/{demo}/'
    for url, (path, size) in zip(urls, [('assets/logo.png', 1234), ('components/A%20b.html', 0)], strict=True):
        prefix = f'{base}{path}?token='
        assert url.startswith(prefix), url
        upload.verify_token(url.removeprefix(prefix), uuid.UUID(demo), path.replace('%20', ' '), size)
    # Minting writes nothing: no new commit.
    assert await git_log(demo) == [f'new_artifact: {demo}']


async def test_upload_url_rejects_bad_requests(me: auth.Principal, demo: str):
    with pytest.raises(ToolError, match='files is empty'):
        await mcp_server.upload_url(demo, [])
    with pytest.raises(ToolError, match=r"'\.\./x\.png': empty, \. and \.\. segments"):
        await mcp_server.upload_url(demo, [('../x.png', 1)])
    with pytest.raises(ToolError, match=r"'dist/index\.html': dist/ is build output"):
        await mcp_server.upload_url(demo, [('dist/index.html', 1)])
    with pytest.raises(ToolError, match='over the 10,485,760 byte limit'):
        await mcp_server.upload_url(demo, [('big.png', upload.MAX_UPLOAD_SIZE + 1)])
    with pytest.raises(ToolError, match="'main.md' is listed twice"):
        await mcp_server.upload_url(demo, [('main.md', 1), ('main.md', 2)])
    with pytest.raises(ToolError, match='does not exist'):
        await mcp_server.upload_url(str(uuid.uuid4()), [('main.md', 1)])


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


NONE_YET = 'no artifacts of your own yet; create one with `new_personal_artifact` or `new_org_artifact`\n'


async def test_list_artifacts(me: auth.Principal):
    assert await mcp_server.list_artifacts() == NONE_YET
    first = artifact_id(await new_artifact('First', '# 1\n'))
    second = artifact_id(await mcp_server.new_personal_artifact('Second', '# 2\n', True, type='page'))
    assert await mcp_server.list_artifacts() == (
        f'{first}  deck  First  [private]  http://127.0.0.1:8765/artifacts/{first}/\n'
        f'{second}  page  Second  [public]  http://127.0.0.1:8765/artifacts/{second}/\n'
    )


async def test_other_users_cannot_see_my_artifacts(me: auth.Principal, demo: str, db_pool: db.Pool, pool: None):
    async with db_pool.acquire() as conn, conn.transaction():
        other = await auth.upsert_user(conn, sub='user-b', email='b@example.com', name=None, picture=None)
    with auth.as_principal(other):
        assert await mcp_server.list_artifacts() == NONE_YET
        with pytest.raises(ToolError, match='does not exist'):
            await mcp_server.build_artifact(demo)
        with pytest.raises(ToolError, match='does not exist'):
            await mcp_server.run_code(demo, 'pass')
        with pytest.raises(ToolError, match='does not exist'):
            await mcp_server.fork(demo)


@pytest.fixture
async def org_users(
    db_pool: db.Pool, storage: store.ObjectStore
) -> tuple[auth.Principal, auth.Principal, auth.Principal]:
    """Two members of one Workspace organisation and a user with none."""
    async with db_pool.acquire() as conn, conn.transaction():
        owner = await auth.upsert_user(conn, sub='o', email='o@x.test', name='Owner', picture=None, hd='x.test')
        colleague = await auth.upsert_user(conn, sub='c', email='c@x.test', name=None, picture=None, hd='x.test')
        outsider = await auth.upsert_user(conn, sub='s', email='s@gmail.test', name=None, picture=None)
    return owner, colleague, outsider


async def test_org_artifacts(org_users: tuple[auth.Principal, auth.Principal, auth.Principal], pool: None):
    owner, colleague, outsider = org_users
    with auth.as_principal(outsider), pytest.raises(ToolError, match='not in an organisation'):
        await mcp_server.new_org_artifact('Nope', '# n\n', False, False)
    with auth.as_principal(owner):
        visible = artifact_id(await mcp_server.new_org_artifact('Visible', '# v\n', False, False))
        editable = artifact_id(await mcp_server.new_org_artifact('Editable', '# e\n', True, False, type='page'))
        private = artifact_id(await new_artifact('Private', '# p\n'))
        listed = await mcp_server.list_artifacts()
        assert f'{visible}  deck  Visible  [visible to the organisation]' in listed
        assert f'{editable}  page  Editable  [visible to the organisation, editable by the organisation]' in listed
        assert 'shared with you' not in listed
    with auth.as_principal(colleague):
        listed = await mcp_server.list_artifacts()
        assert listed.startswith(NONE_YET)
        assert '\nshared with you by your organisation:\n' in listed
        assert f'{visible}  deck  Visible  [o@x.test]  ' in listed
        assert f'{editable}  page  Editable  [o@x.test, editable]  ' in listed
        assert private not in listed
        # Read-only: build works, editing does not, forking does.
        assert 'page:' in await mcp_server.build_artifact(visible)
        with pytest.raises(ToolError, match='read-only'):
            await mcp_server.run_code(visible, 'pass')
        with pytest.raises(ToolError, match='read-only'):
            await mcp_server.upload_url(visible, [('a.png', 1)])
        assert await mcp_server.run_code(editable, "print('ok')") == 'ok\n'
        with pytest.raises(ToolError, match='does not exist'):
            await mcp_server.build_artifact(private)
        with pytest.raises(ToolError, match='not yours'):
            await mcp_server.set_access(visible, public=True)
        forked = await mcp_server.fork(visible)
        copy = artifact_id(forked)
        assert f'forked from: {visible}\naccess: visible to the organisation\n' in forked
        assert (await mcp_server.run_code(copy, "print(open('main.md').read())")) == '# v\n\n'
        row = await workspace.get_artifact(uuid.UUID(copy))
        assert row is not None and row.forked_from == uuid.UUID(visible) and row.workspace_id == colleague.workspace_id
    with auth.as_principal(outsider), pytest.raises(ToolError, match='does not exist'):
        await mcp_server.build_artifact(visible)
    # The owner opens it to the world; the outsider can now read and fork (personal, since they have no org).
    with auth.as_principal(owner):
        out = await mcp_server.set_access(visible, public=True, org_editable=True)
        assert out == f'artifact: {visible}\naccess: public, editable by the organisation\n'
        with pytest.raises(ToolError, match='cannot be editable by an organisation'):
            await mcp_server.set_access(private, public=False, org_editable=True)
    with auth.as_principal(outsider):
        assert 'page:' in await mcp_server.build_artifact(visible)
        with pytest.raises(ToolError, match='read-only'):
            await mcp_server.run_code(visible, 'pass')
        forked = await mcp_server.fork(visible, public=True)
        assert 'access: public\n' in forked


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
        assert description is not None and description.startswith('Create a deck, document or page with OpenArtifact.')
        [content] = await client.read_resource(mcp_server.SKILL_URI)
        assert getattr(content, 'text', None) == skill_file.read_text(encoding='utf-8')
        # Supporting files are reachable through the template, and the manifest lists them.
        templates = [t.uri_template for t in await client.list_resource_templates()]
        assert 'skill://openartifact/{path*}' in templates
        [manifest] = await client.read_resource('skill://openartifact/_manifest')
        manifest_text = getattr(manifest, 'text', '')
        assert '"skill": "openartifact"' in manifest_text
        # The reference files SKILL.md points at are listed and served verbatim.
        for name in ('styles', 'steps', 'local'):
            assert f'references/{name}.md' in skill_file.read_text(encoding='utf-8')
            assert f'references/{name}.md' in manifest_text
            [reference] = await client.read_resource(f'skill://openartifact/references/{name}.md')
            expected = (skill_file.parent / 'references' / f'{name}.md').read_text(encoding='utf-8')
            assert getattr(reference, 'text', None) == expected
    assert mcp_server.mcp.instructions is not None and mcp_server.SKILL_URI in mcp_server.mcp.instructions


async def test_tools_over_mcp(me: auth.Principal, pool: None):
    async with Client(mcp_server.mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {
            'new_personal_artifact',
            'new_org_artifact',
            'run_code',
            'build',
            'screenshot',
            'upload_url',
            'set_access',
            'fork',
            'list_artifacts',
        }
        # The Google-style docstrings are split: the lead is the description, `Args:` entries describe parameters.
        description = tools['new_personal_artifact'].description or ''
        assert description.startswith('Create an artifact of your own from markdown and, by default, build it.')
        assert 'Args:' not in description
        schema = tools['new_personal_artifact'].input_schema
        properties = schema['properties']
        assert properties['build']['description'].startswith('Build straight away.')
        assert properties['type']['enum'] == list(mcp_server.TYPES)
        # Permissions are explicit: `public` has no default, so the schema requires it.
        assert set(schema['required']) == {'title', 'content', 'public'}
        assert set(tools['new_org_artifact'].input_schema['required']) == {'title', 'content', 'org_editable', 'public'}
        files = tools['upload_url'].input_schema['properties']['files']
        assert files['description'].startswith('`(path, size)` pairs')

        created = await client.call_tool(
            'new_personal_artifact', {'title': 'Demo', 'content': '# Demo\n', 'public': False}
        )
        name = artifact_id(created.data)

        # The (path, size) pairs arrive as JSON arrays and come back as a list of URLs.
        minted = await client.call_tool('upload_url', {'artifact': name, 'files': [['assets/a.png', 3]]})
        urls: list[str] = minted.data
        assert len(urls) == 1
        assert urls[0].startswith(f'http://127.0.0.1:8765/artifacts/{name}/assets/a.png?token=')

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
                'new_personal_artifact',
                {'title': 'T', 'content': '# T\n', 'public': False, **bad},
                raise_on_error=False,
            )
            assert rejected.is_error
        assert len(await workspace.list_artifacts(me.workspace_id)) == 1
