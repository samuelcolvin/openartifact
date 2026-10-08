"""MCP server that lets an agent create an artifact's source files, edit them in a sandbox and build the page.

Artifacts belong to the calling user's workspace (see `auth.py` and `workspace.py`). Each artifact is a git
repository, cached as a working tree holding the files `build.py` expects: `artifact.toml`, `main.md` (pages
separated by `---`), optional `styles.css`, `components/` and `assets/`. Every tool that changes files goes
through `workspace.edit`, so each call is a commit and the repository is pushed to the object store before the
tool returns. The server instructions an agent sees first are `instructions.md` next to this module.

Tools:

- `new_personal_artifact` and `new_org_artifact` create an artifact from markdown and build it once; the
  identifier they return is a UUID. The first is the caller's own (private or public); the second lives in the
  caller's organisation (visible to it, optionally editable by it, optionally public). `set_access` changes a
  caller's own artifact's permissions later; `fork` copies an artifact one can see, history included.
- `run_code` runs agent-written Python in a pydantic-monty sandbox with the artifact directory mounted
  read-write at `/artifact`. Nothing else on the host is visible to the sandbox.
- `build` validates the files and writes `dist/index.html`, which `server.py` serves.
- `upload_url` mints signed URLs (see `upload.py`) that the agent `PUT`s local files to, so images, fonts and
  large sources reach the artifact without passing through a tool call. `server.py` accepts the uploads.
- `list_artifacts` lists the caller's artifacts.

The authoring guide `skills/openartifact/SKILL.md`, with its `references/`, is served as an agent skill through
FastMCP's `SkillProvider`: the resource `skill://openartifact/SKILL.md`, a `_manifest` listing the skill's files
and a template serving the rest (`skill://openartifact/references/styles.md`).

The tools are coroutines: the sandbox is `AsyncMonty`, whose worker I/O stays off the event loop, and the
builder's file work runs in a thread. `server.py` opens the pools in its lifespan and serves the output.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import quote

import render
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.skills import SkillProvider
from fastmcp.utilities.types import Image
from pydantic_monty import (
    AsyncMonty,
    CollectStreams,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MountDir,
    ResourceLimits,
)

import access
import auth
import build
import upload
import workspace
from config import ROOT, base_url

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

# What the agent reads before it starts: `backend/instructions.md`, with the mount path and the skill URI filled in.
INSTRUCTIONS_PATH = Path(__file__).with_name('instructions.md')
instructions = INSTRUCTIONS_PATH.read_text()
instructions = instructions.replace('{VIRTUAL_PATH}', VIRTUAL_PATH).replace('{SKILL_URI}', SKILL_URI)

mcp = FastMCP('openartifact', instructions=instructions, auth=auth.make_auth_provider())


def artifact_url(artifact_id: uuid.UUID, file: str = '') -> str:
    """Where `server.py` serves a built artifact's page, or a file (such as an image) from its directory."""
    return f'{base_url()}/artifacts/{artifact_id}/{file}'


