"""Browser sign-in: Google OAuth for viewers, a signed session cookie, and the development sign-in.

The MCP endpoint is behind FastMCP's OAuth proxy, which issues bearer tokens to MCP clients and keeps no browser
session. Viewers of artifact pages sign in here instead, with the same Google OAuth client (a second redirect
URI, `/login/callback`, next to the proxy's `/auth/callback`), and land in the same `users` row and
`auth.Principal` as the MCP side, organisation membership included.

Routes: `GET /login` (the page), `GET /login/google` (to Google), `GET /login/callback` (back from Google),
`POST /logout`, and `POST /login/dev` when Google is not configured. The session is `oa_session`, a signed
`user_id.expires.signature` (`signing.py`), 30 days, HttpOnly and SameSite=Lax, Secure on https. The in-flight
sign-in is `oa_login`, a signed, ten-minute cookie holding the OAuth `state`, the PKCE verifier and where to go
afterwards, so `next` is never taken from the callback's query string.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

import httpx2
import pages
import signing
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

import auth
import db
from config import base_url

router = APIRouter()

SESSION_COOKIE = 'oa_session'
SESSION_TTL = 30 * 86400
SESSION_PURPOSE = b'openartifact session'
LOGIN_COOKIE = 'oa_login'
LOGIN_TTL = 600
LOGIN_PURPOSE = b'openartifact login state'

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'
GOOGLE_SCOPE = 'openid email profile'

# A `next` must be a path on this server: one leading slash, no scheme or host, no control characters.
SAFE_NEXT_RE = re.compile(r'^/(?![/\\])[^\x00-\x1f\x7f]*$')


def google_config() -> tuple[str, str] | None:
    """The Google OAuth client, from `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`; None without them."""
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    return (client_id, secret) if client_id and secret else None


def dev_login_enabled() -> bool:
    """The development sign-in exists only where Google is not configured and the dev token is."""
    return google_config() is None and bool(os.environ.get('OPENARTIFACT_DEV_TOKEN'))


def google_client() -> httpx2.AsyncClient:
    """The HTTP client for Google's token and userinfo endpoints; tests swap it for one wired to a stub."""
    return httpx2.AsyncClient(timeout=20)


def safe_next(value: str | None) -> str:
    """`value` when it is a path on this server, else `/`: a `next` can never send the browser elsewhere."""
    return value if value and SAFE_NEXT_RE.match(value) else '/'


def same_origin(request: Request) -> bool:
    """Whether a state-changing request came from a page of ours, by `Sec-Fetch-Site`, else `Origin`.

    Neither header means a non-browser client, which the Lax session cookie already keeps out of cross-site
    posts; this is the second lock.
    """
    site = request.headers.get('sec-fetch-site')
    if site is not None:
        return site in ('same-origin', 'none')
    origin = request.headers.get('origin')
    if origin is None:
        return True
    ours = urlsplit(base_url())
    theirs = urlsplit(origin)
    return (theirs.scheme, theirs.netloc) == (ours.scheme, ours.netloc)


def secure_cookies() -> bool:
    return base_url().startswith('https://')


def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path='/', httponly=True, samesite='lax', secure=secure_cookies())


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path='/', httponly=True, samesite='lax', secure=secure_cookies())


def make_session(user_id: uuid.UUID, expires: int) -> str:
    """The session cookie's value."""
    return f'{user_id}.{signing.token(SESSION_PURPOSE, str(user_id), expires=expires)}'


def read_session(value: str | None, now: float | None = None) -> uuid.UUID | None:
    """The user a session cookie names, or None for a missing, forged or expired one."""
    if not value:
        return None
    user_text, _, token = value.partition('.')
    try:
        user_id = uuid.UUID(user_text)
        signing.verify(SESSION_PURPOSE, token, str(user_id), now=now)
    except (ValueError, signing.SignatureError):
        return None
    return user_id


def encode_state(data: dict[str, Any], expires: int) -> str:
    """The `oa_login` cookie's value: the JSON, base64url, then its token."""
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()
    return f'{payload}.{signing.token(LOGIN_PURPOSE, payload, expires=expires)}'


def decode_state(value: str | None, now: float | None = None) -> dict[str, Any] | None:
    """The data behind an `oa_login` cookie, or None when it is missing, forged or expired."""
    if not value:
        return None
    payload, _, token = value.partition('.')
    try:
        signing.verify(LOGIN_PURPOSE, token, payload, now=now)
        data: object = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (ValueError, signing.SignatureError):
        return None
    return cast('dict[str, Any]', data) if isinstance(data, dict) else None


async def sign_in(response: Response, principal: auth.Principal) -> None:
    """Attach a fresh session for `principal` to `response`."""
    set_cookie(response, SESSION_COOKIE, make_session(principal.user_id, int(time.time()) + SESSION_TTL), SESSION_TTL)


async def current_viewer(request: Request) -> auth.Principal | None:
    """Who the browser is signed in as, from the session cookie; None for a visitor. Memoised per request."""
    if hasattr(request.state, 'viewer'):
        return request.state.viewer
    viewer: auth.Principal | None = None
    user_id = read_session(request.cookies.get(SESSION_COOKIE))
    if user_id is not None:
        async with db.pool().acquire() as conn, conn.transaction():
            viewer = await auth.load_principal(conn, user_id)
    request.state.viewer = viewer
    return viewer


