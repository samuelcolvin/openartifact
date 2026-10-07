-- One git repository per artifact instead of one per workspace: `head_sha` moves to the artifact, and its bundle
-- sits at artifacts/<id>/<head_sha>.bundle in the object store. A workspace is now an owner, not a repository.
-- Workspace bundles made before this migration are not converted (a development-time change): the artifacts
-- they held start again with no head and no files.
alter table artifacts add column head_sha text check (head_sha ~ '^[0-9a-f]{40,64}$');
alter table workspaces drop column head_sha;