def parse_artifact_id(value: str) -> uuid.UUID:
    """The UUID an agent passed back, or a `ToolError` saying where identifiers come from."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ToolError(
            f'invalid artifact id {value!r}: pass the identifier returned when the artifact was created, or one '
            'from `list_artifacts`'
        ) from None


async def resolve(artifact: str, *, edit: bool = False) -> workspace.Artifact:
    """The artifact an agent named, if the caller may see it (and change it, with `edit`).

    An artifact the caller cannot see is reported as not existing, like a missing one; one they can see but not
    change says so and points at `fork`.
    """
    principal = await auth.current_principal()
    found = await workspace.get_artifact(parse_artifact_id(artifact))
    if found is None or not access.can_view(found, principal):
        raise ToolError(
            f'artifact {artifact} does not exist; create one with `new_personal_artifact` or `new_org_artifact`, '
            'or pick one from `list_artifacts`'
        )
    if edit and not access.can_edit(found, principal):
        raise ToolError(f'artifact {artifact} is shared with you read-only; `fork` it to get a copy you can edit')
    return found


@contextlib.asynccontextmanager
async def editing(
    artifact_id: uuid.UUID, message: str, *, create: workspace.NewArtifact | None = None
) -> AsyncGenerator[workspace.Edit]:
    """`workspace.edit` with its contention error turned into a `ToolError` the agent can act on."""
    try:
        async with workspace.edit(artifact_id, message, create=create) as tx:
            yield tx
    except workspace.ArtifactBusy as exc:
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


# The tool docstrings below are Google style because of how FastMCP reads them: the text before `Args:` is the tool
# description an agent sees, and each `Args:` entry becomes that parameter's description in the input schema.
# Nothing after `Args:` is shown, so what a tool returns is said in the lead text rather than in a `Returns:`.


def access_summary(found: workspace.Artifact) -> str:
    """One word or two describing who may see and edit an artifact, for tool output."""
    if found.organization_id is None:
        return found.visibility
    editable = ', editable by the organisation' if found.org_editable else ''
    return ('public' if found.visibility == 'public' else 'visible to the organisation') + editable


async def create_artifact(
    principal: auth.Principal,
    *,
    title: str,
    content: str,
    type: ArtifactType,
    theme: Theme,
    visibility: str,
    org_editable: bool,
    organization_id: uuid.UUID | None,
) -> uuid.UUID:
    """What the two creation tools and the web app share: write `artifact.toml` and `main.md` in one edit.

    Returns the new id; the caller builds (or not) and formats its answer. A bad combination of placement and
    permissions is a `ToolError` before anything is written.
    """
    problem = access.check_access(visibility, org_editable, organization_id)
    if problem is not None:
        raise ToolError(problem)
    artifact_id = uuid.uuid4()
    config = {'title': title, 'type': type, 'theme': theme}
    create = workspace.NewArtifact(
        workspace_id=principal.workspace_id,
        title=title,
        type=type,
        visibility=visibility,
        org_editable=org_editable,
        organization_id=organization_id,
    )
    async with editing(artifact_id, f'new_artifact: {artifact_id}', create=create) as tx:
        (tx.path / 'artifact.toml').write_text(render_toml(config), encoding='utf-8')
        (tx.path / 'main.md').write_text(content, encoding='utf-8')
    return artifact_id


async def created(artifact_id: uuid.UUID, build: bool) -> str:
    """A creation tool's answer: the id and page URL, after building unless told not to."""
    if not build:
        return f'artifact: {artifact_id}\npage (after `build`): {artifact_url(artifact_id)}\n'
    # Built after the edit has committed, so an artifact whose first build fails still exists to be fixed.
    built = await build_artifact(str(artifact_id))
    return f'artifact: {artifact_id}\n{built}'


async def new_personal_artifact(
    title: str,
    content: str,
    public: bool,
    type: ArtifactType = 'deck',
    theme: Theme = 'light',
    build: bool = True,
) -> str:
    """Create an artifact of your own from markdown and, by default, build it.

    A personal artifact is yours alone unless `public`. Use `new_org_artifact` for one your organisation should
    see. Returns the artifact identifier (a UUID) to pass to the other tools, and the URL of its page. A build
    problem is returned as an error naming the file and line; the files are kept, so fix them with `run_code` and
    call `build`.

    Args:
        title: The artifact's title, written to `artifact.toml`.
        content: The markdown for `main.md`. A line containing only `---`, with a blank line before it, starts a
            new page.
        public: Whether anyone with the link can see, download and fork it; otherwise only you can see it. Ask
            the user if they have not said.
        type: How the pages are laid out: `deck` shows one 16:9 page at a time (every page is a slide),
            `document` stacks fixed-width sheets that print one per A4 page, `page` is a continuous web page
            (usually a single page).
        theme: The colour theme, written to `artifact.toml`; `styles.css` can override its variables later.
        build: Build straight away. Pass false when `content` refers to components or images you have still to
            add with `upload_url` or `run_code`, then call `build` once they are in place.
    """
    principal = await auth.current_principal()
    artifact_id = await create_artifact(
        principal,
        title=title,
        content=content,
        type=type,
        theme=theme,
        visibility='public' if public else 'private',
        org_editable=False,
        organization_id=None,
    )
    return await created(artifact_id, build)


async def new_org_artifact(
    title: str,
    content: str,
    org_editable: bool,
    public: bool,
    type: ArtifactType = 'deck',
    theme: Theme = 'light',
    build: bool = True,
) -> str:
    """Create an artifact in your organisation from markdown and, by default, build it.

    Everyone in your organisation (your Google Workspace domain) can see, download and fork it; `org_editable`
    lets them edit it too, and `public` opens it to anyone with the link as well. You remain its owner. Fails for
    an account that is not in an organisation; use `new_personal_artifact` then. Returns the artifact identifier
    (a UUID) to pass to the other tools, and the URL of its page. A build problem is returned as an error naming
    the file and line; the files are kept, so fix them with `run_code` and call `build`.

    Args:
        title: The artifact's title, written to `artifact.toml`.
        content: The markdown for `main.md`. A line containing only `---`, with a blank line before it, starts a
            new page.
        org_editable: Whether everyone in the organisation may edit it, not only see it. Ask the user if they
            have not said.
        public: Whether anyone with the link can see, download and fork it too. Ask the user if they have not
            said.
        type: How the pages are laid out: `deck` shows one 16:9 page at a time (every page is a slide),
            `document` stacks fixed-width sheets that print one per A4 page, `page` is a continuous web page
            (usually a single page).
        theme: The colour theme, written to `artifact.toml`; `styles.css` can override its variables later.
        build: Build straight away. Pass false when `content` refers to components or images you have still to
            add with `upload_url` or `run_code`, then call `build` once they are in place.
    """
    principal = await auth.current_principal()
    if principal.organization_id is None:
        raise ToolError(
            'your account is not in an organisation: sign in with a Google Workspace account to share with one, '
            'or create a personal artifact with `new_personal_artifact`'
        )
    artifact_id = await create_artifact(
        principal,
        title=title,
        content=content,
        type=type,
        theme=theme,
        visibility='public' if public else 'org',
        org_editable=org_editable,
        organization_id=principal.organization_id,
    )
    return await created(artifact_id, build)


