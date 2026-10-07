"""Tests for `backend/api.py`: the JSON API behind the web app, and the app shell routes in `server.py`."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_server import in_app, org_members, seed_starter, sign_in, sign_out, starter

import server
import workspace


@pytest.fixture
def client(server_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    shell = tmp_path / 'index.html'
    shell.write_text('<!doctype html><title>app</title><div id="root"></div>')
    monkeypatch.setattr(server, 'APP_INDEX', shell)
    with TestClient(server.app) as client:
        yield client


def test_shell_requires_sign_in(client: TestClient):
    for path in ('/', '/edit/abc'):
        anonymous = client.get(path, headers={'accept': 'text/html'}, follow_redirects=False)
        assert anonymous.status_code == 303 and anonymous.headers['location'].startswith('/login?next=')
    artifact = starter(client)
    for path in ('/', f'/edit/{artifact.id}'):
        page = client.get(path)
        assert page.status_code == 200 and 'id="root"' in page.text
        assert page.headers['cache-control'] == 'no-store'


def test_api_requires_sign_in(client: TestClient):
    assert client.get('/api/').status_code == 200
    for path in ('/api/me', '/api/configure', '/api/artifacts'):
        response = client.get(path)
        assert response.status_code == 401 and response.json() == {'detail': 'sign in required'}, path
    assert client.post('/api/artifacts', json={'title': 'x'}).status_code == 401


def test_me_and_configure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)
    colleague, _ = org_members(client)
    me = client.get('/api/me').json()
    assert me['viewer']['email'] == 'seed@x.test' and me['organization'] == {'domain': 'x.test', 'name': 'x.test'}
    sign_in(client, colleague)
    assert client.get('/api/me').json()['viewer']['name'] == 'C'
    monkeypatch.setenv('OPENARTIFACT_MODELS', 'gateway/anthropic:claude-opus-5-5=Opus, test:x = Test X')
    assert client.get('/api/configure').json() == {
        'models': [{'id': 'gateway/anthropic:claude-opus-5-5', 'name': 'Opus'}, {'id': 'test:x', 'name': 'Test X'}],
        'default': 'gateway/anthropic:claude-opus-5-5',
    }
    monkeypatch.setenv('OPENARTIFACT_MODELS', 'test:x=Test X')
    assert client.get('/api/configure').json()['default'] == 'test:x'
    monkeypatch.delenv('OPENARTIFACT_MODELS')
    monkeypatch.delenv('PYDANTIC_AI_GATEWAY_API_KEY', raising=False)
    assert client.get('/api/configure').json() == {'models': [], 'default': None}
    monkeypatch.setenv('PYDANTIC_AI_GATEWAY_API_KEY', 'pylf_v1_eu_test')
    configured = client.get('/api/configure').json()
    assert configured['default'] == 'gateway/anthropic:claude-opus-5-5'
    assert [m['id'] for m in configured['models']] == [
        'gateway/anthropic:claude-opus-5-5',
        'gateway/anthropic:claude-sonnet-5-5',
        'gateway/openai:gpt-6-astra',
        'gateway/openai:gpt-6.1-sol',
        'gateway/openai:gpt-6-luna',
    ]
    assert artifact.id


def test_list_create_and_access(client: TestClient):
    artifact = starter(client)
    colleague, outsider = org_members(client)
    listed = client.get('/api/artifacts').json()
    assert [a['id'] for a in listed['mine']] == [str(artifact.id)] and listed['shared'] == []
    mine = listed['mine'][0]
    assert (mine['visibility'], mine['can_edit'], mine['can_manage'], mine['owner_email']) == (
        'private',
        True,
        True,
        'seed@x.test',
    )
    assert mine['url'].endswith(f'/artifacts/{artifact.id}/')

    # Create: a personal public page, then an org one editable by the org; bad combinations are 400.
    created = client.post('/api/artifacts', json={'title': ' Notes ', 'type': 'page', 'public': True})
    assert created.status_code == 201, created.text
    notes = created.json()
    assert (notes['title'], notes['type'], notes['visibility']) == ('Notes', 'page', 'public')
    page = client.get(f'/artifacts/{notes["id"]}/main.md')
    assert page.text == '# Notes\n'
    team = client.post(
        '/api/artifacts',
        json={'title': 'Team', 'placement': 'org', 'org_editable': True, 'content': '# Team\n\nHello.\n'},
    ).json()
    assert (team['visibility'], team['org_editable']) == ('org', True)
    assert client.post('/api/artifacts', json={'title': '  '}).status_code == 400
    assert client.post('/api/artifacts', json={'title': 'T', 'type': 'scroll'}).status_code == 422
    cross = client.post('/api/artifacts', json={'title': 'T'}, headers={'sec-fetch-site': 'cross-site'})
    assert cross.status_code == 403

    # The colleague sees the org artifact as shared, with the owner's email; the outsider sees only the public one.
    sign_in(client, colleague)
    listed = client.get('/api/artifacts').json()
    assert listed['mine'] == []
    assert [(a['title'], a['owner_email'], a['can_edit']) for a in listed['shared']] == [('Team', 'seed@x.test', True)]
    sign_in(client, outsider)
    assert client.get('/api/artifacts').json() == {'mine': [], 'shared': []}
    assert client.post('/api/artifacts', json={'title': 'T', 'placement': 'org'}).status_code == 400

    # Access changes: owner only, validated.
    sign_in(client, colleague)
    assert client.patch(f'/api/artifacts/{team["id"]}', json={'public': True}).status_code == 403
    assert client.patch(f'/api/artifacts/{artifact.id}', json={'public': True}).status_code == 404
    sign_out(client)
    seed, _ = in_app(client, seed_starter)
    sign_in(client, seed)
    changed = client.patch(f'/api/artifacts/{team["id"]}', json={'public': True, 'org_editable': False}).json()
    assert (changed['visibility'], changed['org_editable']) == ('public', False)
    bad = client.patch(f'/api/artifacts/{notes["id"]}', json={'public': False, 'org_editable': True})
    assert bad.status_code == 400 and 'personal artifact' in bad.json()['detail']
    row = in_app(client, lambda: workspace.get_artifact(workspace.uuid.UUID(team['id'])))
    assert row is not None and row.visibility == 'public'
