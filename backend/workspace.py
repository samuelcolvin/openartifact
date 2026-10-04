"""Workspaces: one git repository per owner, cached as a working clone, stored as bundles.

A workspace repo holds one directory per artifact, `artifacts/<artifact uuid>/`, plus a root `.gitignore` that
keeps build output (`artifacts/*/dist/`) out of history. The server keeps a working clone of each workspace at
`<cache dir>/<workspace id>/`; the sandbox mounts an artifact's directory from there, exactly as a plain
directory. At rest the repo is a `git bundle` in the object store under an immutable key per head commit,
`workspaces/<workspace id>/<sha>.bundle`, and `workspaces.head_sha` in Postgres says which one is current.

Every change goes through `edit()`, which holds the workspace's row lock for the duration, syncs the checkout to
the current head, lets the caller write files and rows, then commits, bundles, uploads and advances `head_sha`
in the same transaction. Readers use `open_artifact()`, which only syncs. Both take the per-workspace asyncio
lock, so within one process nothing touches a checkout concurrently; the row lock serialises processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import asyncpg
import logfire
from config import cache_dir

import db
import store

ARTIFACTS_DIR = 'artifacts'
GITIGNORE = 'artifacts/*/dist/\n'
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


class WorkspaceBusy(Exception):
    """Another process held the workspace's row lock for longer than `lock_timeout`."""


@dataclass(frozen=True)
class Artifact:
    """A row of the `artifacts` table."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    type: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> Artifact:
        return cls(
            id=row['id'],
            workspace_id=row['workspace_id'],
            title=row['title'],
            type=row['type'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )


ARTIFACT_COLUMNS = 'id, workspace_id, title, type, created_at, updated_at'


def checkout_path(workspace_id: uuid.UUID) -> Path:
    """The working clone of a workspace in this process's cache."""
    return cache_dir() / str(workspace_id)


def bundle_key(workspace_id: uuid.UUID, sha: str) -> str:
    """Object store key of the bundle for one commit; immutable, never overwritten."""
    return f'workspaces/{workspace_id}/{sha}.bundle'


# Locks are keyed by event loop as well as workspace: an asyncio.Lock binds to the loop that first waits on it,
# and tests run each test in a fresh loop.
_locks: dict[tuple[int, uuid.UUID], asyncio.Lock] = {}
# The head each checkout in this process is known to match, so an unchanged workspace skips the git calls.
_synced: dict[uuid.UUID, str] = {}


def workspace_lock(workspace_id: uuid.UUID) -> asyncio.Lock:
    """The in-process lock serialising every use of a workspace's checkout."""
    key = (id(asyncio.get_running_loop()), workspace_id)
    return _locks.setdefault(key, asyncio.Lock())


def reset_state() -> None:
    """Forget cached locks and sync state; for tests that swap the cache directory or database between runs."""
    _locks.clear()
    _synced.clear()


async def run_git(*args: str, cwd: Path) -> tuple[int, str, str]:
    """Run git in `cwd` and return `(exit code, stdout, stderr)`; raises `GitError` only on a timeout."""
    with logfire.span('git {argv}', argv=' '.join(args)) as span:
        proc = await asyncio.create_subprocess_exec(
            'git', *args, cwd=cwd, env=GIT_ENV, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT)
        except TimeoutError as exc:
            proc.kill()
            raise GitError(f'git {" ".join(args)} timed out after {GIT_TIMEOUT:g}s') from exc
        code = proc.returncode or 0
        span.set_attribute('exit_code', code)
        return code, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


async def git(*args: str, cwd: Path) -> str:
    """Run git in `cwd` and return stdout; a non-zero exit is a `GitError`."""
    code, out, err = await run_git(*args, cwd=cwd)
    if code != 0:
        raise GitError(f'git {" ".join(args)} failed with exit code {code}: {err.strip()}')
    return out


