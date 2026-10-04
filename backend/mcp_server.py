"""MCP server that lets an agent write an artifact's source files and build it.

Each artifact is a directory under the artifacts root (`OPENARTIFACT_ROOT`, default `artifacts/` in the repo)
holding the files `build.py` expects: `deck.md`, optional `artifact.toml`, `styles.css`, `components/` and
`assets/`. Three tools are exposed:

- `new_artifact` creates the directory with a server-chosen identifier, writes `artifact.toml` and `deck.md` from
  its arguments and builds once, so a simple deck is one call and a broken one fails immediately.
- `run_code` runs agent-written Python in a pydantic-monty sandbox with the artifact directory mounted
  read-write at `/artifact`, so the agent edits files with ordinary `pathlib` calls. Nothing else on the host is
  visible to the sandbox.
- `build` validates the files and runs the builder, writing `dist/index.html` inside the artifact directory.

Both tools are coroutines: the sandbox is `AsyncMonty`, whose worker I/O stays off the event loop, and the builder's
file work runs in a thread. The worker pool lives for the lifetime of the app; `server.py` opens it with
`monty_pool()` in its lifespan. `OPENARTIFACT_BASE_URL` is the public URL `server.py` reports artifacts under.
`build.py` stays importable on its own, without these dependencies.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import string
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Literal, get_args

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic_monty import (
    AsyncMonty,
    CollectStreams,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MountDir,
    ResourceLimits,
)

import build

ROOT = Path(__file__).resolve().parent.parent
# Where the sandbox sees the artifact directory. Relative paths in agent code resolve against it too, because
# monty's working directory defaults to the first mount's virtual path.
VIRTUAL_PATH = '/artifact'
ARTIFACT_NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
# Identifiers are `<slug of the title>-<suffix>`; the slug is capped so the whole thing fits ARTIFACT_NAME_RE.
SLUG_MAX_LEN = 40
SUFFIX_LEN = 6
SUFFIX_ALPHABET = string.ascii_lowercase + string.digits
Theme = Literal['light', 'dark', 'markdown-light', 'markdown-dark']
THEMES: tuple[str, ...] = get_args(Theme)
# Per-call sandbox limits: the agent is editing a handful of text files, nothing heavy.
LIMITS: ResourceLimits = {'max_feed_duration_secs': 30.0, 'max_memory': 256 * 1024 * 1024}

mcp = FastMCP(
    'openartifact',
    instructions=(
        'Create a slide deck with `new_artifact`, which writes `deck.md` from `content`, builds it and returns the '
        'identifier and page URL. Edit it with `run_code`, where the artifact directory is the working directory and is '
        f'also mounted at `{VIRTUAL_PATH}` (`deck.md`, `artifact.toml`, `styles.css`, `components/*.html`, '
        '`assets/*`), then call `build` to validate the files and refresh the page.'
    ),
)


def artifacts_root() -> Path:
    """The directory holding one sub-directory per artifact, from `OPENARTIFACT_ROOT` or `artifacts/` in the repo."""
    return Path(os.environ.get('OPENARTIFACT_ROOT') or ROOT / 'artifacts').resolve()


def base_url() -> str:
    """Public URL of `server.py`, from `OPENARTIFACT_BASE_URL`; defaults to uvicorn's local default."""
    return (os.environ.get('OPENARTIFACT_BASE_URL') or 'http://127.0.0.1:8000').rstrip('/')


def artifact_url(name: str, file: str = '') -> str:
    """Where `server.py` serves a built artifact's page, or a file (such as an image) from its directory."""
    return f'{base_url()}/artifacts/{name}/{file}'


def artifact_dir(name: str, create: bool) -> Path:
    """Resolve an artifact name to its directory, checking the name so it cannot escape the root."""
    if not ARTIFACT_NAME_RE.fullmatch(name):
        raise ToolError(
            f'invalid artifact name {name!r}: use 1-64 lowercase letters, digits, `-` or `_`, starting with a letter '
            'or digit'
        )
    path = artifacts_root() / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise ToolError(f'artifact {name!r} does not exist; create one with `new_artifact`')
    return path


