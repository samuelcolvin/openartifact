"""Artifacts: one git repository per artifact, cached as a working tree, stored as bundles.

An artifact's repository holds its files at the root: `artifact.toml`, `main.md`, `styles.css`, `components/`,
`assets/`. The server keeps a checkout of each artifact in this process's cache as two sibling directories,
`<cache dir>/<artifact id>/git` (the repository) and `<cache dir>/<artifact id>/tree` (the working tree), so the
tree holds nothing but the artifact's files and is what the sandbox mounts; build output under `dist/` is
excluded through the repository's `info/exclude`, not a file in the tree. At rest the repository is a `git bundle`
in the object store under an immutable key per head commit, `artifacts/<artifact id>/<sha>.bundle`, and
`artifacts.head_sha` in Postgres says which one is current.

Every change goes through `edit()`, which holds the artifact's row lock for the duration, syncs the checkout to
the current head, lets the caller write files, then commits, bundles, uploads and advances `head_sha` in the same
transaction. Readers use `open_artifact()`, which only syncs. Both take the per-artifact asyncio lock, so within
one process nothing touches a checkout concurrently; the row lock serialises processes. Artifacts in different
repositories never wait on each other.

A workspace is the owner of artifacts (a user now, an org later): a row, not a repository.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg
import logfire

import db
import store
from config import cache_dir

# Build output is never committed; the exclude lives in the repository so the tree carries no git files.
EXCLUDE = 'dist/\n'
GIT_TIMEOUT = 60.0
# A fixed environment so git ignores the host user's config and never prompts. Commits are authored by the bot;
# when users have identities on the repo (the GitHub backend) the author can become the user.
GIT_ENV = {
    'PATH': os.environ.get('PATH', ''),
    'HOME': os.environ.get('HOME', ''),
    'LC_ALL': 'C',
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_TERMINAL_PROMPT': '0',
    'GIT_AUTHOR_NAME': 'openartifact',
    'GIT_AUTHOR_EMAIL': 'bot@openartifact.local',
    'GIT_COMMITTER_NAME': 'openartifact',
    'GIT_COMMITTER_EMAIL': 'bot@openartifact.local',
}


class GitError(Exception):
    """A git command failed or timed out; the message carries the command and stderr."""


class ArtifactBusy(Exception):
    """Another process held the artifact's row lock for longer than `lock_timeout`."""


@dataclass(frozen=True)
class Artifact:
    """A row of the `artifacts` table."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    type: str
    # The commit whose bundle is current; None until the first edit commits.
    head_sha: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Artifact:
        return cls(
            id=row['id'],
            workspace_id=row['workspace_id'],
            title=row['title'],
            type=row['type'],
            head_sha=row['head_sha'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )


ARTIFACT_COLUMNS = 'id, workspace_id, title, type, head_sha, created_at, updated_at'


@dataclass(frozen=True)
class NewArtifact:
    """The row `edit(..., create=...)` inserts before the first commit."""

    workspace_id: uuid.UUID
    title: str
    type: str
    forked_from: uuid.UUID | None = None


def checkout_path(artifact_id: uuid.UUID) -> Path:
    """The working tree of an artifact in this process's cache: its files and nothing else."""
    return cache_dir() / str(artifact_id) / 'tree'


def git_dir(artifact_id: uuid.UUID) -> Path:
    """The artifact's repository, kept beside the tree rather than inside it."""
    return cache_dir() / str(artifact_id) / 'git'


def bundle_key(artifact_id: uuid.UUID, sha: str) -> str:
    """Object store key of the bundle for one commit; immutable, never overwritten."""
    return f'artifacts/{artifact_id}/{sha}.bundle'


# Locks are keyed by event loop as well as artifact: an asyncio.Lock binds to the loop that first waits on it,
# and tests run each test in a fresh loop.
_locks: dict[tuple[int, uuid.UUID], asyncio.Lock] = {}
# The head each checkout in this process is known to match, so an unchanged artifact skips the git calls.
_synced: dict[uuid.UUID, str] = {}


