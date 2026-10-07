"""Stored editing conversations: the pydantic-ai message history of one user's chat about one artifact.

The server owns the history. The browser sends only the new user message each turn (`api.py`), the agent runs
with the stored history, and the whole history is written back when the turn completes. A turn that fails or is
stopped is not stored.
"""

from __future__ import annotations

import uuid

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

import db


async def load(artifact_id: uuid.UUID, user_id: uuid.UUID) -> tuple[list[ModelMessage], str | None]:
    """The stored history and the model it last ran with; empty for a conversation that has not started."""
    row = await db.pool().fetchrow(
        'SELECT messages, model FROM chats WHERE artifact_id = $1 AND user_id = $2', artifact_id, user_id
    )
    if row is None:
        return [], None
    return list(ModelMessagesTypeAdapter.validate_json(row['messages'])), row['model']


async def save(artifact_id: uuid.UUID, user_id: uuid.UUID, messages: list[ModelMessage], model: str | None) -> None:
    """Replace the stored history after a completed turn."""
    body = ModelMessagesTypeAdapter.dump_json(messages).decode()
    await db.pool().execute(
        'INSERT INTO chats (artifact_id, user_id, messages, model) VALUES ($1, $2, $3::jsonb, $4) '
        'ON CONFLICT (artifact_id, user_id) DO UPDATE SET messages = EXCLUDED.messages, model = EXCLUDED.model, '
        'updated_at = now()',
        artifact_id,
        user_id,
        body,
        model,
    )


async def clear(artifact_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Forget the conversation."""
    await db.pool().execute('DELETE FROM chats WHERE artifact_id = $1 AND user_id = $2', artifact_id, user_id)