def new_artifact_id(title: str) -> str:
    """A fresh identifier: the title slugified and capped, plus a random suffix so titles never collide."""
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:SLUG_MAX_LEN].rstrip('-') or 'artifact'
    suffix = ''.join(secrets.choice(SUFFIX_ALPHABET) for _ in range(SUFFIX_LEN))
    return f'{slug}-{suffix}'


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


async def new_artifact(title: str, content: str, theme: Theme = 'light', footer: str | None = None) -> str:
    """Create a slide deck artifact from markdown and build it.

    `content` becomes `deck.md`: slides separated by lines containing only `<slide .../>`, markdown in between.
    `title`, `theme` and `footer` are written to `artifact.toml`. The deck is built straight away, so a problem in
    `content` is returned as an error naming the line; the files are kept, so fix them with `run_code` and call
    `build`. On success returns the artifact identifier to pass to the other tools, and the URL of the page.
    """
    artifact = new_artifact_id(title)
    directory = artifact_dir(artifact, create=True)
    config = {'title': title, 'theme': theme}
    if footer is not None:
        config['footer'] = footer
    (directory / 'artifact.toml').write_text(render_toml(config), encoding='utf-8')
    (directory / 'deck.md').write_text(content, encoding='utf-8')
    built = await build_artifact(artifact)
    return f'artifact: {artifact}\n{built}'


async def run_code(artifact: str, code: str, inputs: dict[str, str | int] | None = None) -> str:
    """Run Python code in a sandbox to edit the files of an artifact created with `new_artifact`.

    The artifact directory is the working directory and is mounted read-write at `/artifact`; use `pathlib.Path`
    or `open()` to read and write files there. Nothing outside it is reachable
    and there is no network. `inputs` are bound as global variables before the code runs, which is the easiest way
    to pass large text without escaping it inside `code`. Returns everything printed plus the value of the final
    expression; a Python exception is returned as an error with its traceback.
    """
    directory = artifact_dir(artifact, create=False)
    streams = CollectStreams()
    # Closed explicitly rather than with `with`: `MountDir.__exit__` is typed as possibly swallowing exceptions, which
    # makes `result` look unbound to pyright after the block.
    mount = MountDir(host_path=directory, virtual_path=VIRTUAL_PATH, mode='read-write')
    try:
        async with pool().checkout(limits=LIMITS) as session:
            try:
                result = await session.feed_run(code, inputs=inputs or {}, print_callback=streams, mount=mount)
            except (MontyRuntimeError, MontySyntaxError) as exc:
                raise ToolError(format_output(streams, None) + exc.display()) from exc
            except MontyError as exc:
                raise ToolError(f'sandbox failed: {exc}') from exc
    finally:
        mount.close()
    return format_output(streams, result)


async def build_artifact(artifact: str) -> str:
    """Validate an artifact's files and build it to `dist/index.html`.

    Validation covers `artifact.toml`, the slide structure of `deck.md`, every referenced component and image.
    Problems are returned as an error naming the file and line, so fix them with `run_code` and build again. On
    success returns the URL where the page can be viewed; inside `run_code` the output is also visible under
    `/artifact/dist/`.
    """
    directory = artifact_dir(artifact, create=False)
    try:
        # Plain file reads and writes, kept off the event loop.
        html_path = await asyncio.to_thread(build.build_html, directory)
    except build.BuildError as exc:
        raise ToolError(f'error: {exc}') from exc
    return f'wrote {html_path} ({html_path.stat().st_size:,} bytes)\npage: {artifact_url(artifact)}\n'


mcp.tool(new_artifact)
mcp.tool(run_code)
mcp.tool(build_artifact, name='build')
