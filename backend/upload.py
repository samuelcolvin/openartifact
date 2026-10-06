"""Signed upload URLs: how an agent moves files into an artifact without passing their bytes through a tool call.

The `upload_url` tool (`mcp_server.py`) mints one URL per file, `PUT /artifacts/{id}/{path}?token=...`, and the
route in `server.py` accepts it. The token is an HMAC over the artifact, the path, the exact size and an expiry,
so the server keeps no record of what was minted: it recomputes the signature from the request and compares. A
URL stays valid until it expires, which only lets its holder write the same path again with the same size.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid

import config

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
    """URL-safe HMAC-SHA256 of the four values, keyed by a key derived from the server secret for this purpose."""
    key = hmac.new(config.secret_key(), b'openartifact upload url', hashlib.sha256).digest()
    message = f'{artifact_id}\n{path}\n{size}\n{expires}'.encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


def make_token(artifact_id: uuid.UUID, path: str, size: int, expires: int) -> str:
    """The `token` query parameter: the expiry (unix seconds) and the signature, dot separated."""
    return f'{expires}.{signature(artifact_id, path, size, expires)}'


def verify_token(token: str, artifact_id: uuid.UUID, path: str, size: int, now: float | None = None) -> None:
    """Raise `UploadError` unless `token` signs exactly these values and has not expired."""
    expires_text, _, given = token.partition('.')
    try:
        expires = int(expires_text)
    except ValueError:
        raise UploadError('malformed upload token') from None
    if not hmac.compare_digest(given, signature(artifact_id, path, size, expires)):
        raise UploadError('the upload token does not match this artifact, path and size')
    if expires < (time.time() if now is None else now):
        raise UploadError('the upload URL has expired; ask for a new one with `upload_url`')
