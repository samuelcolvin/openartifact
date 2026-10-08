"""Tests for `backend/workspace.py`: the git sequences, the edit protocol and its failure paths."""

from __future__ import annotations

import tomllib
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


async def head_sha(artifact_id: uuid.UUID) -> str | None:
    return await db.pool().fetchval('SELECT head_sha FROM artifacts WHERE id = $1', artifact_id)


async def git_log(artifact_id: uuid.UUID) -> list[str]:
    return (await workspace.artifact_git(artifact_id, 'log', '--format=%s')).splitlines()


async def write_artifact(ws: uuid.UUID, title: str = 'Demo', body: str = '# hi\n') -> workspace.Artifact:
    """One edit creating an artifact with a single file."""
    artifact_id = uuid.uuid4()
    create = workspace.NewArtifact(workspace_id=ws, title=title, type='page')
    async with workspace.edit(artifact_id, f'new_artifact: {artifact_id}', create=create) as tx:
        (tx.path / 'main.md').write_text(body)
    found = await workspace.get_artifact(artifact_id)
    assert found is not None
    return found


async def test_first_edit_creates_repo_bundle_and_head(principal: auth.Principal, storage: store.ObjectStore):
    ws = principal.workspace_id
    artifact = await write_artifact(ws)
    sha = artifact.head_sha
    assert sha is not None and await head_sha(artifact.id) == sha
    assert (await workspace.artifact_git(artifact.id, 'rev-parse', 'HEAD')).strip() == sha
    assert await git_log(artifact.id) == [f'new_artifact: {artifact.id}']
    # The tree holds the artifact's files and nothing of git's; the repository sits beside it.
    tree = workspace.checkout_path(artifact.id)
    assert sorted(p.name for p in tree.iterdir()) == ['main.md']
    assert (workspace.git_dir(artifact.id) / 'info' / 'exclude').read_text() == 'dist/\n'
    assert (await workspace.artifact_git(artifact.id, 'status', '--porcelain')) == ''
    assert await storage.exists(workspace.bundle_key(artifact.id, sha))
    assert await workspace.list_artifacts(ws) == [artifact]


async def test_create_rolls_back_with_the_body(principal: auth.Principal):
    artifact_id = uuid.uuid4()
    create = workspace.NewArtifact(workspace_id=principal.workspace_id, title='Doomed', type='page')
    with pytest.raises(RuntimeError, match='boom'):
        async with workspace.edit(artifact_id, 'new', create=create):
            raise RuntimeError('boom')
    assert await workspace.get_artifact(artifact_id) is None
    assert await workspace.list_artifacts(principal.workspace_id) == []


async def test_dist_is_ignored(principal: auth.Principal):
    artifact = await write_artifact(principal.workspace_id)
    async with workspace.edit(artifact.id, 'noise') as tx:
        dist = tx.path / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('<html>')
    assert await git_log(artifact.id) == [f'new_artifact: {artifact.id}']
    assert (workspace.checkout_path(artifact.id) / 'dist' / 'index.html').exists()


async def test_edit_without_changes_is_a_noop(principal: auth.Principal, storage: store.ObjectStore):
    artifact = await write_artifact(principal.workspace_id)
    async with workspace.edit(artifact.id, 'nothing'):
        pass
    assert await head_sha(artifact.id) == artifact.head_sha
    assert await storage.list(f'artifacts/{artifact.id}/') == [
        workspace.bundle_key(artifact.id, artifact.head_sha or '')
    ]


async def test_body_error_rolls_back_and_resets_checkout(principal: auth.Principal):
    artifact = await write_artifact(principal.workspace_id)
    with pytest.raises(RuntimeError, match='boom'):
        async with workspace.edit(artifact.id, 'bad') as tx:
            (tx.path / 'main.md').write_text('changed')
            raise RuntimeError('boom')
    assert await head_sha(artifact.id) == artifact.head_sha
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').read_text() == '# hi\n'
    assert (await workspace.get_artifact(artifact.id)) == artifact  # updated_at untouched: nothing was committed