async def sync_checkout(workspace_id: uuid.UUID, head_sha: str | None) -> Path:
    """Make the cached checkout match `head_sha` exactly, fetching the bundle if the commit is not local.

    The caller holds `workspace_lock`. A checkout left dirty or ahead by a failed edit is repaired here, because
    the reset is unconditional. Build output under `dist/` is ignored by git and survives the reset, so it is
    deleted for every artifact whose sources changed between the old and new head; the server rebuilds on demand.
    """
    path = checkout_path(workspace_id)
    if head_sha is None:
        # Nothing exists yet. Start clean even if an earlier failed first edit left a repo behind, and stage the
        # ignore file so the first commit carries it.
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        await git('init', '-q', '-b', 'main', cwd=path)
        (path / '.gitignore').write_text(GITIGNORE, encoding='utf-8')
        await git('add', '--', '.gitignore', cwd=path)
        _synced.pop(workspace_id, None)
        return path

    if _synced.get(workspace_id) == head_sha and (path / '.git').is_dir():
        return path
    path.mkdir(parents=True, exist_ok=True)
    if not (path / '.git').is_dir():
        await git('init', '-q', '-b', 'main', cwd=path)

    code, _, _ = await run_git('cat-file', '-e', f'{head_sha}^{{commit}}', cwd=path)
    if code != 0:
        # `git fetch <file>` reads a bundle without recording a remote, unlike `git clone <bundle>`.
        bundle = cache_dir() / f'{workspace_id}.{head_sha}.bundle'
        bundle.write_bytes(await store.store().get(bundle_key(workspace_id, head_sha)))
        try:
            await git('fetch', '-q', str(bundle), 'main', cwd=path)
        finally:
            bundle.unlink(missing_ok=True)

    code, old, _ = await run_git('rev-parse', '--verify', '-q', 'HEAD', cwd=path)
    old_sha = old.strip() if code == 0 else None
    await git('reset', '-q', '--hard', head_sha, cwd=path)
    await git('clean', '-q', '-fd', cwd=path)

    artifacts = path / ARTIFACTS_DIR
    if old_sha is None:
        stale = [p.name for p in artifacts.iterdir()] if artifacts.is_dir() else []
    elif old_sha != head_sha:
        changed = await git('diff', '--name-only', old_sha, head_sha, cwd=path)
        stale = sorted(
            {
                parts[1]
                for line in changed.splitlines()
                if (parts := line.split('/'))[0] == ARTIFACTS_DIR and len(parts) > 2
            }
        )
    else:
        stale = []
    for name in stale:
        shutil.rmtree(artifacts / name / 'dist', ignore_errors=True)

    _synced[workspace_id] = head_sha
    return path


async def bundle_and_upload(workspace_id: uuid.UUID, sha: str, cwd: Path) -> str:
    """Serialise the whole repo at `main` into a bundle and store it under the key for `sha`; returns the key."""
    key = bundle_key(workspace_id, sha)
    tmp = cache_dir() / f'{workspace_id}.{sha}.bundle.tmp'
    await git('bundle', 'create', '-q', str(tmp), 'main', cwd=cwd)
    try:
        data = tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
    await store.store().put(key, data)
    return key


@dataclass
class Edit:
    """What a caller gets inside `edit()`: where to write, and the transaction to write rows in."""

    workspace_id: uuid.UUID
    head_sha: str | None
    path: Path
    conn: db.Connection
    touched: set[uuid.UUID] = field(default_factory=set)

    def artifact_dir(self, artifact_id: uuid.UUID) -> Path:
        """The artifact's directory inside the checkout (not created); remembered for the commit."""
        self.touched.add(artifact_id)
        return self.path / ARTIFACTS_DIR / str(artifact_id)


def strip_nested_git(directory: Path) -> list[str]:
    """Remove `.git*` entries an agent may have written inside an artifact; they would hide files from the commit."""
    removed: list[str] = []
    if not directory.is_dir():
        return removed
    for entry in directory.iterdir():
        if entry.name.startswith('.git'):
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            removed.append(entry.name)
    return removed


