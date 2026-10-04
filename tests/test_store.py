"""Tests for `backend/store.py` against the file-backed store."""

from __future__ import annotations

from pathlib import Path

import pytest

import store

pytestmark = pytest.mark.anyio


async def test_round_trip(storage: store.ObjectStore):
    key = 'workspaces/abc/0123.bundle'
    assert not await storage.exists(key)
    await storage.put(key, b'bundle bytes')
    assert await storage.exists(key)
    assert await storage.get(key) == b'bundle bytes'
    await storage.put('workspaces/abc/4567.bundle', b'x')
    await storage.put('workspaces/other/0000.bundle', b'y')
    assert await storage.list('workspaces/abc/') == [key, 'workspaces/abc/4567.bundle']
    await storage.delete(key)
    assert not await storage.exists(key)
    assert await storage.list('workspaces/abc/') == ['workspaces/abc/4567.bundle']


async def test_missing_key(storage: store.ObjectStore):
    with pytest.raises(store.StoreMissing, match='nope'):
        await storage.get('nope')


async def test_file_store_creates_its_directory(tmp_path: Path):
    target = tmp_path / 'deep' / 'store'
    opened = store.ObjectStore(target.as_uri())
    await opened.put('k', b'v')
    assert (target / 'k').read_bytes() == b'v'


def test_store_not_open():
    with pytest.raises(RuntimeError, match='object store is not open'):
        store.store()
