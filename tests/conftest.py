"""Shared fixtures: a throwaway Postgres database for the session, and per-test pool, store and cache.

Only tests that ask for `db_pool` (directly or through another fixture) need Postgres; `test_build.py` and
`test_pdf.py` run without it. `DATABASE_URL` names the server; the maintenance database `postgres` on that server
is used to create and drop `openartifact_test_<hex>`.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

import auth
import db
import store
import workspace

# The MCP server builds its auth provider at import time; give it the static development token before any test
# module imports it. Tests that need a second user register more tokens on the same verifier.
DEV_TOKEN = 'test-dev-token'
os.environ['OPENARTIFACT_DEV_TOKEN'] = DEV_TOKEN  # not setdefault: the Makefile exports a dev token of its own

TABLES = ('users', 'credentials', 'workspaces', 'organizations', 'organization_members', 'artifacts', 'chats')


@pytest.fixture(scope='session')
def anyio_backend() -> str:
    return 'asyncio'


def with_database(url: str, name: str) -> str:
    """The same server URL pointing at database `name`."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f'/{name}'))


@pytest.fixture(scope='session')
def test_database_url() -> Iterator[str]:
    """Create a fresh database, migrate it, point `DATABASE_URL` at it for the session, drop it afterwards."""
    base = db.database_url()
    maintenance = with_database(base, 'postgres')
    name = f'openartifact_test_{secrets.token_hex(4)}'

    async def create() -> None:
        try:
            conn = await asyncpg.connect(maintenance)
        except (OSError, asyncpg.PostgresError) as exc:
            pytest.exit(f'cannot reach Postgres at {maintenance} ({exc}); run `make pg-start`', returncode=1)
        try:
            await conn.execute(f'CREATE DATABASE {name}')
        finally:
            await conn.close()

    async def migrate(url: str) -> None:
        conn = await asyncpg.connect(url)
        try:
            assert await db.migrate(conn), 'no migrations applied'
            assert await db.migrate(conn) == [], 'migrations are not idempotent'
        finally:
            await conn.close()

    async def drop() -> None:
        conn = await asyncpg.connect(maintenance)
        try:
            await conn.execute(f'DROP DATABASE {name} WITH (FORCE)')
        finally:
            await conn.close()

    asyncio.run(create())
    url = with_database(base, name)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('DATABASE_URL', url)
            asyncio.run(migrate(url))
            yield url
    finally:
        asyncio.run(drop())


@pytest.fixture
async def db_pool(test_database_url: str) -> AsyncGenerator[db.Pool]:
    """The app's pool, open for one test; tables are emptied afterwards so tests are independent."""
    async with db.db_pool() as pool:
        try:
            yield pool
        finally:
            await pool.execute(f'TRUNCATE {", ".join(TABLES)} CASCADE')
            # Per-process state would otherwise point at checkouts that no longer exist.
            workspace.reset_state()


@pytest.fixture
async def principal(db_pool: db.Pool, storage: store.ObjectStore) -> auth.Principal:
    """A signed-in user with an empty workspace; use `with auth.as_principal(principal):` around tool calls."""
    async with db_pool.acquire() as conn, conn.transaction():
        return await auth.upsert_user(conn, sub='user-a', email='a@example.com', name='User A', picture=None)


@pytest.fixture
async def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[store.ObjectStore]:
    """A file-backed object store and an empty checkout cache under the test's temporary directory."""
    monkeypatch.setenv('OPENARTIFACT_STORE_URL', (tmp_path / 'store').as_uri())
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache'))
    async with store.object_store() as opened:
        yield opened


async def truncate_all() -> None:
    """Empty every table through a fresh connection, for tests whose pool belongs to another loop (the app's)."""
    conn = await asyncpg.connect(db.database_url())
    try:
        await conn.execute(f'TRUNCATE {", ".join(TABLES)} CASCADE')
    finally:
        await conn.close()


@pytest.fixture
def server_env(test_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Environment for running the whole app (TestClient or uvicorn): fresh store and cache, tables emptied after.

    Unlike `db_pool`/`storage`, nothing is opened here; the app's lifespan opens its own pool, store and monty pool
    in its own loop, and the test seeds data through them.
    """
    monkeypatch.setenv('OPENARTIFACT_STORE_URL', (tmp_path / 'store').as_uri())
    monkeypatch.setenv('OPENARTIFACT_CACHE_DIR', str(tmp_path / 'cache'))
    try:
        yield
    finally:
        asyncio.run(truncate_all())
        workspace.reset_state()