async def set_access(artifact: str, public: bool, org_editable: bool = False) -> str:
    """Change who may see and edit one of your own artifacts.

    A personal artifact is private unless `public`, and can never be editable by an organisation. An
    organisation's artifact stays visible to the organisation, is open to anyone with the link when `public`, and
    editable by the organisation when `org_editable`. Only the owner can do this.

    Args:
        artifact: The identifier of an artifact you own.
        public: Whether anyone with the link can see, download and fork it.
        org_editable: Whether everyone in its organisation may edit it (organisation artifacts only).
    """
    found = await resolve(artifact)
    principal = await auth.current_principal()
    if not access.can_manage(found, principal):
        raise ToolError(f'artifact {artifact} is not yours; only its owner can change who may see it')
    if found.organization_id is None:
        visibility = 'public' if public else 'private'
    else:
        visibility = 'public' if public else 'org'
    problem = access.check_access(visibility, org_editable, found.organization_id)
    if problem is not None:
        raise ToolError(problem)
    changed = await workspace.set_access(found.id, visibility=visibility, org_editable=org_editable)
    return f'artifact: {changed.id}\naccess: {access_summary(changed)}\n'


async def fork(artifact: str, org_editable: bool = False, public: bool = False) -> str:
    """Copy an artifact you can see into a new one of your own, history included.

    The copy lives in your organisation when you have one (visible to it; `org_editable` and `public` as for
    `new_org_artifact`), otherwise it is a personal artifact (private unless `public`). Returns the new identifier
    and page URL; the original is untouched.

    Args:
        artifact: The identifier of the artifact to copy.
        org_editable: Whether everyone in your organisation may edit the copy (ignored without an organisation).
        public: Whether anyone with the link can see the copy.
    """
    found = await resolve(artifact)
    principal = await auth.current_principal()
    organization_id = principal.organization_id
    if organization_id is None:
        visibility, org_editable = ('public' if public else 'private'), False
    else:
        visibility = 'public' if public else 'org'
    created = await workspace.fork_artifact(
        found, principal.workspace_id, organization_id=organization_id, visibility=visibility, org_editable=org_editable
    )
    return (
        f'artifact: {created.id}\nforked from: {found.id}\naccess: {access_summary(created)}\n'
        f'page: {artifact_url(created.id)}\n'
    )


async def run_code(artifact: str, code: str, inputs: dict[str, Any] | None = None) -> str:
    """Run Python in a sandbox to read and edit the files of an artifact.

    The artifact directory is the working directory and is also mounted at `/artifact`; use `pathlib.Path` or
    `open()` to read and write files there. Nothing outside it is reachable and there is no network. Returns
    everything printed plus the value of the final expression. A Python exception is returned as an error with its
    traceback; files written before it are kept.

    Args:
        artifact: The identifier returned when the artifact was created, or one from `list_artifacts`.
        code: The Python source to run.
        inputs: Values bound as global variables before the code runs. The easiest way to pass large text: put
            it here rather than escaping it inside `code`.
    """
    found = await resolve(artifact, edit=True)
    streams = CollectStreams()
    failure: ToolError | None = None
    async with editing(found.id, f'run_code: {found.id}') as tx:
        # Closed explicitly rather than with `with`: `MountDir.__exit__` is typed as possibly swallowing exceptions,
        # which makes `result` look unbound to pyright after the block.
        mount = MountDir(host_path=tx.path, virtual_path=VIRTUAL_PATH, mode='read-write')
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
    if failure is not None:
        raise failure
    return format_output(streams, result)


