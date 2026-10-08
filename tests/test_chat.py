"""Tests for the editing chat (`api.py` + `agent.py` + `chats.py`): a scripted model drives the real agent, whose
tools are the in-process MCP server acting as the signed-in user."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from test_server import STARTER, in_app, org_members, sign_in, starter

import agent
import chats
import server
import workspace


@pytest.fixture
def client(server_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv('OPENARTIFACT_MODELS', 'test=Scripted')
    with TestClient(server.app) as client:
        yield client


def tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]


def scripted(new_markdown: str) -> FunctionModel:
    """A model that rewrites main.md with `run_code`, builds, then answers; on later turns it just answers."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = tool_returns(messages)
        # Only this turn's tool returns count: those after the last user prompt.
        last_user = max(
            (
                i
                for i, m in enumerate(messages)
                if isinstance(m, ModelRequest) and any(p.part_kind == 'user-prompt' for p in m.parts)
            ),
            default=-1,
        )
        this_turn = tool_returns(messages[last_user:])
        first_turn = len(returns) == len(this_turn)
        artifact_id = None
        for part in info.instructions.split('`') if info.instructions else []:
            if len(part) == 36 and part.count('-') == 4:
                artifact_id = part
        assert artifact_id, info.instructions
        if first_turn and not this_turn:
            args = {
                'artifact': artifact_id,
                'code': "from pathlib import Path\nPath('main.md').write_text(md)\nprint('written')",
                'inputs': {'md': new_markdown},
            }
            yield {0: DeltaToolCall(name='run_code', json_args=json.dumps(args))}
        elif first_turn and len(this_turn) == 1:
            yield {0: DeltaToolCall(name='build', json_args=json.dumps({'artifact': artifact_id}))}
        else:
            yield f'Done: I rewrote the deck ({len(messages)} messages so far).'

    return FunctionModel(stream_function=stream)


def use_model(monkeypatch: pytest.MonkeyPatch, model: FunctionModel) -> None:
    """Make every chat turn run `model`, whatever id the browser picked."""

    def resolve(model_id: str) -> FunctionModel:
        return model

    monkeypatch.setattr(agent, 'resolve_model', resolve)


def send(client: TestClient, artifact_id: Any, text: str, model: str | None = 'test') -> list[dict[str, Any]]:
    """One turn, as the browser sends it: the new message only, plus the model; returns the parsed SSE chunks."""
    body: dict[str, Any] = {
        'id': 'chat-1',
        'trigger': 'submit-message',
        'messages': [{'id': 'u1', 'role': 'user', 'parts': [{'type': 'text', 'text': text}]}],
    }
    if model is not None:
        body['model'] = model
    response = client.post(f'/api/artifacts/{artifact_id}/chat', json=body)
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith('data: ') and line != 'data: [DONE]'
    ]


def test_chat_edits_the_artifact_as_the_viewer(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)  # private: only the owner can edit it, so the tool must see the owner
    use_model(monkeypatch, scripted('# New deck\n\n---\n\n# Page two\n'))
    chunks = send(client, artifact.id, 'Rewrite the deck')
    kinds = [c['type'] for c in chunks]
    assert 'tool-input-start' in kinds and 'tool-output-available' in kinds and 'text-delta' in kinds
    assert kinds[-1] == 'finish' and chunks[-1].get('finishReason', 'stop') == 'stop'
    started = [c for c in chunks if c['type'] == 'tool-input-start']
    assert [c['toolName'] for c in started] == ['run_code', 'build']
    outputs = [c for c in chunks if c['type'] == 'tool-output-available']
    assert outputs[0]['output'] == 'written\n' and 'page:' in outputs[1]['output']
    text = ''.join(c['delta'] for c in chunks if c['type'] == 'text-delta')
    assert text.startswith('Done: I rewrote the deck')
    # The files changed, committed by the tool as the viewer's own edit; the page rebuilt.
    directory = workspace.checkout_path(artifact.id)
    assert (directory / 'main.md').read_text() == '# New deck\n\n---\n\n# Page two\n'
    log = in_app(client, lambda: workspace.artifact_git(artifact.id, 'log', '--format=%s')).splitlines()
    assert log[0] == f'run_code: {artifact.id}'
    assert (directory / 'dist' / 'index.html').is_file()
    # The conversation was stored and comes back as UI messages with the tool parts.
    stored = client.get(f'/api/artifacts/{artifact.id}/chat').json()
    assert stored['model'] == 'test'
    roles = [m['role'] for m in stored['messages']]
    assert roles == ['user', 'assistant']
    part_types = [p['type'] for p in stored['messages'][1]['parts']]
    assert 'tool-run_code' in part_types and 'tool-build' in part_types and 'text' in part_types
    run_code = next(p for p in stored['messages'][1]['parts'] if p['type'] == 'tool-run_code')
    assert run_code['state'] == 'output-available' and run_code['input']['inputs']['md'].startswith('# New deck')
    assert 'toolCallId' in run_code
    # A second turn runs with the stored history: the model sees it, and the history grows.
    chunks = send(client, artifact.id, 'Thanks')
    text = ''.join(c['delta'] for c in chunks if c['type'] == 'text-delta')
    owner_id = artifact_owner(client)
    before = len(in_app(client, lambda: chats.load(artifact.id, owner_id))[0])
    assert 'messages so far' in text and before >= 6
    assert [m['role'] for m in client.get(f'/api/artifacts/{artifact.id}/chat').json()['messages']] == [
        'user',
        'assistant',
        'user',
        'assistant',
    ]
    # Clear forgets it.
    assert client.delete(f'/api/artifacts/{artifact.id}/chat').status_code == 204
    assert client.get(f'/api/artifacts/{artifact.id}/chat').json() == {'messages': [], 'model': None}


