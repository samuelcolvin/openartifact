-- One editing conversation per artifact per user, for the chat in the web app: the pydantic-ai message history
-- as JSON (`ModelMessagesTypeAdapter`), replaced wholesale after each completed turn, and the model last used.
create table chats (
    artifact_id uuid not null references artifacts (id) on delete cascade,
    user_id     uuid not null references users (id) on delete cascade,
    messages    jsonb not null default '[]',
    model       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (artifact_id, user_id)
);
