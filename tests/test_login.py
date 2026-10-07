"""Tests for `backend/login.py`: the browser sign-in, the session cookie and the development sign-in."""

from __future__ import annotations

import functools
import json
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

import auth
import db
import login
import server
import workspace


@pytest.fixture
def client(server_env: None) -> Iterator[TestClient]:
    with TestClient(server.app) as client:
        yield client


def test_safe_next():
    assert login.safe_next('/artifacts/x/') == '/artifacts/x/'
    assert login.safe_next('/a?b=c#d') == '/a?b=c#d'
    for bad in (None, '', 'https://evil.test/', '//evil.test/', '/\\evil.test', 'artifacts/x', '/a\nb'):
        assert login.safe_next(bad) == '/', bad


def test_session_cookie_round_trip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('OPENARTIFACT_SECRET_KEY', 'k')
    principal_id = auth.uuid.uuid4()
    value = login.make_session(principal_id, expires=2_000)
    assert login.read_session(value, now=1_999) == principal_id
    assert login.read_session(value, now=2_001) is None
    assert login.read_session(value[:-1] + ('a' if value[-1] != 'a' else 'b')) is None
    assert login.read_session('not-a-uuid.1.x') is None
    assert login.read_session(None) is None
    monkeypatch.setenv('OPENARTIFACT_SECRET_KEY', 'other')
    assert login.read_session(value, now=0) is None