async def test_upload_failure_rolls_back(principal: auth.Principal, monkeypatch: pytest.MonkeyPatch):
    artifact = await write_artifact(principal.workspace_id)

    async def failing_put(self: store.ObjectStore, key: str, data: bytes) -> None:
        raise OSError('store down')

    monkeypatch.setattr(store.ObjectStore, 'put', failing_put)
    with pytest.raises(OSError, match='store down'):
        async with workspace.edit(artifact.id, 'run_code') as tx:
            (tx.path / 'main.md').write_text('changed')
    monkeypatch.undo()
    assert await head_sha(artifact.id) == artifact.head_sha
    # The local commit that was never published is discarded on the next sync.
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').read_text() == '# hi\n'
    assert await git_log(artifact.id) == [f'new_artifact: {artifact.id}']


async def test_missing_bundle_is_a_clear_error(
    principal: auth.Principal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A database pointing at a bundle the store does not have, as when the two come from different deployments."""
    artifact = await write_artifact(principal.workspace_id)
    assert artifact.head_sha is not None
    await store.store().delete(workspace.bundle_key(artifact.id, artifact.head_sha))
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache-b'))
    workspace.reset_state()
    with pytest.raises(workspace.GitError, match='missing from the object store'):
        async with workspace.open_artifact(artifact):
            pass


async def test_second_cache_syncs_from_bundle(
    principal: auth.Principal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact = await write_artifact(principal.workspace_id, body='# from A\n')
    async with workspace.edit(artifact.id, 'run_code') as tx:
        (tx.path / 'extra.txt').write_text('second commit')
    # Another process: an empty cache directory and no memory of what is synced.
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache-b'))
    workspace.reset_state()
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').read_text() == '# from A\n'
        assert (directory / 'extra.txt').read_text() == 'second commit'
    assert len(await git_log(artifact.id)) == 2
    assert 'origin' not in await workspace.artifact_git(artifact.id, 'remote')
    # And it can continue the history.
    async with workspace.edit(artifact.id, 'run_code') as tx:
        (tx.path / 'third.txt').write_text('3')
    assert len(await git_log(artifact.id)) == 3


async def test_sync_removes_stale_dist_for_a_changed_artifact(
    principal: auth.Principal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ws = principal.workspace_id
    changed = await write_artifact(ws, 'Changed')
    untouched = await write_artifact(ws, 'Untouched')
    cache_a = Path(workspace.cache_dir())
    for artifact in (changed, untouched):
        dist = workspace.checkout_path(artifact.id) / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('built')
    # Process B edits `changed`.
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache-b'))
    workspace.reset_state()
    async with workspace.edit(changed.id, 'run_code') as tx:
        (tx.path / 'main.md').write_text('# new\n')
    # Back in process A, the next sync drops only the stale build.
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(cache_a))
    workspace.reset_state()
    async with workspace.open_artifact(changed) as directory:
        assert (directory / 'main.md').read_text() == '# new\n'
        assert not (directory / 'dist').exists()
    async with workspace.open_artifact(untouched) as directory:
        assert (directory / 'dist' / 'index.html').exists()


async def test_artifacts_do_not_share_a_lock(principal: auth.Principal):
    """Two artifacts are two repositories: an edit of one can run inside an edit of the other."""
    ws = principal.workspace_id
    first = await write_artifact(ws, 'First')
    second = await write_artifact(ws, 'Second')
    async with workspace.edit(first.id, 'outer') as outer:
        (outer.path / 'a.txt').write_text('a')
        async with workspace.edit(second.id, 'inner') as inner:
            (inner.path / 'b.txt').write_text('b')
    assert await git_log(first.id) == ['outer', f'new_artifact: {first.id}']
    assert await git_log(second.id) == ['inner', f'new_artifact: {second.id}']


async def test_nested_git_entries_are_stripped(principal: auth.Principal):
    artifact = await write_artifact(principal.workspace_id)
    async with workspace.edit(artifact.id, 'run_code') as tx:
        (tx.path / '.git').mkdir()
        (tx.path / '.git' / 'HEAD').write_text('ref: refs/heads/main')
        (tx.path / '.gitignore').write_text('main.md\n')
        (tx.path / 'kept.txt').write_text('kept')
    tracked = (await workspace.artifact_git(artifact.id, 'ls-files')).split()
    assert tracked == ['kept.txt', 'main.md']
    assert not (workspace.checkout_path(artifact.id) / '.git').exists()


async def test_import_directory(principal: auth.Principal):
    artifact = await workspace.import_directory(principal.workspace_id, 'Starter', 'deck', STARTER)
    assert artifact.title == 'Starter' and artifact.head_sha is not None
    async with workspace.open_artifact(artifact) as directory:
        assert (directory / 'main.md').is_file()
        assert (directory / 'components' / 'Hero.html').is_file()
        assert not (directory / 'dist').exists()
    assert await git_log(artifact.id) == [f'import: {artifact.id}']


async def test_clone_repository(principal: auth.Principal, tmp_path: Path):
    artifact = await write_artifact(principal.workspace_id)
    async with workspace.edit(artifact.id, 'second') as tx:
        (tx.path / 'main.md').write_text('# two\n')
    dest = tmp_path / 'clone'
    async with workspace.open_artifact(artifact):
        await workspace.clone_repository(artifact.id, dest)
    assert (dest / 'main.md').read_text() == '# two\n'
    assert (await workspace.git('log', '--format=%s', cwd=dest)).splitlines() == [
        'second',
        f'new_artifact: {artifact.id}',
    ]
    assert (await workspace.git('status', '--porcelain', cwd=dest)) == ''
    assert (await workspace.git('remote', cwd=dest)) == ''
    assert (dest / '.git' / 'info' / 'exclude').read_text() == 'dist/\n'


async def test_lookups_are_scoped(principal: auth.Principal, db_pool: db.Pool):
    artifact = await write_artifact(principal.workspace_id)
    async with db_pool.acquire() as conn, conn.transaction():
        other = await auth.upsert_user(conn, sub='user-b', email='b@example.com', name=None, picture=None)
    assert await workspace.get_artifact_in(principal.workspace_id, artifact.id) == artifact
    assert await workspace.get_artifact_in(other.workspace_id, artifact.id) is None
    assert await workspace.list_artifacts(other.workspace_id) == []
    assert await workspace.get_artifact(uuid.uuid4()) is None


async def test_edit_unknown_artifact(db_pool: db.Pool, storage: store.ObjectStore):
    with pytest.raises(LookupError, match='does not exist'):
        async with workspace.edit(uuid.uuid4(), 'x'):
            pass


async def test_bundle_verifies(principal: auth.Principal, storage: store.ObjectStore, tmp_path: Path):
    artifact = await write_artifact(principal.workspace_id)
    assert artifact.head_sha
    bundle = tmp_path / 'check.bundle'
    bundle.write_bytes(await storage.get(workspace.bundle_key(artifact.id, artifact.head_sha)))
    out = await workspace.git('bundle', 'list-heads', str(bundle), cwd=tmp_path)
    assert out.split() == [artifact.head_sha, 'refs/heads/main']


async def test_fork_keeps_history(principal: auth.Principal, db_pool: db.Pool):
    source = await write_artifact(principal.workspace_id, 'Original', body='# one\n')
    async with workspace.edit(source.id, 'second') as tx:
        (tx.path / 'main.md').write_text('# two\n')
        (tx.path / 'dist').mkdir()
        (tx.path / 'dist' / 'index.html').write_text('built')
    source = await workspace.get_artifact(source.id) or source
    async with db_pool.acquire() as conn, conn.transaction():
        other = await auth.upsert_user(conn, sub='user-b', email='b@example.com', name=None, picture=None)
    fork = await workspace.fork_artifact(
        source, other.workspace_id, organization_id=None, visibility='private', org_editable=False
    )
    assert fork.forked_from == source.id and fork.workspace_id == other.workspace_id
    assert (fork.title, fork.type, fork.visibility) == ('Original', 'page', 'private')
    assert fork.head_sha is not None and fork.head_sha != source.head_sha
    assert await git_log(fork.id) == [f'fork of {source.id}', 'second', f'new_artifact: {source.id}']
    async with workspace.open_artifact(fork) as directory:
        assert (directory / 'main.md').read_text() == '# two\n'
        assert not (directory / 'dist').exists()
    # The fork is its own repository: editing it leaves the source alone, and vice versa.
    async with workspace.edit(fork.id, 'forked edit') as tx:
        (tx.path / 'main.md').write_text('# three\n')
    assert await git_log(source.id) == ['second', f'new_artifact: {source.id}']
    assert await workspace.list_artifacts(other.workspace_id) == [await workspace.get_artifact(fork.id)]


async def test_shared_listing_and_set_access(principal: auth.Principal, db_pool: db.Pool):
    async with db_pool.acquire() as conn, conn.transaction():
        me = await auth.upsert_user(conn, sub='user-a', email='a@example.com', name=None, picture=None, hd='x.test')
        colleague = await auth.upsert_user(
            conn, sub='user-c', email='c@example.com', name=None, picture=None, hd='x.test'
        )
        stranger = await auth.upsert_user(conn, sub='user-s', email='s@example.com', name=None, picture=None)
    assert me.organization_id is not None and me.org_ids == colleague.org_ids == {me.organization_id}
    assert stranger.org_ids == frozenset() and stranger.organization_id is None
    org = await workspace.get_organization(me.organization_id)
    assert org is not None and (org.domain, org.name) == ('x.test', 'x.test')

    mine = uuid.uuid4()
    create = workspace.NewArtifact(me.workspace_id, 'Shared', 'page', visibility='org', organization_id=org.id)
    async with workspace.edit(mine, 'new', create=create) as tx:
        (tx.path / 'main.md').write_text('# shared\n')
    private = await write_artifact(me.workspace_id, 'Private')
    shared = await workspace.list_shared_artifacts(colleague.org_ids, colleague.workspace_id)
    assert [(a.id, email) for a, email in shared] == [(mine, 'a@example.com')]
    assert await workspace.list_shared_artifacts(me.org_ids, me.workspace_id) == []
    assert private.visibility == 'private'

    org_id = me.organization_id
    changed = await workspace.set_access(mine, visibility='public', org_editable=True, organization_id=org_id)
    assert (changed.visibility, changed.org_editable) == ('public', True)
    with pytest.raises(Exception, match='artifacts_org_access'):
        await workspace.set_access(mine, visibility='private', org_editable=False, organization_id=org_id)
    with pytest.raises(LookupError):
        await workspace.set_access(uuid.uuid4(), visibility='public', org_editable=False, organization_id=None)
    # Moving it out of the organisation makes it personal and private; colleagues no longer see it.
    moved = await workspace.set_access(mine, visibility='private', org_editable=False, organization_id=None)
    assert (moved.organization_id, moved.visibility) == (None, 'private')
    assert await workspace.list_shared_artifacts(colleague.org_ids, colleague.workspace_id) == []


async def test_rename_artifact_updates_toml_and_row(principal: auth.Principal):
    artifact = await workspace.import_directory(principal.workspace_id, 'Starter', 'deck', STARTER)
    renamed = await workspace.rename_artifact(artifact.id, 'Quarterly "review" 2026')
    assert renamed.title == 'Quarterly "review" 2026'
    config = (workspace.checkout_path(artifact.id) / 'artifact.toml').read_text()
    assert (
        config.splitlines()[0] == 'title = "Quarterly \\"review\\" 2026"'
        or 'title = "Quarterly \\"review\\" 2026"' in config
    )
    assert tomllib.loads(config)['title'] == 'Quarterly "review" 2026'
    log = (await workspace.artifact_git(artifact.id, 'log', '--format=%s')).splitlines()
    assert log[0] == 'rename: Quarterly "review" 2026'
    found = await workspace.get_artifact(artifact.id)
    assert found is not None and found.title == 'Quarterly "review" 2026'
    # Without a title line, one is added at the top.
    (workspace.checkout_path(artifact.id) / 'artifact.toml').write_text('type = "deck"\n')
    await workspace.rename_artifact(artifact.id, 'Plain')
    assert (workspace.checkout_path(artifact.id) / 'artifact.toml').read_text().startswith('title = "Plain"\ntype')