def artifact_lock(artifact_id: uuid.UUID) -> asyncio.Lock:
    """The in-process lock serialising every use of an artifact's checkout."""
    key = (id(asyncio.get_running_loop()), artifact_id)
    return _locks.setdefault(key, asyncio.Lock())


def reset_state() -> None:
    """Forget cached locks and sync state; for tests that swap the cache directory or database between runs."""
    _locks.clear()
    _synced.clear()


async def run_git(*args: str, cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run git in `cwd` and return `(exit code, stdout, stderr)`; raises `GitError` only on a timeout.

    `env` adds to the fixed `GIT_ENV`.
    """
    with logfire.span('git {argv}', argv=' '.join(args)) as span:
        proc = await asyncio.create_subprocess_exec(
            'git',
            *args,
            cwd=cwd,
            env={**GIT_ENV, **(env or {})},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT)
        except TimeoutError as exc:
            proc.kill()
            raise GitError(f'git {" ".join(args)} timed out after {GIT_TIMEOUT:g}s') from exc
        code = proc.returncode or 0
        span.set_attribute('exit_code', code)
        return code, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


async def git(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    """Run git in `cwd` and return stdout; a non-zero exit is a `GitError`."""
    code, out, err = await run_git(*args, cwd=cwd, env=env)
    if code != 0:
        raise GitError(f'git {" ".join(args)} failed with exit code {code}: {err.strip()}')
    return out


def repo_args(artifact_id: uuid.UUID) -> list[str]:
    """The options that point git at an artifact's repository and tree."""
    return ['--git-dir', str(git_dir(artifact_id)), '--work-tree', str(checkout_path(artifact_id))]


async def run_artifact_git(artifact_id: uuid.UUID, *args: str) -> tuple[int, str, str]:
    """`run_git` against an artifact's repository, from its tree."""
    return await run_git(*repo_args(artifact_id), *args, cwd=checkout_path(artifact_id))


async def artifact_git(artifact_id: uuid.UUID, *args: str) -> str:
    """`git` against an artifact's repository, from its tree."""
    return await git(*repo_args(artifact_id), *args, cwd=checkout_path(artifact_id))


async def init_checkout(artifact_id: uuid.UUID) -> Path:
    """A fresh, empty repository and tree for an artifact, replacing whatever the cache held."""
    base = cache_dir() / str(artifact_id)
    if base.exists():
        shutil.rmtree(base)
    tree = checkout_path(artifact_id)
    tree.mkdir(parents=True)
    repo = git_dir(artifact_id)
    await git('init', '-q', '-b', 'main', '--bare', str(repo), cwd=base)
    # A bare layout with the tree attached by `--work-tree`; `core.bare` must say so for tree operations to run.
    await artifact_git(artifact_id, 'config', 'core.bare', 'false')
    (repo / 'info' / 'exclude').write_text(EXCLUDE, encoding='utf-8')
    return tree


async def sync_checkout(artifact_id: uuid.UUID, head_sha: str | None) -> Path:
    """Make the cached checkout match `head_sha` exactly, fetching the bundle if the commit is not local.

    The caller holds `artifact_lock`. A checkout left dirty or ahead by a failed edit is repaired here, because
    the reset is unconditional. Build output under `dist/` is excluded from git and survives the reset, so it is
    deleted whenever the head changes; the server rebuilds on demand.
    """
    tree = checkout_path(artifact_id)
    if head_sha is None:
        # Nothing exists yet. Start clean even if an earlier failed first edit left a repository behind.
        _synced.pop(artifact_id, None)
        return await init_checkout(artifact_id)

    if _synced.get(artifact_id) == head_sha and git_dir(artifact_id).is_dir():
        return tree
    if not git_dir(artifact_id).is_dir():
        await init_checkout(artifact_id)
    tree.mkdir(parents=True, exist_ok=True)

    code, _, _ = await run_artifact_git(artifact_id, 'cat-file', '-e', f'{head_sha}^{{commit}}')
    if code != 0:
        # `git fetch <file>` reads a bundle without recording a remote, unlike `git clone <bundle>`.
        bundle = cache_dir() / f'{artifact_id}.{head_sha}.bundle'
        try:
            data = await store.store().get(bundle_key(artifact_id, head_sha))
        except store.StoreMissing as exc:
            raise GitError(
                f'artifact {artifact_id}: the bundle for head {head_sha} is missing from the object store; '
                'DATABASE_URL and OPENARTIFACT_STORE_URL do not describe the same deployment'
            ) from exc
        bundle.write_bytes(data)
        try:
            await artifact_git(artifact_id, 'fetch', '-q', str(bundle), 'main')
        finally:
            bundle.unlink(missing_ok=True)

    code, old, _ = await run_artifact_git(artifact_id, 'rev-parse', '--verify', '-q', 'HEAD')
    old_sha = old.strip() if code == 0 else None
    await artifact_git(artifact_id, 'reset', '-q', '--hard', head_sha)
    await artifact_git(artifact_id, 'clean', '-q', '-fd')
    if old_sha != head_sha:
        shutil.rmtree(tree / 'dist', ignore_errors=True)

    _synced[artifact_id] = head_sha
    return tree


async def bundle_and_upload(artifact_id: uuid.UUID, sha: str) -> str:
    """Serialise the repository at `main` into a bundle and store it under the key for `sha`; returns the key."""
    key = bundle_key(artifact_id, sha)
    tmp = cache_dir() / f'{artifact_id}.{sha}.bundle.tmp'
    await artifact_git(artifact_id, 'bundle', 'create', '-q', str(tmp), 'main')
    try:
        data = tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
    await store.store().put(key, data)
    return key


@dataclass
class Edit:
    """What a caller gets inside `edit()`: the row as it was when the lock was taken, where to write, and the
    transaction to write other rows in."""

    artifact: Artifact
    # The artifact's working tree: write files here.
    path: Path
    conn: db.Connection
    # The commit message; the caller may rewrite it before the block ends (e.g. to record a failed run).
    message: str


def strip_nested_git(directory: Path) -> list[str]:
    """Remove `.git*` entries an agent may have written into the tree; they would hide files from the commit."""
    removed: list[str] = []
    if not directory.is_dir():
        return removed
    for entry in directory.iterdir():
        if entry.name.startswith('.git'):
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            removed.append(entry.name)
    return removed


async def commit_edit(tx: Edit) -> str | None:
    """Stage everything, commit if anything changed, and return the new sha; `None` means nothing to commit."""
    strip_nested_git(tx.path)
    artifact_id = tx.artifact.id
    await artifact_git(artifact_id, 'add', '-A')
    code, _, _ = await run_artifact_git(artifact_id, 'diff', '--cached', '--quiet')
    if code == 0 and tx.artifact.head_sha is not None:
        return None
    await artifact_git(artifact_id, 'commit', '-q', '--allow-empty', '-m', tx.message)
    return (await artifact_git(artifact_id, 'rev-parse', 'HEAD')).strip()


@contextlib.asynccontextmanager
async def edit(artifact_id: uuid.UUID, message: str, *, create: NewArtifact | None = None) -> AsyncGenerator[Edit]:
    """Change an artifact: lock it, sync the checkout, yield, then commit, bundle, upload and advance the head.

    The artifact row is locked (`FOR UPDATE`) for the whole block, so two processes editing the same artifact run
    one after the other, and the second sees the first's commit. With `create`, the row is inserted first, inside
    the same transaction, and the block makes the first commit. If the block raises, or the upload fails, the
    transaction rolls back (the row too, when created here), `head_sha` is unchanged and the checkout is reset on
    its next use.
    """
    async with artifact_lock(artifact_id), db.pool().acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '60s'")
        if create is not None:
            row = await conn.fetchrow(
                'INSERT INTO artifacts (id, workspace_id, title, type, forked_from) VALUES ($1, $2, $3, $4, $5) '
                f'RETURNING {ARTIFACT_COLUMNS}',
                artifact_id,
                create.workspace_id,
                create.title,
                create.type,
                create.forked_from,
            )
        else:
            try:
                row = await conn.fetchrow(
                    f'SELECT {ARTIFACT_COLUMNS} FROM artifacts WHERE id = $1 FOR UPDATE', artifact_id
                )
            except asyncpg.LockNotAvailableError as exc:
                raise ArtifactBusy(f'artifact {artifact_id} is busy, try again') from exc
        if row is None:
            raise LookupError(f'artifact {artifact_id} does not exist')
        artifact = Artifact.from_row(row)
        try:
            path = await sync_checkout(artifact_id, artifact.head_sha)
            tx = Edit(artifact, path, conn, message)
            yield tx
            new_sha = await commit_edit(tx)
            if new_sha is None:
                return
            await bundle_and_upload(artifact_id, new_sha)
            await conn.execute(
                'UPDATE artifacts SET head_sha = $2, updated_at = now() WHERE id = $1', artifact_id, new_sha
            )
            _synced[artifact_id] = new_sha
        except BaseException:
            # The checkout may be dirty or one commit ahead of the head that survives the rollback.
            _synced.pop(artifact_id, None)
            raise


@contextlib.asynccontextmanager
async def open_artifact(artifact: Artifact) -> AsyncGenerator[Path]:
    """Read-only access to an artifact's tree, synced to the current head and locked for the block."""
    async with artifact_lock(artifact.id):
        head_sha: str | None = await db.pool().fetchval('SELECT head_sha FROM artifacts WHERE id = $1', artifact.id)
        yield await sync_checkout(artifact.id, head_sha)


async def clone_repository(artifact_id: uuid.UUID, dest: Path) -> None:
    """Clone the artifact's repository into `dest` as an ordinary working clone with no remote.

    `dist/` is excluded locally there too. Call it under the artifact lock with the checkout synced.
    """
    await git('clone', '-q', str(git_dir(artifact_id)), str(dest), cwd=dest.parent)
    await git('remote', 'remove', 'origin', cwd=dest)
    (dest / '.git' / 'info' / 'exclude').write_text(EXCLUDE, encoding='utf-8')


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


async def get_artifact(artifact_id: uuid.UUID) -> Artifact | None:
    """Look an artifact up by id alone; pages are public by id, so this is what the routes use."""
    row = await db.pool().fetchrow(f'SELECT {ARTIFACT_COLUMNS} FROM artifacts WHERE id = $1', artifact_id)
    return Artifact.from_row(row) if row else None


async def get_artifact_in(workspace_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact | None:
    """Look an artifact up within one workspace; what the tools use, so callers never see other workspaces."""
    row = await db.pool().fetchrow(
        f'SELECT {ARTIFACT_COLUMNS} FROM artifacts WHERE id = $1 AND workspace_id = $2', artifact_id, workspace_id
    )
    return Artifact.from_row(row) if row else None


async def list_artifacts(workspace_id: uuid.UUID) -> list[Artifact]:
    """Every artifact in a workspace, oldest first."""
    rows = await db.pool().fetch(
        f'SELECT {ARTIFACT_COLUMNS} FROM artifacts WHERE workspace_id = $1 ORDER BY created_at, id', workspace_id
    )
    return [Artifact.from_row(row) for row in rows]


async def import_directory(workspace_id: uuid.UUID, title: str, type: str, src: Path) -> Artifact:
    """Copy an existing artifact directory (minus `dist/`) into a workspace as a new artifact; one edit."""
    artifact_id = uuid.uuid4()
    create = NewArtifact(workspace_id=workspace_id, title=title, type=type)
    async with edit(artifact_id, f'import: {artifact_id}', create=create) as tx:
        shutil.copytree(src, tx.path, ignore=shutil.ignore_patterns('dist'), dirs_exist_ok=True)
    found = await get_artifact(artifact_id)
    assert found is not None
    return found
