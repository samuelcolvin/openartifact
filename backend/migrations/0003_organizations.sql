-- Organisations come from Google Workspace accounts: the `hd` (hosted domain) claim names the domain, and every
-- verified account on that domain is a member. One row per domain; the name is the domain until there is a UI.
create table organizations (
    id         uuid primary key,
    domain     text not null unique,
    name       text not null,
    created_at timestamptz not null default now()
);

-- Membership is many-to-many so a user can belong to several organisations later; `role` is reserved.
create table organization_members (
    organization_id uuid not null references organizations (id) on delete cascade,
    user_id         uuid not null references users (id) on delete cascade,
    role            text not null default 'member',
    created_at      timestamptz not null default now(),
    primary key (organization_id, user_id)
);
create index organization_members_user_idx on organization_members (user_id);

-- Where an artifact lives and who may see or change it. A personal artifact (`organization_id` null) is private
-- or public; an organisation's artifact is visible to the organisation or public, and may be editable by it.
-- The owner (the workspace's user) always sees and edits. `on delete restrict`: the checks below would fail on
-- `set null`, and an organisation with artifacts should not vanish silently.
alter table artifacts
    add column organization_id uuid references organizations (id) on delete restrict,
    add column visibility text not null default 'private' check (visibility in ('private', 'org', 'public')),
    add column org_editable boolean not null default false,
    add constraint artifacts_personal_access check (organization_id is not null or (visibility <> 'org' and not org_editable)),
    add constraint artifacts_org_access check (organization_id is null or visibility <> 'private');
create index artifacts_organization_idx on artifacts (organization_id, created_at) where visibility <> 'private';
