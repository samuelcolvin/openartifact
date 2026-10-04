-- Users are created from identities the MCP auth layer verifies (Google today). `google_sub` is null only for
-- the development user that OPENARTIFACT_DEV_TOKEN signs in.
create table users (
    id            uuid primary key,
    google_sub    text unique,
    email         text,
    name          text,
    picture       text,
    created_at    timestamptz not null default now(),
    last_login_at timestamptz not null default now()
);

-- Reserved for the GitHub integration: one secret per (user, provider), stored as Fernet ciphertext under
-- OPENARTIFACT_SECRET_KEY. Nothing reads or writes it yet.
create table credentials (
    id         uuid primary key,
    user_id    uuid not null references users (id) on delete cascade,
    provider   text not null,
    secret     bytea not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, provider)
);

-- One git repository per workspace. `head_sha` is the commit whose bundle sits at
-- workspaces/<id>/<head_sha>.bundle in the object store; null until the first edit creates the repo.
-- Orgs will own workspaces later: add `owner_org_id` then and relax `owner_user_id` with a check constraint.
create table workspaces (
    id            uuid primary key,
    owner_user_id uuid not null references users (id),
    head_sha      text check (head_sha ~ '^[0-9a-f]{40,64}$'),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index workspaces_owner_idx on workspaces (owner_user_id);

-- The directory inside the workspace repo is artifacts/<id>/. `type` is validated by the application
-- (build.TYPES), so adding a type is not a migration.
create table artifacts (
    id           uuid primary key,
    workspace_id uuid not null references workspaces (id) on delete cascade,
    title        text not null,
    type         text not null,
    forked_from  uuid references artifacts (id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index artifacts_workspace_idx on artifacts (workspace_id, created_at);
