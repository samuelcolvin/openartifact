"""Who may see, change or fork an artifact. Pure rules over the row's fields; nothing here touches a database.

An artifact lives in a personal space (`organization_id` is null) or in an organisation, and has a `visibility`:

- `private`: only the owner (personal artifacts only)
- `org`: everyone in the organisation can see, download and fork it
- `public`: anyone can see, download and (signed in) fork it

`org_editable` lets everyone in the organisation edit as well (organisation artifacts only, never private). The
owner, the user whose workspace holds the artifact, can always see, edit and manage it. The same checks live in
the database (`0003_organizations.sql`); `check_access` repeats them to explain a bad combination before SQL does.
"""

from __future__ import annotations

import uuid
from typing import Literal, Protocol

Visibility = Literal['private', 'org', 'public']
VISIBILITIES: tuple[str, ...] = ('private', 'org', 'public')


class Viewer(Protocol):
    """Who is asking: `auth.Principal` satisfies this structurally."""

    @property
    def user_id(self) -> uuid.UUID: ...
    @property
    def workspace_id(self) -> uuid.UUID: ...
    @property
    def org_ids(self) -> frozenset[uuid.UUID]: ...


class Shared(Protocol):
    """What is asked about: `workspace.Artifact` satisfies this structurally."""

    @property
    def workspace_id(self) -> uuid.UUID: ...
    @property
    def visibility(self) -> str: ...
    @property
    def org_editable(self) -> bool: ...
    @property
    def organization_id(self) -> uuid.UUID | None: ...


def is_owner(artifact: Shared, viewer: Viewer | None) -> bool:
    """The artifact is in the viewer's workspace."""
    return viewer is not None and artifact.workspace_id == viewer.workspace_id


def in_org(artifact: Shared, viewer: Viewer | None) -> bool:
    """The viewer belongs to the organisation the artifact lives in."""
    return viewer is not None and artifact.organization_id is not None and artifact.organization_id in viewer.org_ids


def can_view(artifact: Shared, viewer: Viewer | None) -> bool:
    """See the page, its sources and exports."""
    if artifact.visibility == 'public' or is_owner(artifact, viewer):
        return True
    return artifact.visibility == 'org' and in_org(artifact, viewer)


def can_edit(artifact: Shared, viewer: Viewer | None) -> bool:
    """Change the files: the owner, or the organisation when the artifact is editable by it."""
    return is_owner(artifact, viewer) or (artifact.org_editable and in_org(artifact, viewer))


def can_fork(artifact: Shared, viewer: Viewer | None) -> bool:
    """Copy the artifact into one's own space: anyone signed in who can see it."""
    return viewer is not None and can_view(artifact, viewer)


def can_manage(artifact: Shared, viewer: Viewer | None) -> bool:
    """Change who may see or edit it: the owner alone."""
    return is_owner(artifact, viewer)


def check_access(visibility: str, org_editable: bool, organization_id: uuid.UUID | None) -> str | None:
    """Why a combination of placement and permissions is invalid, or None when it is fine."""
    if visibility not in VISIBILITIES:
        return f'visibility must be one of {", ".join(VISIBILITIES)}, not {visibility!r}'
    if organization_id is None:
        if visibility == 'org':
            return 'a personal artifact cannot be visible to an organisation; create it as an organisation artifact'
        if org_editable:
            return 'a personal artifact cannot be editable by an organisation; create it as an organisation artifact'
    elif visibility == 'private':
        return "an organisation's artifact is at least visible to the organisation; it cannot be private"
    return None
