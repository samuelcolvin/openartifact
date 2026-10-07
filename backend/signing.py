"""Signed, expiring tokens for upload URLs, browser sessions and print passes, keyed from the server secret.

Each use has its own purpose string, so a token minted for one purpose never verifies for another. A token is
`<expires>.<signature>`, the signature an HMAC-SHA256 over the purpose-derived key, the parts and the expiry,
URL-safe base64 without padding; the parts are length-prefixed so no two sequences of parts share a message. The server stores nothing: it recomputes and compares.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import config


class SignatureError(ValueError):
    """A token that is malformed, does not match, or has expired; the message says which."""


def sign(purpose: bytes, *parts: str) -> str:
    """The signature of `parts` for `purpose`."""
    key = hmac.new(config.secret_key(), purpose, hashlib.sha256).digest()
    # Length-prefixed parts, so ('a', 'b') and ('a\nb',) never sign the same message.
    message = ''.join(f'{len(part)}:{part}\n' for part in parts).encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


def token(purpose: bytes, *parts: str, expires: int) -> str:
    """A token over `parts` that verifies until `expires` (unix seconds)."""
    return f'{expires}.{sign(purpose, *parts, str(expires))}'


def verify(purpose: bytes, value: str, *parts: str, now: float | None = None) -> int:
    """Check `value` signs exactly `parts` for `purpose` and is not expired; returns the expiry."""
    expires_text, _, given = value.partition('.')
    try:
        expires = int(expires_text)
    except ValueError:
        raise SignatureError('malformed token') from None
    if not hmac.compare_digest(given, sign(purpose, *parts, str(expires))):
        raise SignatureError('the token does not match')
    if expires < (time.time() if now is None else now):
        raise SignatureError('the token has expired')
    return expires
