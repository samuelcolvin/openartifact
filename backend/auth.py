"""Who is calling: the MCP auth provider, and the `Principal` the tools act as.

In production the MCP endpoint is protected by Google login through FastMCP's `GoogleProvider`, an OAuth proxy
that issues its own tokens and keeps its state (client registrations, Google refresh tokens) encrypted in
Postgres. Each Google identity becomes a row in `users` with its own workspace on first sight. Without Google
credentials, `OPENARTIFACT_DEV_TOKEN` names one static bearer token for a development user.

Tools call `current_principal()`; it reads the verified token's claims. Code that is not inside an MCP request
(tests, scripts) wraps calls in `as_principal()`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass

from config import base_url
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.dependencies import get_access_token

import db

logger = logging.getLogger(__name__)

DEV_SUB = 'dev'
DEV_CLAIMS = {'sub': DEV_SUB, 'email': 'dev@localhost', 'name': 'Developer'}
GOOGLE_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
]


def make_auth_provider() -> AuthProvider:
    """The `auth=` for the FastMCP server, from the environment.

    `GOOGLE_CLIENT_ID` (with `GOOGLE_CLIENT_SECRET` and `OPENARTIFACT_SECRET_KEY`) selects Google login;
    otherwise `OPENARTIFACT_DEV_TOKEN` selects a single static token. Neither is a startup error: an MCP
    endpoint with no identity would create artifacts nobody owns.
    """
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    if client_id:
        secret_key = os.environ.get('OPENARTIFACT_SECRET_KEY')
        if not secret_key:
            raise RuntimeError('OPENARTIFACT_SECRET_KEY (a Fernet key) is required with GOOGLE_CLIENT_ID')
        # Imported here so the dev path needs neither cryptography nor the key-value Postgres store.
        from cryptography.fernet import Fernet
        from key_value.aio.stores.postgresql import PostgreSQLStore
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        storage = FernetEncryptionWrapper(
            key_value=PostgreSQLStore(url=db.database_url(), table_name='oauth_state'),
            fernet=Fernet(secret_key),
            raise_on_decryption_error=False,
        )
        return GoogleProvider(
            client_id=client_id,
            client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
            base_url=base_url(),
            required_scopes=GOOGLE_SCOPES,
            jwt_signing_key=secret_key,
            client_storage=storage,
        )
    dev_token = os.environ.get('OPENARTIFACT_DEV_TOKEN')
    if dev_token:
        logger.warning('OPENARTIFACT_DEV_TOKEN is set: anyone holding that token is the development user')
        return StaticTokenVerifier(tokens={dev_token: {'client_id': DEV_SUB, 'scopes': [], **DEV_CLAIMS}})
    raise RuntimeError(
        'no authentication configured: set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and OPENARTIFACT_SECRET_KEY, '
        'or OPENARTIFACT_DEV_TOKEN for local development'
    )


@dataclass(frozen=True)
class Principal:
    """The authenticated user and the workspace their tools act on."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    email: str | None


_override: ContextVar[Principal | None] = ContextVar('principal', default=None)
# sub -> Principal, so a tool call after the first is a dictionary lookup rather than an upsert.
_cache: dict[str, Principal] = {}


def reset_cache() -> None:
    """Forget resolved principals; for tests that empty the database between runs."""
    _cache.clear()


@contextlib.contextmanager
def as_principal(principal: Principal) -> Generator[None]:
    """Make `current_principal()` return `principal` inside the block, bypassing token lookup."""
    token = _override.set(principal)
    try:
        yield
    finally:
        _override.reset(token)


async def upsert_user(
    conn: db.Connection, *, sub: str, email: str | None, name: str | None, picture: str | None
) -> Principal:
    """Create or refresh the user for an identity, create their workspace on first sight, return the principal."""
    user_id: uuid.UUID = await conn.fetchval(
        'INSERT INTO users (id, google_sub, email, name, picture) VALUES ($1, $2, $3, $4, $5) '
        'ON CONFLICT (google_sub) DO UPDATE SET email = EXCLUDED.email, name = EXCLUDED.name, '
        'picture = EXCLUDED.picture, last_login_at = now() RETURNING id',
        uuid.uuid4(),
        sub,
        email,
        name,
        picture,
    )
    workspace_id: uuid.UUID | None = await conn.fetchval(
        'SELECT id FROM workspaces WHERE owner_user_id = $1 ORDER BY created_at LIMIT 1', user_id
    )
    if workspace_id is None:
        workspace_id = uuid.uuid4()
        await conn.execute('INSERT INTO workspaces (id, owner_user_id) VALUES ($1, $2)', workspace_id, user_id)
    return Principal(user_id=user_id, workspace_id=workspace_id, email=email)


async def current_principal() -> Principal:
    """The caller of the current tool, from the verified access token; `ToolError` when there is none."""
    override = _override.get()
    if override is not None:
        return override
    token = get_access_token()
    if token is None:
        raise ToolError('not authenticated')
    claims = token.claims
    sub = str(claims.get('sub') or token.client_id)
    cached = _cache.get(sub)
    if cached is not None:
        return cached
    async with db.pool().acquire() as conn, conn.transaction():
        principal = await upsert_user(
            conn, sub=sub, email=claims.get('email'), name=claims.get('name'), picture=claims.get('picture')
        )
    _cache[sub] = principal
    return principal
