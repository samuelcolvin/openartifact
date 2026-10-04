"""The object store that holds workspace bundles at rest.

A thin wrapper over obstore so the rest of the code sees five operations on whole objects and the client can be
swapped. The store is chosen by URL (`OPENARTIFACT_STORE_URL`): `file:///...` for local development and tests,
`s3://bucket/prefix` (or any scheme obstore knows) in production. Keys are immutable: a bundle is written once
under a key containing its commit sha and never overwritten, which is why no conditional writes are needed and a
plain filesystem store is enough locally.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import logfire
import obstore
import obstore.store

if TYPE_CHECKING:
    # Only in obstore's stubs, not importable at runtime.
    from obstore import ObjectMeta

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE_URL = (ROOT / 'data' / 'store').as_uri()


class StoreMissing(Exception):
    """`get` of a key that does not exist."""


def store_url() -> str:
    """Where bundles live, from `OPENARTIFACT_STORE_URL`; defaults to `data/store/` in the repo."""
    return os.environ.get('OPENARTIFACT_STORE_URL') or DEFAULT_STORE_URL


class ObjectStore:
    """Whole-object get/put/exists/list/delete on an obstore store."""

    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith('file://'):
            # obstore's LocalStore wants the directory to exist already.
            Path(url.removeprefix('file://')).mkdir(parents=True, exist_ok=True)
        self._store = obstore.store.from_url(url)

    async def get(self, key: str) -> bytes:
        """The object's bytes; `StoreMissing` if there is none."""
        with logfire.span('store get {key}', key=key):
            # obstore maps a missing object to the builtin FileNotFoundError.
            try:
                result = await obstore.get_async(self._store, key)
            except FileNotFoundError as exc:
                raise StoreMissing(key) from exc
            return bytes(await result.bytes_async())

    async def put(self, key: str, data: bytes) -> None:
        """Write the object. Keys are treated as immutable by callers; this does not enforce it."""
        with logfire.span('store put {key}', key=key, size=len(data)):
            await obstore.put_async(self._store, key, data)

    async def exists(self, key: str) -> bool:
        with logfire.span('store head {key}', key=key):
            try:
                await obstore.head_async(self._store, key)
            except FileNotFoundError:
                return False
            return True

    async def list(self, prefix: str) -> list[str]:
        """Keys under `prefix`, sorted."""
        with logfire.span('store list {prefix}', prefix=prefix):
            stream = obstore.list(self._store, prefix)  # pyright: ignore[reportUnknownMemberType]
            items: Sequence[ObjectMeta] = await stream.collect_async()
            return sorted(item['path'] for item in items)

    async def delete(self, key: str) -> None:
        with logfire.span('store delete {key}', key=key):
            await obstore.delete_async(self._store, key)


_store: ObjectStore | None = None


@contextlib.asynccontextmanager
async def object_store() -> AsyncGenerator[ObjectStore]:
    """Open the store for the duration of the block and publish it to `store()`."""
    global _store
    opened = ObjectStore(store_url())
    _store = opened
    try:
        yield opened
    finally:
        _store = None


def store() -> ObjectStore:
    """The store opened by `object_store()`."""
    if _store is None:
        raise RuntimeError('the object store is not open: enter `object_store()` first')
    return _store
