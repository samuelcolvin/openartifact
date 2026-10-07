"""The editing agent behind the web app's chat: a pydantic-ai agent whose tools are this server's own MCP tools.

The agent edits one artifact for one signed-in user. Its toolset is the FastMCP server from `mcp_server.py`,
called in-process through `MCPToolset`, narrowed to `run_code` and `build`: the agent changes files and
rebuilds the page, and nothing else (creating, forking and sharing stay with the user). The tools find out who is
calling through `auth.set_principal()`, which `api.py` sets inside the streamed response, so the agent's edits
are governed by the same permission rules as every other caller and committed to the same history.

Its instructions are the authoring guide the MCP skill serves (`SKILL.md` and the style and build-step
references) plus a per-run note naming the artifact and the user. The model comes from the browser's model
picker, validated against `configured_models()`; `resolve_model` is the seam tests replace with a scripted model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model
from pydantic_ai.tools import ToolDefinition

import auth
import mcp_server
import workspace

DEFAULT_MODEL = 'anthropic:claude-opus-5-5'
# What the picker offers when `OPENARTIFACT_MODELS` is not set, per provider key present in the environment.
BUILTIN_MODELS: dict[str, list[tuple[str, str]]] = {
    'ANTHROPIC_API_KEY': [
        ('anthropic:claude-opus-5-5', 'Claude Opus 5.5'),
        ('anthropic:claude-sonnet-5-5', 'Claude Sonnet 5.5'),
    ],
    'OPENAI_API_KEY': [('openai-responses:gpt-5.2', 'GPT-5.2')],
}
# Tool calls per turn before the run stops: an agent that cannot converge should not loop for ever.
REQUEST_LIMIT = 40
# The tools the agent may call; the rest of the MCP server (creating, forking, sharing, listing) stays with the user.
AGENT_TOOLS = frozenset({'run_code', 'build'})

PREAMBLE = """\
You are the OpenArtifact editing assistant. You help a user change one artifact: a deck, document or page built
from markdown, components and CSS. The user sees the artifact's page in a live preview beside this chat; it
refreshes whenever you call `build`.

How to work:

- Read before you write: use `run_code` to print `main.md`, `artifact.toml` and the other files before changing
  them, unless you have just written them yourself.
- Make the change with `run_code`, writing files with `pathlib`; pass long text through `inputs` rather than
  embedding it in the code. The sandbox runs a subset of Python: `Path.read_text()` and `Path.write_text()` take
  no keyword arguments, and there is no network.
- After every change, call `build`. A build error names the file and line: fix it and build again.
- Keep replies short and concrete. Say what you changed; do not paste whole files back into the chat.
- Never create, fork or delete artifacts, and never change who may see this one: the user does that themselves.

The authoring guide follows.
"""


def skill_text() -> str:
    """The authoring guide the MCP skill serves, as the agent's reference: the guide and two of its references."""
    parts = [(mcp_server.SKILL_DIR / 'SKILL.md').read_text(encoding='utf-8')]
    for name in ('styles.md', 'steps.md'):
        parts.append((mcp_server.SKILL_DIR / 'references' / name).read_text(encoding='utf-8'))
    return '\n\n'.join(parts)


@dataclass(frozen=True)
class ChatDeps:
    """What one run is about: the artifact being edited and the user asking."""

    artifact: workspace.Artifact
    viewer: auth.Principal


@dataclass(frozen=True)
class ModelChoice:
    """One entry of the model picker: the pydantic-ai model name and a label."""

    id: str
    name: str


def configured_models() -> list[ModelChoice]:
    """The models the picker offers: `OPENARTIFACT_MODELS` (`id=Name,id=Name`), else the built-ins whose provider
    key is set. The first is the default unless `DEFAULT_MODEL` is among them."""
    configured = os.environ.get('OPENARTIFACT_MODELS')
    if configured:
        choices: list[ModelChoice] = []
        for entry in configured.split(','):
            model_id, _, name = entry.strip().partition('=')
            model_id = model_id.strip()
            if model_id:
                choices.append(ModelChoice(model_id, name.strip() or model_id))
        return choices
    return [
        ModelChoice(model_id, name)
        for key, models in BUILTIN_MODELS.items()
        if os.environ.get(key)
        for model_id, name in models
    ]


def default_model() -> str | None:
    """The model used when the browser names none."""
    choices = configured_models()
    ids = [choice.id for choice in choices]
    if DEFAULT_MODEL in ids:
        return DEFAULT_MODEL
    return ids[0] if ids else None


def check_model(model_id: str | None) -> str:
    """The model to run: `model_id` when it is configured, the default when None; `ValueError` otherwise."""
    if model_id is None:
        chosen = default_model()
        if chosen is None:
            raise ValueError(
                'no model is configured: set an API key (ANTHROPIC_API_KEY, OPENAI_API_KEY) or OPENARTIFACT_MODELS'
            )
        return chosen
    if model_id not in {choice.id for choice in configured_models()}:
        raise ValueError(f'unknown model {model_id!r}')
    return model_id


def resolve_model(model_id: str) -> Model | str:
    """The model pydantic-ai runs for a configured id. Tests replace this with a scripted model."""
    return model_id


def agent_tool(ctx: RunContext[Any], tool: ToolDefinition) -> bool:
    return tool.name in AGENT_TOOLS


# The server's own tools, in-process: no HTTP, no token; the caller is whoever `auth.set_principal` named.
toolset = MCPToolset(mcp_server.mcp, max_retries=8).filtered(agent_tool)

agent: Agent[ChatDeps, str] = Agent(
    model=None,
    deps_type=ChatDeps,
    instructions=[PREAMBLE, skill_text()],
    toolsets=[toolset],
    name='openartifact-editor',
)


@agent.instructions
def about_this_run(ctx: RunContext[ChatDeps]) -> str:
    """What this conversation is about: the one artifact to edit, and who is asking."""
    artifact = ctx.deps.artifact
    who = ctx.deps.viewer.name or ctx.deps.viewer.email or 'the user'
    return (
        f'You are editing the artifact with id `{artifact.id}` (pass exactly this as the `artifact` argument of '
        f'every tool call): "{artifact.title}", a {artifact.type}. You are helping {who}. Work only on this '
        'artifact.'
    )