def test_login_page_and_dev_sign_in(client: TestClient):
    page = client.get('/login?next=/artifacts/abc/')
    assert page.status_code == 200
    assert 'Continue as the development user' in page.text and 'Continue with Google' not in page.text
    assert 'value="/artifacts/abc/"' in page.text
    # The development user signs in, optionally with a Workspace domain, and becomes the MCP dev user's identity.
    response = client.post(
        '/login/dev', data={'next': '/artifacts/abc/', 'email': 'me@x.test', 'hd': 'X.Test'}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers['location'] == '/artifacts/abc/'
    cookie = client.cookies.get(login.SESSION_COOKIE)
    assert cookie and login.read_session(cookie) is not None

    async def whoami() -> auth.Principal | None:
        async with db.pool().acquire() as conn:
            user_id = login.read_session(cookie)
            assert user_id
            return await auth.load_principal(conn, user_id)

    assert client.portal is not None
    me = client.portal.call(whoami)
    assert me is not None and me.email == 'me@x.test' and me.organization_id is not None
    org = client.portal.call(lambda: workspace.get_organization(me.organization_id or auth.uuid.uuid4()))
    assert org is not None and org.domain == 'x.test'
    # Cross-site posts are refused; so is an open redirect.
    assert client.post('/login/dev', data={}, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    out = client.post('/logout', data={'next': 'https://evil.test/'}, follow_redirects=False)
    assert out.status_code == 303 and out.headers['location'] == '/'
    assert not client.cookies.get(login.SESSION_COOKIE)


def test_dev_sign_in_is_off_with_google(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'secret')
    assert client.post('/login/dev', data={}).status_code == 404
    page = client.get('/login')
    assert 'Continue with Google' in page.text and 'development user' not in page.text


def google_stub(seen: list[dict[str, str]], userinfo: dict[str, object]) -> FastAPI:
    """Google's token and userinfo endpoints, recording the token request."""
    stub = FastAPI()

    @stub.post('/token')
    async def token(request: Request) -> Response:
        form = await request.form()
        seen.append({k: str(v) for k, v in form.items()})
        return JSONResponse({'access_token': 'ya29.test', 'token_type': 'Bearer'})

    @stub.get('/v1/userinfo')
    async def info(request: Request) -> Response:
        assert request.headers['authorization'] == 'Bearer ya29.test'
        return JSONResponse(userinfo)

    return stub


def test_google_sign_in_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'secret')
    seen: list[dict[str, str]] = []
    userinfo: dict[str, object] = {
        'sub': 'g-123',
        'email': 'samuel@pydantic.dev',
        'email_verified': True,
        'name': 'Samuel',
        'picture': 'https://lh3.test/p.png',
        'hd': 'pydantic.dev',
    }
    stub = google_stub(seen, userinfo)
    monkeypatch.setattr(
        login,
        'google_client',
        lambda: httpx2.AsyncClient(transport=httpx2.ASGITransport(app=stub), base_url='https://google.test'),
    )
    monkeypatch.setattr(login, 'GOOGLE_TOKEN_URL', 'https://google.test/token')
    monkeypatch.setattr(login, 'GOOGLE_USERINFO_URL', 'https://google.test/v1/userinfo')

    start = client.get('/login/google?next=/artifacts/abc/', follow_redirects=False)
    assert start.status_code == 303
    target = urlsplit(start.headers['location'])
    assert (target.scheme, target.netloc, target.path) == ('https', 'accounts.google.com', '/o/oauth2/v2/auth')
    params = parse_qs(target.query)
    assert params['redirect_uri'] == ['http://127.0.0.1:8765/login/callback']
    assert params['code_challenge_method'] == ['S256'] and 'access_type' not in params
    state = params['state'][0]
    pending = login.decode_state(client.cookies.get(login.LOGIN_COOKIE))
    assert pending and pending['state'] == state and pending['next'] == '/artifacts/abc/'

    # A wrong state is refused, and a `next` in the callback's query is ignored in favour of the cookie's.
    wrong = client.get('/login/callback?code=abc&state=nope', follow_redirects=False)
    assert wrong.status_code == 400 and 'did not start here' in wrong.text
    done = client.get(f'/login/callback?code=abc&state={state}&next=https://evil.test/', follow_redirects=False)
    assert done.status_code == 303 and done.headers['location'] == '/artifacts/abc/'
    assert seen[0]['code'] == 'abc' and seen[0]['code_verifier'] == pending['verifier']
    assert seen[0]['redirect_uri'] == 'http://127.0.0.1:8765/login/callback'
    assert not client.cookies.get(login.LOGIN_COOKIE)
    user_id = login.read_session(client.cookies.get(login.SESSION_COOKIE))
    assert user_id

    async def check() -> None:
        async with db.pool().acquire() as conn:
            me = await auth.load_principal(conn, user_id)
        assert me is not None and (me.email, me.name) == ('samuel@pydantic.dev', 'Samuel')
        assert me.picture == 'https://lh3.test/p.png'
        assert me.organization_id is not None
        org = await workspace.get_organization(me.organization_id)
        assert org is not None and org.domain == 'pydantic.dev'

    assert client.portal is not None
    client.portal.call(check)

    # A personal account (no `hd`) gets no organisation; an unverified `hd` is ignored.
    for info, expect_org in (
        ({'sub': 'g-2', 'email': 'a@gmail.test', 'email_verified': True}, False),
        ({'sub': 'g-3', 'email': 'b@y.test', 'email_verified': False, 'hd': 'y.test'}, False),
    ):
        userinfo.clear()
        userinfo.update(info)
        client.cookies.clear()
        start = client.get('/login/google', follow_redirects=False)
        state = parse_qs(urlsplit(start.headers['location']).query)['state'][0]
        assert client.get(f'/login/callback?code=x&state={state}', follow_redirects=False).status_code == 303
        uid = login.read_session(client.cookies.get(login.SESSION_COOKIE))
        assert uid
        assert bool(client.portal.call(functools.partial(orgs_of, uid))) is expect_org, info


async def orgs_of(user_id: auth.uuid.UUID) -> frozenset[auth.uuid.UUID]:
    async with db.pool().acquire() as conn:
        me = await auth.load_principal(conn, user_id)
    assert me
    return me.org_ids


def test_hosted_domain():
    assert auth.hosted_domain({'email_verified': True, 'hd': 'Pydantic.dev '}) == 'pydantic.dev'
    assert auth.hosted_domain({'email_verified': 'true', 'google_user_data': {'hd': 'x.test'}}) == 'x.test'
    assert auth.hosted_domain({'email_verified': True}) is None
    assert auth.hosted_domain({'email_verified': False, 'hd': 'x.test'}) is None
    assert auth.hosted_domain({'hd': 'x.test'}) is None
    assert auth.hosted_domain({'email_verified': True, 'hd': ''}) is None
    assert json.dumps(auth.DEV_CLAIMS)  # the dev claims carry no `hd`: the dev user joins an org only via /login/dev