async def build_artifact(artifact: str) -> str:
    """Validate an artifact's files and build its page.

    Validation covers `artifact.toml`, the page structure of `main.md`, every component and its parameters, and
    every image. Problems are returned as an error naming the file and line, so fix them with `run_code` and build
    again. On success returns the URL of the page; inside `run_code` the output is also visible under
    `/artifact/dist/`.

    Args:
        artifact: The identifier returned when the artifact was created, or one from `list_artifacts`.
    """
    found = await resolve(artifact)
    async with workspace.open_artifact(found) as directory:
        try:
            # Plain file reads and writes, kept off the event loop.
            html_path = await asyncio.to_thread(build.build_html, directory, markdown_url=f'../{found.id}.md')
        except build.BuildError as exc:
            raise ToolError(f'error: {exc}') from exc
        size = html_path.stat().st_size
    return f'wrote dist/index.html ({size:,} bytes)\npage: {artifact_url(found.id)}\n'


async def screenshot(artifact: str, page: int = 1) -> Image:
    """See one page of an artifact as a viewer sees it: a PNG of the built page, 1-based.

    For a deck this is the slide itself at 16:9; for a document or page artifact the window scrolled to that
    page. Use it to check layout after a build, or when the user talks about how something looks. The page is
    built first if needed, so a broken artifact fails as a build error.

    Args:
        artifact: The identifier returned when the artifact was created, or one from `list_artifacts`.
        page: Which page, counting from 1.
    """
    if page < 1:
        raise ToolError('page is 1-based')
    found = await resolve(artifact)
    try:
        data = await render.screenshot(found, page)
    except build.BuildError as exc:
        raise ToolError(f'error: {exc}') from exc
    except render.RenderError as exc:
        raise ToolError(str(exc)) from exc
    return Image(data=data, format='png')


async def upload_url(artifact: str, files: list[tuple[str, int]]) -> list[str]:
    """Get URLs to upload local files into an artifact, one per file, so their bytes never pass through a tool call.

    Returns one URL per entry of `files`, in the same order. `PUT` each file's bytes to its URL, for example
    `curl -T assets/logo.png "<url>"` (quote the URL: it has a query string). The body must be exactly the declared
    size. Each upload is committed on its own and answered with JSON holding the file's `sha256`, so compare it with
    `shasum -a 256` locally. URLs expire after an hour and allow up to 10 MB per file. A file already at the path is
    overwritten; `dist/` and `.git` entries are refused.

    Args:
        artifact: The identifier returned when the artifact was created, or one from `list_artifacts`.
        files: `(path, size)` pairs: the path the file will have inside the artifact, relative, such as
            `assets/logo.png` or `components/Card.html`, and its exact size in bytes.
    """
    found = await resolve(artifact, edit=True)
    if not files:
        raise ToolError('files is empty: pass at least one (path, size) pair')
    expires = int(time.time()) + upload.TOKEN_TTL
    urls: list[str] = []
    seen: set[str] = set()
    for path, size in files:
        try:
            upload.validate_path(path)
            upload.validate_size(path, size)
        except upload.UploadError as exc:
            raise ToolError(str(exc)) from exc
        if path in seen:
            raise ToolError(f'{path!r} is listed twice')
        seen.add(path)
        token = upload.make_token(found.id, path, size, expires)
        urls.append(f'{artifact_url(found.id, quote(path))}?token={token}')
    return urls


async def list_artifacts() -> str:
    """List your artifacts, oldest first, one line each with id, type, title, who may see it and the page URL;
    then the ones others in your organisation share with you, with their owner's email."""
    principal = await auth.current_principal()
    mine = await workspace.list_artifacts(principal.workspace_id)
    shared = await workspace.list_shared_artifacts(principal.org_ids, principal.workspace_id)
    lines = [f'{a.id}  {a.type}  {a.title}  [{access_summary(a)}]  {artifact_url(a.id)}\n' for a in mine]
    if not lines:
        lines.append('no artifacts of your own yet; create one with `new_personal_artifact` or `new_org_artifact`\n')
    if shared:
        lines.append('\nshared with you by your organisation:\n')
        for a, owner in shared:
            editable = ', editable' if a.org_editable else ''
            lines.append(f'{a.id}  {a.type}  {a.title}  [{owner or "unknown"}{editable}]  {artifact_url(a.id)}\n')
    return ''.join(lines)


mcp.tool(new_personal_artifact)
mcp.tool(new_org_artifact)
mcp.tool(run_code)
mcp.tool(build_artifact, name='build')
mcp.tool(screenshot)
mcp.tool(upload_url)
mcp.tool(set_access)
mcp.tool(fork)
mcp.tool(list_artifacts)
mcp.add_provider(SkillProvider(SKILL_DIR))
