"""Signed upload URLs: how an agent moves files into an artifact without passing their bytes through a tool call.

The `upload_url` tool (`mcp_server.py`) mints one URL per file, `PUT /artifacts/{id}/{path}?token=...`, and the
route in `server.py` accepts it. The token is an HMAC over the artifact, the path, the exact size and an expiry,
so the server keeps no record of what was minted: it recomputes the signature from the request and compares. A
URL stays valid until it expires, which only lets its holder write the same path again with the same size.
"""

from __future__ import annotations

import uuid

import signing

# The key purpose: a session or print token never verifies as an upload token.
PURPOSE = b'openartifact upload url'
# One URL is good for an hour: agents are slow between asking for a URL and uploading to it.
TOKEN_TTL = 3600
# Per-file cap; the body is held in memory before it is written.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


class UploadError(ValueError):
    """A path, size or token that cannot be accepted; the message says why."""


def validate_path(path: str) -> str:
    """Check a path an agent wants to upload to and return it; `UploadError` says what is wrong.

    The path is used as-is under the artifact directory, so it must be relative and already normalised: no empty,
    `.` or `..` segments, no backslashes, no control characters (the token signs it on its own line), no `.git*`
    entries (they would hide files from the commit) and nothing under `dist/`, which is build output.
    """
    if not path:
        raise UploadError('path is empty')
    if any(ch < ' ' or ch == '\x7f' for ch in path):
        raise UploadError(f'{path!r}: control characters are not allowed in a path')
    if path.startswith('/') or '\\' in path:
        raise UploadError(f'{path!r}: a path is relative to the artifact directory, with / as the separator')
    segments = path.split('/')
    for segment in segments:
        if segment in ('', '.', '..'):
            raise UploadError(f'{path!r}: empty, . and .. segments are not allowed')
        if segment.startswith('.git'):
            raise UploadError(f'{path!r}: .git entries are not allowed')
    if segments[0] == 'dist':
        raise UploadError(f'{path!r}: dist/ is build output and cannot be uploaded to')
    return path


def validate_size(path: str, size: int) -> int:
    """Check a declared file size and return it; `UploadError` names the path when it is out of range."""
    if size < 0:
        raise UploadError(f'{path!r}: size must not be negative')
    if size > MAX_UPLOAD_SIZE:
        raise UploadError(f'{path!r}: {size:,} bytes is over the {MAX_UPLOAD_SIZE:,} byte limit')
    return size


def signature(artifact_id: uuid.UUID, path: str, size: int, expires: int) -> str:
    """URL-safe HMAC-SHA256 of the four values, keyed for this purpose (`signing.py`)."""
    return signing.sign(PURPOSE, str(artifact_id), path, str(size), str(expires))


def make_token(artifact_id: uuid.UUID, path: str, size: int, expires: int) -> str:
    """The `token` query parameter: the expiry (unix seconds) and the signature, dot separated."""
    return signing.token(PURPOSE, str(artifact_id), path, str(size), expires=expires)


def verify_token(token: str, artifact_id: uuid.UUID, path: str, size: int, now: float | None = None) -> None:
    """Raise `UploadError` unless `token` signs exactly these values and has not expired."""
    try:
        signing.verify(PURPOSE, token, str(artifact_id), path, str(size), now=now)
    except signing.SignatureError as exc:
        reason = str(exc)
        if 'malformed' in reason:
            raise UploadError('malformed upload token') from None
        if 'expired' in reason:
            raise UploadError('the upload URL has expired; ask for a new one with `upload_url`') from None
        raise UploadError('the upload token does not match this artifact, path and size') from None
