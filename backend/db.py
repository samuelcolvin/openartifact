"""Postgres: the asyncpg pool for the app's lifetime and a small migration runner.

The pool is opened by `db_pool()` in the server lifespan (beside the monty pool and the object store) and reached
through `pool()` everywhere else. Migrations are plain SQL files in `migrations/`, named `NNNN_name.sql` and
applied in order at startup; `schema_migrations` records what has run. There is deliberately no migration tool:
the files are read and executed here.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TypeAlias

import asyncpg
import asyncpg.pool
import logfire

DEFAULT_DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/openartifact'
MIGRATIONS_DIR = Path(__file__).resolve().parent / 'migrations'
MIGRATION_FILE_RE = re.compile(r'(\d{4})_([a-z0-9_]+)\.sql')
# Advisory lock taken while migrating so two processes starting together apply each file once. Any constant works.
MIGRATION_LOCK_KEY = 0x6F70656E

# String aliases: asyncpg's classes are generic only in the stubs, not at runtime. `Connection` covers both a
# direct connection and the proxy `pool.acquire()` hands out.
Pool: TypeAlias = 'asyncpg.Pool[asyncpg.Record]'
Connection: TypeAlias = 'asyncpg.Connection[asyncpg.Record] | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]'


class MigrationError(Exception):
    """The migrations directory is malformed: a gap, a duplicate version or a bad file name."""


def database_url() -> str:
    """The connection URL, from `DATABASE_URL`; the default matches docker-compose.yml."""
    return os.environ.get('DATABASE_URL') or DEFAULT_DATABASE_URL


_pool: Pool | None = None


@contextlib.asynccontextmanager
async def db_pool() -> AsyncGenerator[Pool]:
    """Open the connection pool for the duration of the block and publish it to `pool()`."""
    global _pool
    opened = await asyncpg.create_pool(database_url(), min_size=1, max_size=10)
    _pool = opened
    try:
        yield opened
    finally:
        _pool = None
        await opened.close()


def pool() -> Pool:
    """The pool opened by `db_pool()`."""
    if _pool is None:
        raise RuntimeError('the database pool is not running: enter `db_pool()` first')
    return _pool


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    """The migrations in `directory` as `(version, name, path)`, sorted; versions must run 1, 2, 3 ... with no gaps."""
    found: list[tuple[int, str, Path]] = []
    for path in sorted(directory.glob('*.sql')):
        match = MIGRATION_FILE_RE.fullmatch(path.name)
        if match is None:
            raise MigrationError(f'{path.name}: migration files are named NNNN_name.sql')
        found.append((int(match.group(1)), match.group(2), path))
    found.sort()
    for expected, (version, _name, path) in enumerate(found, start=1):
        if version != expected:
            raise MigrationError(
                f'{path.name}: expected version {expected:04d}; versions must be consecutive from 0001'
            )
    return found


async def migrate(conn: Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration not yet recorded in `schema_migrations`; returns the file names applied, in order.

    Each file runs in its own transaction, so a file must not contain statements that cannot (such as
    `CREATE INDEX CONCURRENTLY`). `conn.execute` with no arguments uses the simple query protocol, so a file may
    hold several statements.
    """
    files = migration_files(directory)
    await conn.execute(
        'CREATE TABLE IF NOT EXISTS schema_migrations ('
        ' version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())'
    )
    await conn.execute('SELECT pg_advisory_lock($1)', MIGRATION_LOCK_KEY)
    try:
        applied = {row['version'] for row in await conn.fetch('SELECT version FROM schema_migrations')}
        names: list[str] = []
        for version, name, path in files:
            if version in applied:
                continue
            with logfire.span('migrate {name}', name=path.name):
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding='utf-8'))
                    await conn.execute('INSERT INTO schema_migrations (version, name) VALUES ($1, $2)', version, name)
            names.append(path.name)
        return names
    finally:
        await conn.execute('SELECT pg_advisory_unlock($1)', MIGRATION_LOCK_KEY)
