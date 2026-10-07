"""Who is calling: the MCP auth provider, and the `Principal` the tools act as.

In production the MCP endpoint is protected by Google login through FastMCP's `GoogleProvider`, an OAuth proxy
that issues its own tokens and keeps its state (client registrations, Google refresh tokens) encrypted in
Postgres. Each Google identity becomes a row in `users` with its own workspace on first sight. Without Google
credentials, `OPENARTIFACT_DEV_TOKEN` names one static bearer token for a development user.

Tools call `current_principal()`; it reads the verified token's claims. Browser sessions (`login.py`) resolve to
the same `Principal` through `load_principal()`. Code that is not inside an MCP request (tests, scripts) wraps
calls in `as_principal()`. Organisations come from Google Workspace accounts: `hosted_domain()` reads the `hd`
claim and `upsert_user()` records the membership.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import cast

import logfire
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.dependencies import get_access_token

import db
from config import base_url

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
        install_pages()
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


def install_pages() -> None:
    """Make FastMCP's consent and OAuth error pages ours (`pages.py`).

    FastMCP binds the renderers as module globals in the modules that call them, so replacing those names is the
    whole override: the consent logic, CSRF tokens and cookies stay FastMCP's. `test_server.py` pins the keyword
    arguments our renderers must accept.
    """
    import fastmcp.server.auth.oauth_proxy.consent as fastmcp_consent
    import fastmcp.server.auth.oauth_proxy.proxy as fastmcp_proxy
    import pages

    # Written through the module dict: a plain assignment is a private-import error for the type checker.
    fastmcp_consent.__dict__['create_consent_html'] = pages.consent_html
    fastmcp_proxy.__dict__['create_error_html'] = pages.oauth_error_html


@dataclass(frozen=True)
class Principal:
    """The authenticated user: what the tools act as, and what a browser session resolves to.

    `org_ids` are the organisations the user belongs to (`access.py` checks artifacts against them);
    `organization_id` is the one new artifacts are placed in, the single organisation a user has today and a
    choice once a user can be in several.
    """

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    email: str | None
    name: str | None = None
    picture: str | None = None
    org_ids: frozenset[uuid.UUID] = frozenset()
    organization_id: uuid.UUID | None = None


# Set by `as_principal()` for code running outside an MCP request (tests, scripts). A plain global rather than a
# ContextVar: it is process-wide by design, and a ContextVar would not reach a test task from a sync fixture.
_override: Principal | None = None


@contextlib.contextmanager
def as_principal(principal: Principal) -> Generator[None]:
    """Make `current_principal()` return `principal` inside the block, bypassing token lookup."""
    global _override
    previous = _override
    _override = principal
    try:
        yield
    finally:
        _override = previous


def hosted_domain(claims: Mapping[str, object]) -> str | None:
    """The Google Workspace domain of a verified account, from the `hd` claim; None for personal accounts.

    Google sets `hd` only for accounts an organisation administers, so it is what makes someone a member of that
    organisation here; the email's own domain is never used. The verifier's claims carry `email_verified` as a
    bool (userinfo) or the string `"true"` (tokeninfo), and `hd` either at the top level or inside
    `google_user_data`.
    """
    verified = claims.get('email_verified')
    if verified not in (True, 'true'):
        return None
    hd = claims.get('hd')
    if hd is None:
        extra = claims.get('google_user_data')
        if isinstance(extra, Mapping):
            hd = cast('Mapping[str, object]', extra).get('hd')
    return hd.strip().lower() or None if isinstance(hd, str) else None


async def organizations_of(conn: db.Connection, user_id: uuid.UUID) -> list[uuid.UUID]:
    """The organisations a user belongs to, oldest membership first."""
    rows = await conn.fetch(
        'SELECT organization_id FROM organization_members WHERE user_id = $1 ORDER BY created_at, organization_id',
        user_id,
    )
    return [row['organization_id'] for row in rows]


async def principal_for(
    conn: db.Connection, user_id: uuid.UUID, email: str | None, name: str | None, picture: str | None
) -> Principal:
    """Assemble the principal for a user row: their workspace (created on first sight) and organisations."""
    workspace_id: uuid.UUID | None = await conn.fetchval(
        'SELECT id FROM workspaces WHERE owner_user_id = $1 ORDER BY created_at LIMIT 1', user_id
    )
    if workspace_id is None:
        workspace_id = uuid.uuid4()
        await conn.execute('INSERT INTO workspaces (id, owner_user_id) VALUES ($1, $2)', workspace_id, user_id)
    org_ids = await organizations_of(conn, user_id)
    return Principal(
        user_id=user_id,
        workspace_id=workspace_id,
        email=email,
        name=name,
        picture=picture,
        org_ids=frozenset(org_ids),
        organization_id=org_ids[0] if org_ids else None,
    )


@logfire.instrument
async def upsert_user(
    conn: db.Connection,
    *,
    sub: str,
    email: str | None,
    name: str | None,
    picture: str | None,
    hd: str | None = None,
) -> Principal:
    """Create or refresh the user for an identity, create their workspace on first sight, and when `hd` names a
    Google Workspace domain make them a member of that organisation (created on first sight too); return the
    principal. Called at every sign-in, through either door."""
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
    if hd:
        organization_id: uuid.UUID = await conn.fetchval(
            'INSERT INTO organizations (id, domain, name) VALUES ($1, $2, $2) '
            'ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain RETURNING id',
            uuid.uuid4(),
            hd,
        )
        await conn.execute(
            'INSERT INTO organization_members (organization_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING',
            organization_id,
            user_id,
        )
    return await principal_for(conn, user_id, email, name, picture)


async def load_principal(conn: db.Connection, user_id: uuid.UUID) -> Principal | None:
    """The principal for a user id (a browser session names one); None when the user no longer exists."""
    row = await conn.fetchrow('SELECT email, name, picture FROM users WHERE id = $1', user_id)
    if row is None:
        return None
    return await principal_for(conn, user_id, row['email'], row['name'], row['picture'])


async def current_principal() -> Principal:
    """The caller of the current tool, from the verified access token; `ToolError` when there is none.

    Not cached: organisation membership is settled at sign-in and may change between calls, and the upsert is one
    short transaction next to the upstream verification FastMCP already does per request.
    """
    if _override is not None:
        return _override
    token = get_access_token()
    if token is None:
        raise ToolError('not authenticated')
    claims = token.claims
    sub = str(claims.get('sub') or token.client_id)
    async with db.pool().acquire() as conn, conn.transaction():
        return await upsert_user(
            conn,
            sub=sub,
            email=cast('str | None', claims.get('email')),
            name=cast('str | None', claims.get('name')),
            picture=cast('str | None', claims.get('picture')),
            hd=hosted_domain(claims),
        )
