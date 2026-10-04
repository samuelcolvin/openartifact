"""Tests for `backend/workspace.py`: the git sequences, the edit protocol and its failure paths."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import auth
import db
import store
import workspace

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / 'examples' / 'starter'


async def head_sha(ws: uuid.UUID) -> str | None:
    return await db.pool().fetchval('SELECT head_sha FROM workspaces WHERE id = $1', ws)


async def git_log(path: Path) -> list[str]:
    return (await workspace.git('log', '--format=%s', cwd=path)).splitlines()


async def write_artifact(ws: uuid.UUID, title: str = 'Demo', body: str = '# hi\n') -> workspace.Artifact:
    """One edit creating an artifact with a single file."""
    artifact_id = uuid.uuid4()
    async with workspace.edit(ws, f'new_artifact: {artifact_id}') as tx:
        directory = tx.artifact_dir(artifact_id)
        directory.mkdir(parents=True)
        (directory / 'main.md').write_text(body)
        return await workspace.insert_artifact(
            tx.conn, artifact_id=artifact_id, workspace_id=ws, title=title, type='page'
        )


async def test_first_edit_creates_repo_bundle_and_head(principal: auth.Principal, storage: store.ObjectStore):
    ws = principal.workspace_id
    assert await head_sha(ws) is None
    artifact = await write_artifact(ws)
    sha = await head_sha(ws)
    assert sha is not None
    path = workspace.checkout_path(ws)
    assert (await workspace.git('rev-parse', 'HEAD', cwd=path)).strip() == sha
    assert await git_log(path) == [f'new_artifact: {artifact.id}']
    assert (path / '.gitignore').read_text() == 'artifacts/*/dist/\n'
    assert (await workspace.git('status', '--porcelain', cwd=path)) == ''
    assert await storage.exists(workspace.bundle_key(ws, sha))
    assert await workspace.get_artifact(artifact.id) == artifact
    assert await workspace.list_artifacts(ws) == [artifact]


async def test_dist_is_ignored(principal: auth.Principal):
    ws = principal.workspace_id
    artifact = await write_artifact(ws)
    async with workspace.edit(ws, 'noise') as tx:
        dist = tx.artifact_dir(artifact.id) / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('<html>')
    assert await git_log(workspace.checkout_path(ws)) == [f'new_artifact: {artifact.id}']
    assert (workspace.checkout_path(ws) / 'artifacts' / str(artifact.id) / 'dist' / 'index.html').exists()


async def test_edit_without_changes_is_a_noop(principal: auth.Principal, storage: store.ObjectStore):
    ws = principal.workspace_id
    await write_artifact(ws)
    before = await head_sha(ws)
    async with workspace.edit(ws, 'nothing'):
        pass
    assert await head_sha(ws) == before
    assert await storage.list(f'workspaces/{ws}/') == [workspace.bundle_key(ws, before or '')]


async def test_body_error_rolls_back_and_resets_checkout(principal: auth.Principal):
    ws = principal.workspace_id
    artifact = await write_artifact(ws)
    before = await head_sha(ws)
    with pytest.raises(RuntimeError, match='boom'):
        async with workspace.edit(ws, 'bad') as tx:
            (tx.artifact_dir(artifact.id) / 'main.md').write_text('changed')
            await workspace.touch_artifact(tx.conn, artifact.id)
            raise RuntimeError('boom')
    assert await head_sha(ws) == before
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').read_text() == '# hi\n'
    assert (await workspace.get_artifact(artifact.id)) == artifact  # updated_at untouched: the row write rolled back


async def test_upload_failure_rolls_back(principal: auth.Principal, monkeypatch: pytest.MonkeyPatch):
    ws = principal.workspace_id
    artifact = await write_artifact(ws)
    before = await head_sha(ws)

    async def failing_put(self: store.ObjectStore, key: str, data: bytes) -> None:
        raise OSError('store down')

    monkeypatch.setattr(store.ObjectStore, 'put', failing_put)
    with pytest.raises(OSError, match='store down'):
        async with workspace.edit(ws, 'run_code') as tx:
            (tx.artifact_dir(artifact.id) / 'main.md').write_text('changed')
    monkeypatch.undo()
    assert await head_sha(ws) == before
    # The local commit that was never published is discarded on the next sync.
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').read_text() == '# hi\n'
    assert await git_log(workspace.checkout_path(ws)) == [f'new_artifact: {artifact.id}']


async def test_second_cache_syncs_from_bundle(
    principal: auth.Principal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ws = principal.workspace_id
    artifact = await write_artifact(ws, body='# from A\n')
    async with workspace.edit(ws, 'run_code') as tx:
        (tx.artifact_dir(artifact.id) / 'extra.txt').write_text('second commit')
    # Another process: an empty cache directory and no memory of what is synced.
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache-b'))
    workspace.reset_state()
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').read_text() == '# from A\n'
        assert (directory / 'extra.txt').read_text() == 'second commit'
    path_b = workspace.checkout_path(ws)
    assert len(await git_log(path_b)) == 2
    assert 'origin' not in await workspace.git('remote', cwd=path_b)
    # And it can continue the history.
    async with workspace.edit(ws, 'run_code') as tx:
        (tx.artifact_dir(artifact.id) / 'third.txt').write_text('3')
    assert len(await git_log(path_b)) == 3


async def test_sync_removes_stale_dist_for_changed_artifacts(
    principal: auth.Principal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ws = principal.workspace_id
    changed = await write_artifact(ws, 'Changed')
    untouched = await write_artifact(ws, 'Untouched')
    cache_a = workspace.checkout_path(ws)
    for artifact in (changed, untouched):
        dist = cache_a / 'artifacts' / str(artifact.id) / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('built')
    # Process B edits `changed`.
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache-b'))
    workspace.reset_state()
    async with workspace.edit(ws, 'run_code') as tx:
        (tx.artifact_dir(changed.id) / 'main.md').write_text('# new\n')
    # Back in process A, the next sync drops only the stale build.
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(cache_a.parent))
    workspace.reset_state()
    async with workspace.open_artifact(changed) as directory:
        assert (directory / 'main.md').read_text() == '# new\n'
        assert not (directory / 'dist').exists()
    assert (cache_a / 'artifacts' / str(untouched.id) / 'dist' / 'index.html').exists()


async def test_nested_git_entries_are_stripped(principal: auth.Principal):
    ws = principal.workspace_id
    artifact = await write_artifact(ws)
    async with workspace.edit(ws, 'run_code') as tx:
        directory = tx.artifact_dir(artifact.id)
        (directory / '.git').mkdir()
        (directory / '.git' / 'HEAD').write_text('ref: refs/heads/main')
        (directory / '.gitignore').write_text('main.md\n')
        (directory / 'kept.txt').write_text('kept')
    path = workspace.checkout_path(ws)
    tracked = (await workspace.git('ls-files', cwd=path)).split()
    assert f'artifacts/{artifact.id}/kept.txt' in tracked
    assert not any('.git' in name.split('/')[-1] for name in tracked if name != '.gitignore')
    assert not (path / 'artifacts' / str(artifact.id) / '.git').exists()


async def test_import_directory(principal: auth.Principal):
    artifact = await workspace.import_directory(principal.workspace_id, 'Starter', 'deck', STARTER)
    assert artifact.title == 'Starter'
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').is_file()
        assert (directory / 'components' / 'Hero.html').is_file()
        assert not (directory / 'dist').exists()


async def test_lookups_are_scoped(principal: auth.Principal, db_pool: db.Pool):
    artifact = await write_artifact(principal.workspace_id)
    async with db_pool.acquire() as conn, conn.transaction():
        other = await auth.upsert_user(conn, sub='user-b', email='b@example.com', name=None, picture=None)
    assert await workspace.get_artifact_in(principal.workspace_id, artifact.id) == artifact
    assert await workspace.get_artifact_in(other.workspace_id, artifact.id) is None
    assert await workspace.list_artifacts(other.workspace_id) == []
    assert await workspace.get_artifact(uuid.uuid4()) is None


async def test_edit_unknown_workspace(db_pool: db.Pool, storage: store.ObjectStore):
    with pytest.raises(LookupError, match='does not exist'):
        async with workspace.edit(uuid.uuid4(), 'x'):
            pass


async def test_bundle_verifies(principal: auth.Principal, storage: store.ObjectStore, tmp_path: Path):
    ws = principal.workspace_id
    await write_artifact(ws)
    sha = await head_sha(ws)
    assert sha
    bundle = tmp_path / 'check.bundle'
    bundle.write_bytes(await storage.get(workspace.bundle_key(ws, sha)))
    out = await workspace.git('bundle', 'list-heads', str(bundle), cwd=tmp_path)
    assert out.split() == [sha, 'refs/heads/main']
