"""The JSON API behind the web app (`frontend/app/`): who is signed in, the artifact list, creation and sharing,
and the editing chat, which streams a pydantic-ai agent's turn in the Vercel AI SDK protocol.

Everything here is for a browser with a session cookie (`login.py`): a visitor gets a JSON 401, a user without
the right to see or change an artifact a 403. Mutations check `login.same_origin` on top of the Lax cookie.
Errors are JSON, never redirects: the callers are `fetch`, not navigations (the SPA shell routes in `server.py`
redirect).

The chat: the browser sends one new user message per turn; the server holds the conversation (`chats.py`),
runs the agent (`agent.py`) with the stored history, streams the events back, and stores the whole history when
the turn completes. The agent's tools are the MCP server's own, in-process, acting as the signed-in user: the
principal is set inside the streamed generator, because that is the task the agent and its MCP session run in,
after the route function has already returned the response.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, TypeAdapter
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import StepStartUIPart, UIMessage
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk
from pydantic_ai.usage import UsageLimits

import access
import agent
import auth
import chats
import login
import mcp_server
import workspace

router = APIRouter(prefix='/api')

UI_MESSAGES = TypeAdapter(list[UIMessage])


async def viewer_required(request: Request) -> auth.Principal:
    """The signed-in user, or a JSON 401."""
    viewer = await login.current_viewer(request)
    if viewer is None:
        raise HTTPException(401, 'sign in required')
    return viewer


def mutation(request: Request) -> None:
    """Refuse a state-changing request that did not come from a page of ours."""
    if not login.same_origin(request):
        raise HTTPException(403, 'cross-site request')


async def artifact_for(
    artifact_id: str, viewer: auth.Principal, *, edit: bool = False, manage: bool = False
) -> workspace.Artifact:
    """The artifact, if the viewer may see it (and edit or manage it, as asked); 404 or 403 otherwise."""
    try:
        parsed = uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(404, f'invalid artifact id {artifact_id!r}') from None
    found = await workspace.get_artifact(parsed)
    if found is None or not access.can_view(found, viewer):
        raise HTTPException(404, f'artifact {artifact_id} not found')
    if manage and not access.can_manage(found, viewer):
        raise HTTPException(403, 'only the owner can change who may see this artifact')
    if edit and not access.can_edit(found, viewer):
        raise HTTPException(403, 'this artifact is shared with you read-only')
    return found


def artifact_json(
    found: workspace.Artifact, viewer: auth.Principal, owner_email: str | None = None
) -> dict[str, object]:
    return {
        'id': str(found.id),
        'title': found.title,
        'type': found.type,
        'visibility': found.visibility,
        'org_editable': found.org_editable,
        'forked_from': str(found.forked_from) if found.forked_from else None,
        'created_at': found.created_at.isoformat(),
        'updated_at': found.updated_at.isoformat(),
        'owner_email': owner_email,
        'can_edit': access.can_edit(found, viewer),
        'can_manage': access.can_manage(found, viewer),
        'url': mcp_server.artifact_url(found.id),
    }


@router.get('/')
def index() -> dict[str, object]:
    """Where things are; the web app lives at `/`."""
    return {'mcp': '/mcp/', 'runtime': '/openartifact.js', 'login': '/login', 'app': '/'}


@router.get('/me')
async def me(request: Request) -> dict[str, object]:
    """The signed-in user and their organisation."""
    viewer = await viewer_required(request)
    organization = None
    if viewer.organization_id is not None:
        org = await workspace.get_organization(viewer.organization_id)
        organization = {'domain': org.domain, 'name': org.name} if org else None
    return {
        'viewer': {'name': viewer.name, 'email': viewer.email, 'picture': viewer.picture},
        'organization': organization,
    }


@router.get('/configure')
async def configure(request: Request) -> dict[str, object]:
    """The models the chat's picker offers, and the default."""
    await viewer_required(request)
    return {
        'models': [{'id': m.id, 'name': m.name} for m in agent.configured_models()],
        'default': agent.default_model(),
    }


@router.get('/artifacts')
async def list_artifacts(request: Request) -> dict[str, object]:
    """The viewer's own artifacts and the ones their organisation shares with them."""
    viewer = await viewer_required(request)
    mine = await workspace.list_artifacts(viewer.workspace_id)
    shared = await workspace.list_shared_artifacts(viewer.org_ids, viewer.workspace_id)
    return {
        'mine': [artifact_json(a, viewer, viewer.email) for a in mine],
        'shared': [artifact_json(a, viewer, owner) for a, owner in shared],
    }


class NewArtifactBody(BaseModel):
    title: str
    type: mcp_server.ArtifactType = 'deck'
    theme: mcp_server.Theme = 'light'
    content: str | None = None
    placement: Literal['personal', 'org'] = 'personal'
    public: bool = False
    org_editable: bool = False