def artifact_owner(client: TestClient) -> Any:
    from test_server import seed_starter

    return in_app(client, seed_starter)[0].user_id


def test_chat_tool_errors_are_reported_and_the_run_continues(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if not tool_returns(messages) and not any(
            p.part_kind == 'retry-prompt' for m in messages if isinstance(m, ModelRequest) for p in m.parts
        ):
            yield {
                0: DeltaToolCall(name='run_code', json_args=json.dumps({'artifact': str(artifact.id), 'code': '1/0'}))
            }
        else:
            yield 'That failed; nothing changed.'

    use_model(monkeypatch, FunctionModel(stream_function=stream))
    chunks = send(client, artifact.id, 'Break it')
    errors = [c for c in chunks if c['type'] == 'tool-output-error']
    assert errors and 'ZeroDivisionError' in errors[0]['errorText']
    assert chunks[-1]['type'] == 'finish'
    assert (workspace.checkout_path(artifact.id) / 'main.md').read_bytes() == (STARTER / 'main.md').read_bytes()


def test_chat_tells_the_agent_which_page_the_user_sees(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)
    seen: list[str | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        seen.append(info.instructions)
        yield 'ok'

    use_model(monkeypatch, FunctionModel(stream_function=stream))
    body: dict[str, Any] = {
        'id': 'c',
        'trigger': 'submit-message',
        'messages': [{'id': 'u', 'role': 'user', 'parts': [{'type': 'text', 'text': 'hi'}]}],
        'model': 'test',
    }
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json={**body, 'page': 3}).status_code == 200
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json=body).status_code == 200
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json={**body, 'page': 'x'}).status_code == 200
    with_page, without, junk = seen
    assert with_page and 'The user is looking at page 3 right now' in with_page
    assert without and 'looking at page' not in without
    assert junk and 'looking at page' not in junk


def test_first_turn_names_an_untitled_artifact(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    starter(client)  # signs in
    created = client.post('/api/artifacts', json={'type': 'deck'}).json()
    assert created['title'] == 'Untitled deck'
    naming_calls: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        yield 'Sure.'

    async def name(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # The naming agent runs unstreamed: it sees the request and the markdown, and answers a title to tidy.
        assert info.instructions and info.instructions.startswith('You name documents')
        prompt = messages[-1].parts[-1]
        assert isinstance(prompt, UserPromptPart) and isinstance(prompt.content, str)
        assert 'make a deck about pricing' in prompt.content and '# A new deck' in prompt.content
        naming_calls.append(prompt.content)
        return ModelResponse(parts=[TextPart('  "Pricing plans for 2026."  ')])

    use_model(monkeypatch, FunctionModel(function=name, stream_function=stream))
    assert send(client, created['id'], 'make a deck about pricing')[-1]['type'] == 'finish'
    mine = {a['id']: a['title'] for a in client.get('/api/artifacts').json()['mine']}
    assert mine[created['id']] == 'Pricing plans for 2026'
    directory = workspace.checkout_path(uuid.UUID(created['id']))
    assert (directory / 'artifact.toml').read_text().startswith('title = "Pricing plans for 2026"\n')
    log = in_app(client, lambda: workspace.artifact_git(uuid.UUID(created['id']), 'log', '--format=%s')).splitlines()
    assert log[0] == 'rename: Pricing plans for 2026'
    # Only the first turn names it, and only an artifact still carrying its placeholder title.
    assert send(client, created['id'], 'now add a slide')[-1]['type'] == 'finish'
    assert len(naming_calls) == 1
    titled = client.post('/api/artifacts', json={'type': 'page', 'title': 'Roadmap'}).json()
    assert send(client, titled['id'], 'hello')[-1]['type'] == 'finish'
    assert len(naming_calls) == 1
    assert {a['id']: a['title'] for a in client.get('/api/artifacts').json()['mine']}[titled['id']] == 'Roadmap'


def test_chat_access_and_model_checks(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    artifact = starter(client)
    colleague, outsider = org_members(client)
    use_model(monkeypatch, scripted('# x\n'))
    body = {
        'id': 'c',
        'trigger': 'submit-message',
        'messages': [{'id': 'u', 'role': 'user', 'parts': [{'type': 'text', 'text': 'hi'}]}],
    }
    # A model that is not configured; no model picks the default.
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json={**body, 'model': 'nope'}).status_code == 400
    assert send(client, artifact.id, 'hi', model=None)[-1]['type'] == 'finish'
    # Cross-site and non-JSON bodies are refused.
    assert (
        client.post(
            f'/api/artifacts/{artifact.id}/chat', json=body, headers={'sec-fetch-site': 'cross-site'}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f'/api/artifacts/{artifact.id}/chat',
            content='x=1',
            headers={'content-type': 'application/x-www-form-urlencoded'},
        ).status_code
        == 415
    )
    # A private artifact: others cannot even see the chat; an org-visible one can be read but not chatted about.
    sign_in(client, colleague)
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json=body).status_code == 404
    assert client.get(f'/api/artifacts/{artifact.id}/chat').status_code == 404
    in_app(
        client,
        lambda: workspace.set_access(artifact.id, visibility='public', org_editable=False, organization_id=None),
    )
    assert client.get(f'/api/artifacts/{artifact.id}/chat').json()['messages'] == []
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json=body).status_code == 403
    assert client.delete(f'/api/artifacts/{artifact.id}/chat').status_code == 403
    sign_in(client, outsider)
    assert client.post(f'/api/artifacts/{artifact.id}/chat', json=body).status_code == 403
