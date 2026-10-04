"""MCP server that lets an agent create an artifact's source files, edit them in a sandbox and build the page.

Artifacts belong to the calling user's workspace (see `auth.py` and `workspace.py`): a git repository, cached as
a working clone, with one directory per artifact holding the files `build.py` expects: `artifact.toml`,
`main.md`, optional `styles.css`, `components/` and `assets/`. Every tool that changes files goes through
`workspace.edit`, so each call is a commit and the repo is pushed to the object store before the tool returns.

Tools:

- `new_artifact` creates an artifact from markdown and builds it once; the identifier it returns is a UUID.
- `run_code` runs agent-written Python in a pydantic-monty sandbox with the artifact directory mounted
  read-write at `/artifact`. Nothing else on the host is visible to the sandbox.
- `build` validates the files and writes `dist/index.html`, which `server.py` serves.
- `list_artifacts` lists the caller's artifacts.

The authoring guide `skills/openartifact/SKILL.md` is served as an agent skill through FastMCP's `SkillProvider`:
the resource `skill://openartifact/SKILL.md`, a `_manifest` listing the skill's files and a template for the rest.

The tools are coroutines: the sandbox is `AsyncMonty`, whose worker I/O stays off the event loop, and the
builder's file work runs in a thread. `server.py` opens the pools in its lifespan and serves the output.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Literal, get_args

from config import ROOT, base_url
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.skills import SkillProvider
from pydantic_monty import (
    AsyncMonty,
    CollectStreams,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MountDir,
    ResourceLimits,
)

import auth
import build
import workspace

# Where the sandbox sees the artifact directory. Relative paths in agent code resolve against it too, because
# monty's working directory defaults to the first mount's virtual path.
VIRTUAL_PATH = '/artifact'
# Per-call sandbox limits: the agent is editing a handful of text files, nothing heavy.
LIMITS: ResourceLimits = {'max_feed_duration_secs': 30.0, 'max_memory': 256 * 1024 * 1024}
ArtifactType = Literal['deck', 'document', 'page']
TYPES: tuple[str, ...] = get_args(ArtifactType)
Theme = Literal['light', 'dark', 'markdown-light', 'markdown-dark']
THEMES: tuple[str, ...] = get_args(Theme)
# The authoring guide, exposed as an MCP skill; its directory name is the skill name.
SKILL_DIR = ROOT / 'skills' / 'openartifact'
SKILL_URI = f'skill://{SKILL_DIR.name}/SKILL.md'

mcp = FastMCP(
    'openartifact',
    instructions=(
        'Create an artifact with `new_artifact`, which writes `main.md` from `content`, builds it and returns its '
        'identifier (a UUID) and page URL. Pick the `type` for the job: `deck` for slides (content is markdown '
        'with a `<slide .../>` line starting each slide), `document` for a fixed-width document that prints to '
        'pages, or `page` for a continuous web page (both take plain markdown with no slide markers). Edit it with '
        f'`run_code`, where the artifact directory is the working directory and is also mounted at `{VIRTUAL_PATH}` '
        '(`main.md`, `artifact.toml`, `styles.css`, `components/*.html`, `assets/*`), then call `build` to validate '
        'the files and refresh the page. `list_artifacts` shows the artifacts you already have. Every change is '
        "committed to the artifact's history. The full authoring guide (slide attributes, components, build steps, "
        f'images, the CSS variable contract, mapping a brand palette) is the resource `{SKILL_URI}`; read it before '
        'writing anything beyond plain markdown.'
    ),
    auth=auth.make_auth_provider(),
)


def artifact_url(artifact_id: uuid.UUID, file: str = '') -> str:
    """Where `server.py` serves a built artifact's page, or a file (such as an image) from its directory."""
    return f'{base_url()}/artifacts/{artifact_id}/{file}'


def parse_artifact_id(value: str) -> uuid.UUID:
    """The UUID an agent passed back, or a `ToolError` saying where identifiers come from."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ToolError(
            f'invalid artifact id {value!r}: pass the identifier returned by `new_artifact` or `list_artifacts`'
        ) from None


async def resolve(artifact: str) -> workspace.Artifact:
    """The artifact an agent named, if it exists in the caller's workspace."""
    principal = await auth.current_principal()
    found = await workspace.get_artifact_in(principal.workspace_id, parse_artifact_id(artifact))
    if found is None:
        raise ToolError(
            f'artifact {artifact} does not exist; create one with `new_artifact` or pick one from `list_artifacts`'
        )
    return found


@contextlib.asynccontextmanager
async def editing(workspace_id: uuid.UUID, message: str) -> AsyncGenerator[workspace.Edit]:
    """`workspace.edit` with its contention error turned into a `ToolError` the agent can act on."""
    try:
        async with workspace.edit(workspace_id, message) as tx:
            yield tx
    except workspace.WorkspaceBusy as exc:
        raise ToolError(str(exc)) from exc


def render_toml(values: dict[str, str]) -> str:
    """Serialise flat string values as TOML; the stdlib only reads TOML, but JSON string escaping is valid TOML."""
    return ''.join(f'{key} = {json.dumps(value, ensure_ascii=False)}\n' for key, value in values.items())


_pool: AsyncMonty | None = None


@contextlib.asynccontextmanager
async def monty_pool() -> AsyncGenerator[AsyncMonty]:
    """Run the shared monty worker pool for the duration of the block; `run_code` needs it open.

    The pool is bound to the event loop that enters it, so this belongs in the ASGI lifespan (see `server.py`).
    """
    global _pool
    async with AsyncMonty() as pool:
        _pool = pool
        try:
            yield pool
        finally:
            _pool = None