@router.post('/artifacts', status_code=201)
async def create_artifact(body: NewArtifactBody, request: Request) -> dict[str, object]:
    """Create an artifact the way the MCP tools do, with a one-line placeholder unless content is given."""
    mutation(request)
    viewer = await viewer_required(request)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, 'a title is required')
    if body.placement == 'org':
        if viewer.organization_id is None:
            raise HTTPException(400, 'your account is not in an organisation')
        organization_id = viewer.organization_id
        visibility = 'public' if body.public else 'org'
    else:
        organization_id = None
        visibility = 'public' if body.public else 'private'
    try:
        artifact_id = await mcp_server.create_artifact(
            viewer,
            title=title,
            content=body.content if body.content is not None else f'# {title}\n',
            type=body.type,
            theme=body.theme,
            visibility=visibility,
            org_editable=body.org_editable if body.placement == 'org' else False,
            organization_id=organization_id,
        )
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    found = await workspace.get_artifact(artifact_id)
    assert found is not None
    return artifact_json(found, viewer, viewer.email)


class AccessBody(BaseModel):
    public: bool
    org_editable: bool = False


@router.patch('/artifacts/{artifact_id}')
async def set_access(artifact_id: str, body: AccessBody, request: Request) -> dict[str, object]:
    """Change who may see and edit an artifact; the owner only."""
    mutation(request)
    viewer = await viewer_required(request)
    found = await artifact_for(artifact_id, viewer, manage=True)
    if found.organization_id is None:
        visibility = 'public' if body.public else 'private'
    else:
        visibility = 'public' if body.public else 'org'
    problem = access.check_access(visibility, body.org_editable, found.organization_id)
    if problem is not None:
        raise HTTPException(400, problem)
    changed = await workspace.set_access(found.id, visibility=visibility, org_editable=body.org_editable)
    return artifact_json(changed, viewer, viewer.email)


def merge_turns(messages: list[UIMessage]) -> list[UIMessage]:
    """Fold consecutive assistant messages into one, as the live stream shows a turn.

    `dump_messages` makes one UI message per model response, so a turn with two tool calls and a reply is three
    assistant messages; `useChat` streams the same turn as one message with a `step-start` part between steps.
    Stored history should look like what the browser just saw.
    """
    merged: list[UIMessage] = []
    for message in messages:
        if merged and message.role == 'assistant' and merged[-1].role == 'assistant':
            previous = merged[-1]
            parts = [*previous.parts, StepStartUIPart(type='step-start'), *message.parts]
            merged[-1] = previous.model_copy(update={'parts': parts})
        else:
            merged.append(message)
    return merged


@router.get('/artifacts/{artifact_id}/chat')
async def get_chat(artifact_id: str, request: Request) -> dict[str, object]:
    """The viewer's conversation about this artifact, as the UI messages `useChat` starts from."""
    viewer = await viewer_required(request)
    found = await artifact_for(artifact_id, viewer)
    history, model = await chats.load(found.id, viewer.user_id)
    messages = merge_turns(VercelAIAdapter.dump_messages(history, sdk_version=7))
    return {
        'messages': UI_MESSAGES.dump_python(messages, by_alias=True, exclude_none=True, mode='json'),
        'model': model,
    }


@router.delete('/artifacts/{artifact_id}/chat', status_code=204)
async def clear_chat(artifact_id: str, request: Request) -> Response:
    """Forget the viewer's conversation about this artifact."""
    mutation(request)
    viewer = await viewer_required(request)
    found = await artifact_for(artifact_id, viewer, edit=True)
    await chats.clear(found.id, viewer.user_id)
    return Response(status_code=204)


@router.post('/artifacts/{artifact_id}/chat')
async def chat(artifact_id: str, request: Request) -> Response:
    """One turn of the editing conversation, streamed as Vercel AI SDK chunks.

    The body holds the new user message (the browser sends only that one) and `model`, the picker's choice. The
    agent runs with the stored history and the chosen model, acting as the viewer; when the turn completes the
    whole history is stored. The adapter accepts `application/json` only, a second lock against cross-site posts.
    """
    mutation(request)
    viewer = await viewer_required(request)
    found = await artifact_for(artifact_id, viewer, edit=True)
    adapter = await VercelAIAdapter[agent.ChatDeps, str].from_request(request, agent=agent.agent, sdk_version=7)
    extra: dict[str, Any] = adapter.run_input.__pydantic_extra__ or {}
    model_id = extra.get('model')
    try:
        chosen = agent.check_model(model_id if isinstance(model_id, str) else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    history, _ = await chats.load(found.id, viewer.user_id)

    async def save(result: AgentRunResult[Any]) -> None:
        await chats.save(found.id, viewer.user_id, result.all_messages(), chosen)

    async def events() -> AsyncIterator[BaseChunk]:
        # Set here, not around the call above: the body streams after this function has returned, in this task,
        # and the agent and its in-process MCP session run here too.
        token = auth.set_principal(viewer)
        try:
            async for event in adapter.run_stream(
                message_history=history,
                model=agent.resolve_model(chosen),
                deps=agent.ChatDeps(artifact=found, viewer=viewer),
                conversation_id=f'{found.id}:{viewer.user_id}',
                usage_limits=UsageLimits(request_limit=agent.REQUEST_LIMIT),
                on_complete=save,
            ):
                yield event
        finally:
            auth.reset_principal(token)

    return adapter.streaming_response(events())