async def commit_edit(tx: Edit, message: str) -> str | None:
    """Stage everything, commit if anything changed, and return the new sha; `None` means nothing to commit."""
    for artifact_id in tx.touched:
        strip_nested_git(tx.path / ARTIFACTS_DIR / str(artifact_id))
    await git('add', '-A', cwd=tx.path)
    code, _, _ = await run_git('diff', '--cached', '--quiet', cwd=tx.path)
    if code == 0 and tx.head_sha is not None:
        return None
    await git('commit', '-q', '-m', message, cwd=tx.path)
    return (await git('rev-parse', 'HEAD', cwd=tx.path)).strip()


@contextlib.asynccontextmanager
async def edit(workspace_id: uuid.UUID, message: str) -> AsyncGenerator[Edit]:
    """Change a workspace: lock it, sync the checkout, yield, then commit, bundle, upload and advance the head.

    The workspace row is locked (`FOR UPDATE`) for the whole block, so two processes editing the same workspace
    run one after the other, and the second sees the first's commit. If the block raises, or the upload fails,
    the transaction rolls back, `head_sha` is unchanged and the checkout is reset on its next use.
    """
    async with workspace_lock(workspace_id), db.pool().acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '60s'")
        try:
            row = await conn.fetchrow('SELECT head_sha FROM workspaces WHERE id = $1 FOR UPDATE', workspace_id)
        except asyncpg.LockNotAvailableError as exc:
            raise WorkspaceBusy(f'workspace {workspace_id} is busy, try again') from exc
        if row is None:
            raise LookupError(f'workspace {workspace_id} does not exist')
        head_sha: str | None = row['head_sha']
        try:
            path = await sync_checkout(workspace_id, head_sha)
            tx = Edit(workspace_id, head_sha, path, conn)
            yield tx
            new_sha = await commit_edit(tx, message)
            if new_sha is None:
                return
            await bundle_and_upload(workspace_id, new_sha, path)
            await conn.execute(
                'UPDATE workspaces SET head_sha = $2, updated_at = now() WHERE id = $1', workspace_id, new_sha
            )
            _synced[workspace_id] = new_sha
        except BaseException:
            # The checkout may be dirty or one commit ahead of the head that survives the rollback.
            _synced.pop(workspace_id, None)
            raise


@contextlib.asynccontextmanager
async def open_artifact(artifact: Artifact) -> AsyncGenerator[Path]:
    """Read-only access to an artifact's directory, synced to the current head and locked for the block."""
    async with workspace_lock(artifact.workspace_id):
        head_sha: str | None = await db.pool().fetchval(
            'SELECT head_sha FROM workspaces WHERE id = $1', artifact.workspace_id
        )
        path = await sync_checkout(artifact.workspace_id, head_sha)
        yield path / ARTIFACTS_DIR / str(artifact.id)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


async def insert_artifact(
    conn: db.Connection,
    *,
    artifact_id: uuid.UUID,
    workspace_id: uuid.UUID,
    title: str,
    type: str,
    forked_from: uuid.UUID | None = None,
) -> Artifact:
    """Insert the artifact row inside an `edit()` transaction."""
    row = await conn.fetchrow(
        'INSERT INTO artifacts (id, workspace_id, title, type, forked_from) VALUES ($1, $2, $3, $4, $5) '
        f'RETURNING {ARTIFACT_COLUMNS}',
        artifact_id,
        workspace_id,
        title,
        type,
        forked_from,
    )
    assert row is not None
    return Artifact.from_row(row)


async def touch_artifact(conn: db.Connection, artifact_id: uuid.UUID) -> None:
    """Record that an artifact's files changed."""
    await conn.execute('UPDATE artifacts SET updated_at = now() WHERE id = $1', artifact_id)


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
    async with edit(workspace_id, f'import: {artifact_id}') as tx:
        shutil.copytree(src, tx.artifact_dir(artifact_id), ignore=shutil.ignore_patterns('dist'))
        return await insert_artifact(
            tx.conn, artifact_id=artifact_id, workspace_id=workspace_id, title=title, type=type
        )