def pool() -> AsyncMonty:
    """The pool opened by `monty_pool()`."""
    if _pool is None:
        raise RuntimeError('the monty pool is not running: enter `monty_pool()` first')
    return _pool


def format_output(streams: CollectStreams, result: object) -> str:
    """Combine the sandbox's printed output with the value of its trailing expression, if any."""
    parts = [text for _stream, text in streams.output]
    if result is not None:
        parts.append(f'{result!r}\n')
    return ''.join(parts)


async def new_artifact(title: str, content: str, type: ArtifactType = 'deck', theme: Theme = 'light') -> str:
    """Create an artifact from markdown and build it.

    `type` is the form of the artifact: `deck` is slides, where `content` has a line containing only
    `<slide .../>` starting each slide; `document` is a fixed-width document that prints to A4 pages; `page` is a
    continuous web page. For `document` and `page`, `content` is plain markdown with no slide markers, structured
    with headings. `content` becomes `main.md`; `title`, `type` and `theme` are written to `artifact.toml`. The artifact is built straight away, so a problem in `content` is returned as an error naming
    the line; the files are kept, so fix them with `run_code` and call `build`. On success returns the artifact
    identifier (a UUID) to pass to the other tools, and the URL of the page.
    """
    principal = await auth.current_principal()
    artifact_id = uuid.uuid4()
    config = {'title': title, 'type': type, 'theme': theme}
    async with editing(principal.workspace_id, f'new_artifact: {artifact_id}') as tx:
        directory = tx.artifact_dir(artifact_id)
        directory.mkdir(parents=True)
        (directory / 'artifact.toml').write_text(render_toml(config), encoding='utf-8')
        (directory / 'main.md').write_text(content, encoding='utf-8')
        await workspace.insert_artifact(
            tx.conn, artifact_id=artifact_id, workspace_id=principal.workspace_id, title=title, type=type
        )
    # Built after the edit has committed, so an artifact whose first build fails still exists to be fixed.
    built = await build_artifact(str(artifact_id))
    return f'artifact: {artifact_id}\n{built}'


async def run_code(artifact: str, code: str, inputs: dict[str, str | int] | None = None) -> str:
    """Run Python code in a sandbox to edit the files of an artifact created with `new_artifact`.

    The artifact directory is the working directory and is mounted read-write at `/artifact`; use `pathlib.Path`
    or `open()` to read and write files there. Nothing outside it is reachable and there is no network. `inputs`
    are bound as global variables before the code runs, which is the easiest way to pass large text without
    escaping it inside `code`. Returns everything printed plus the value of the final expression; a Python
    exception is returned as an error with its traceback. Files written before the exception are kept.
    """
    found = await resolve(artifact)
    streams = CollectStreams()
    failure: ToolError | None = None
    async with editing(found.workspace_id, f'run_code: {found.id}') as tx:
        directory = tx.artifact_dir(found.id)
        # Closed explicitly rather than with `with`: `MountDir.__exit__` is typed as possibly swallowing exceptions,
        # which makes `result` look unbound to pyright after the block.
        mount = MountDir(host_path=directory, virtual_path=VIRTUAL_PATH, mode='read-write')
        result: object = None
        try:
            async with pool().checkout(limits=LIMITS) as session:
                try:
                    result = await session.feed_run(code, inputs=inputs or {}, print_callback=streams, mount=mount)
                except (MontyRuntimeError, MontySyntaxError) as exc:
                    failure = ToolError(format_output(streams, None) + exc.display())
                except MontyError as exc:
                    failure = ToolError(f'sandbox failed: {exc}')
        finally:
            mount.close()
        if failure is not None:
            # Whatever the code wrote before failing is committed too, so the agent can inspect and fix it.
            tx.message = f'run_code (failed): {found.id}'
        await workspace.touch_artifact(tx.conn, found.id)
    if failure is not None:
        raise failure
    return format_output(streams, result)


async def build_artifact(artifact: str) -> str:
    """Validate an artifact's files and build it to `dist/index.html`.

    Validation covers `artifact.toml`, the slide structure of `main.md`, every referenced component and image.
    Problems are returned as an error naming the file and line, so fix them with `run_code` and build again. On
    success returns the URL where the page can be viewed; inside `run_code` the output is also visible under
    `/artifact/dist/`.
    """
    found = await resolve(artifact)
    async with workspace.open_artifact(found) as directory:
        try:
            # Plain file reads and writes, kept off the event loop.
            html_path = await asyncio.to_thread(build.build_html, directory)
        except build.BuildError as exc:
            raise ToolError(f'error: {exc}') from exc
        size = html_path.stat().st_size
    return f'wrote dist/index.html ({size:,} bytes)\npage: {artifact_url(found.id)}\n'


async def list_artifacts() -> str:
    """List the caller's artifacts, oldest first: one line per artifact with its id, type, title and page URL."""
    principal = await auth.current_principal()
    found = await workspace.list_artifacts(principal.workspace_id)
    if not found:
        return 'no artifacts yet; create one with `new_artifact`\n'
    return ''.join(f'{a.id}  {a.type}  {a.title}  {artifact_url(a.id)}\n' for a in found)


mcp.tool(new_artifact)
mcp.tool(run_code)
mcp.tool(build_artifact, name='build')
mcp.tool(list_artifacts)
mcp.add_provider(SkillProvider(SKILL_DIR))