def login_url(next_path: str) -> str:
    return '/login?' + urlencode({'next': safe_next(next_path)})


@router.get('/login')
async def login_page(next: str | None = None) -> Response:
    """The sign-in page."""
    return pages.page_response(
        'Sign in', pages.login_html(safe_next(next), google=google_config() is not None, dev=dev_login_enabled())
    )


@router.get('/login/google')
async def login_google(next: str | None = None) -> Response:
    """Start the Google sign-in: remember state, PKCE and the destination, then go to Google."""
    google = google_config()
    if google is None:
        raise HTTPException(404, 'Google sign-in is not configured')
    client_id, _ = google
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    expires = int(time.time()) + LOGIN_TTL
    params = {
        'client_id': client_id,
        'redirect_uri': f'{base_url()}/login/callback',
        'response_type': 'code',
        'scope': GOOGLE_SCOPE,
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'prompt': 'select_account',
    }
    response = RedirectResponse(f'{GOOGLE_AUTH_URL}?{urlencode(params)}', status_code=303)
    data = {'state': state, 'verifier': verifier, 'next': safe_next(next)}
    set_cookie(response, LOGIN_COOKIE, encode_state(data, expires), LOGIN_TTL)
    return response


def login_failed(message: str) -> Response:
    body = (
        '<h1>Sign-in did not complete</h1>'
        f'<p>{pages._e(message)}</p>'  # pyright: ignore[reportPrivateUsage]
        '<a class="button primary" href="/login">Try again</a>'
    )
    return pages.page_response('Sign-in failed', body, status=400)


@router.get('/login/callback')
async def login_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> Response:
    """Back from Google: check the state, trade the code for the identity, start the session."""
    google = google_config()
    if google is None:
        raise HTTPException(404, 'Google sign-in is not configured')
    client_id, client_secret = google
    pending = decode_state(request.cookies.get(LOGIN_COOKIE))
    if pending is None or not state or not secrets.compare_digest(str(pending.get('state', '')), state):
        return login_failed('the sign-in attempt has expired or did not start here; please start again.')
    if error or not code:
        return login_failed(f'Google reported {error or "no authorization code"}.')
    form = {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': f'{base_url()}/login/callback',
        'grant_type': 'authorization_code',
        'code_verifier': str(pending.get('verifier', '')),
    }
    try:
        async with google_client() as client:
            exchanged = await client.post(GOOGLE_TOKEN_URL, data=form)
            if exchanged.status_code != 200:
                return login_failed(f'Google did not accept the sign-in ({exchanged.status_code}).')
            access_token = exchanged.json().get('access_token')
            if not isinstance(access_token, str):
                return login_failed('Google returned no access token.')
            fetched = await client.get(GOOGLE_USERINFO_URL, headers={'Authorization': f'Bearer {access_token}'})
    except httpx2.HTTPError as exc:
        return login_failed(f'Google could not be reached: {exc}')
    if fetched.status_code != 200:
        return login_failed(f'Google did not return the account ({fetched.status_code}).')
    info: dict[str, Any] = fetched.json()
    sub = info.get('sub')
    if not isinstance(sub, str) or not sub:
        return login_failed('Google returned no account identifier.')
    async with db.pool().acquire() as conn, conn.transaction():
        principal = await auth.upsert_user(
            conn,
            sub=sub,
            email=info.get('email'),
            name=info.get('name'),
            picture=info.get('picture'),
            hd=auth.hosted_domain(info),
        )
    response = RedirectResponse(safe_next(str(pending.get('next', '/'))), status_code=303)
    await sign_in(response, principal)
    clear_cookie(response, LOGIN_COOKIE)
    return response


@router.post('/logout')
async def logout(request: Request) -> Response:
    """End the session and go back to where the form said."""
    if not same_origin(request):
        raise HTTPException(403, 'cross-site request')
    form = await request.form()
    response = RedirectResponse(safe_next(str(form.get('next', '/'))), status_code=303)
    clear_cookie(response, SESSION_COOKIE)
    return response


@router.post('/login/dev')
async def login_dev(request: Request) -> Response:
    """Sign in as the development user, optionally with an email and a Workspace domain to try organisations.

    Only where Google is not configured: the same user the MCP dev token acts as, so an agent and a browser
    share one identity locally.
    """
    if not dev_login_enabled():
        raise HTTPException(404, 'the development sign-in is not available')
    if not same_origin(request):
        raise HTTPException(403, 'cross-site request')
    form = await request.form()
    email = str(form.get('email') or auth.DEV_CLAIMS['email']).strip()
    hd = str(form.get('hd') or '').strip().lower() or None
    async with db.pool().acquire() as conn, conn.transaction():
        principal = await auth.upsert_user(
            conn, sub=auth.DEV_SUB, email=email, name=auth.DEV_CLAIMS['name'], picture=None, hd=hd
        )
    response = RedirectResponse(safe_next(str(form.get('next', '/'))), status_code=303)
    await sign_in(response, principal)
    return response
