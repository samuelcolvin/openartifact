"""Tests for `backend/access.py`: the visibility rules, pure and without a database."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import access
import pytest

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
OWNER_WS = uuid.uuid4()


@dataclass(frozen=True)
class Who:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    org_ids: frozenset[uuid.UUID]


@dataclass(frozen=True)
class What:
    workspace_id: uuid.UUID
    visibility: str
    org_editable: bool
    organization_id: uuid.UUID | None


OWNER = Who(uuid.uuid4(), OWNER_WS, frozenset({ORG}))
MEMBER = Who(uuid.uuid4(), uuid.uuid4(), frozenset({ORG}))
OUTSIDER = Who(uuid.uuid4(), uuid.uuid4(), frozenset({OTHER_ORG}))
ANON = None


def artifact(visibility: str, org_editable: bool = False, org: uuid.UUID | None = ORG) -> What:
    return What(OWNER_WS, visibility, org_editable, org)


@pytest.mark.parametrize(
    ('what', 'viewer', 'view', 'edit', 'fork'),
    [
        # private, personal
        (artifact('private', org=None), OWNER, True, True, True),
        (artifact('private', org=None), MEMBER, False, False, False),
        (artifact('private', org=None), ANON, False, False, False),
        # org visible
        (artifact('org'), OWNER, True, True, True),
        (artifact('org'), MEMBER, True, False, True),
        (artifact('org'), OUTSIDER, False, False, False),
        (artifact('org'), ANON, False, False, False),
        # org editable
        (artifact('org', org_editable=True), MEMBER, True, True, True),
        (artifact('org', org_editable=True), OUTSIDER, False, False, False),
        # public
        (artifact('public', org=None), OWNER, True, True, True),
        (artifact('public', org=None), OUTSIDER, True, False, True),
        (artifact('public', org=None), ANON, True, False, False),
        # org editable and public
        (artifact('public', org_editable=True), MEMBER, True, True, True),
        (artifact('public', org_editable=True), OUTSIDER, True, False, True),
        (artifact('public', org_editable=True), ANON, True, False, False),
    ],
)
def test_rules(what: What, viewer: Who | None, view: bool, edit: bool, fork: bool):
    assert access.can_view(what, viewer) is view
    assert access.can_edit(what, viewer) is edit
    assert access.can_fork(what, viewer) is fork
    assert access.can_manage(what, viewer) is (viewer is OWNER)


def test_check_access():
    assert access.check_access('private', False, None) is None
    assert access.check_access('public', False, None) is None
    assert access.check_access('org', False, ORG) is None
    assert access.check_access('org', True, ORG) is None
    assert access.check_access('public', True, ORG) is None
    assert 'personal artifact cannot be visible' in (access.check_access('org', False, None) or '')
    assert 'personal artifact cannot be editable' in (access.check_access('public', True, None) or '')
    assert 'cannot be private' in (access.check_access('private', False, ORG) or '')
    assert 'visibility must be one of' in (access.check_access('secret', False, None) or '')
