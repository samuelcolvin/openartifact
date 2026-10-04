"""Tests for `backend/db.py`: the migration runner and the pool lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

import db

pytestmark = pytest.mark.anyio


def write_migrations(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text('select 1;\n')


def test_migration_files_are_sorted_and_consecutive(tmp_path: Path):
    write_migrations(tmp_path, '0002_second.sql', '0001_first.sql')
    assert [(v, n) for v, n, _ in db.migration_files(tmp_path)] == [(1, 'first'), (2, 'second')]


def test_migration_files_reject_gaps(tmp_path: Path):
    write_migrations(tmp_path, '0001_first.sql', '0003_third.sql')
    with pytest.raises(db.MigrationError, match=r'0003_third\.sql: expected version 0002'):
        db.migration_files(tmp_path)


def test_migration_files_reject_duplicates_and_bad_names(tmp_path: Path):
    write_migrations(tmp_path, '0001_first.sql', '0001_other.sql')
    with pytest.raises(db.MigrationError, match='expected version 0002'):
        db.migration_files(tmp_path)
    write_migrations(tmp_path, 'notes.sql')
    with pytest.raises(db.MigrationError, match='named NNNN_name.sql'):
        db.migration_files(tmp_path)


def test_repo_migrations_are_well_formed():
    files = db.migration_files()
    assert files[0][1] == 'initial'


async def test_schema_and_idempotent_migrate(db_pool: db.Pool):
    async with db_pool.acquire() as conn:
        assert await db.migrate(conn) == []
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
        )
        assert {row['table_name'] for row in rows} >= {
            'users',
            'credentials',
            'workspaces',
            'artifacts',
            'schema_migrations',
        }
        versions = await conn.fetch('SELECT version, name FROM schema_migrations ORDER BY version')
        assert [(row['version'], row['name']) for row in versions] == [(1, 'initial')]


async def test_migrate_applies_new_files_once(db_pool: db.Pool, tmp_path: Path):
    # A private migrations directory, applied against a scratch table so the shared schema is untouched.
    (tmp_path / '0001_initial.sql').write_text('create table scratch (n int);\n')
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM schema_migrations')
        try:
            assert await db.migrate(conn, tmp_path) == ['0001_initial.sql']
            (tmp_path / '0002_more.sql').write_text('insert into scratch values (1); insert into scratch values (2);\n')
            assert await db.migrate(conn, tmp_path) == ['0002_more.sql']
            assert await conn.fetchval('SELECT count(*) FROM scratch') == 2
            assert await db.migrate(conn, tmp_path) == []
        finally:
            await conn.execute('DROP TABLE IF EXISTS scratch')
            await conn.execute('DELETE FROM schema_migrations')
            await conn.execute("INSERT INTO schema_migrations (version, name) VALUES (1, 'initial')")


def test_pool_not_running():
    with pytest.raises(RuntimeError, match='database pool is not running'):
        db.pool()
