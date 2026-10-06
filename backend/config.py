"""Environment-driven settings shared by more than one module. Each accessor reads the environment when called,
so tests can change a value with `monkeypatch.setenv` and nothing is cached at import time."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def base_url() -> str:
    """Public URL of the server, from `OPENARTIFACT_BASE_URL`; defaults to uvicorn's local default."""
    return (os.environ.get('OPENARTIFACT_BASE_URL') or 'http://127.0.0.1:8765').rstrip('/')


def cache_dir() -> Path:
    """Where workspace checkouts live, from `OPENARTIFACT_CACHE_DIR`; one directory per process, never shared."""
    return Path(os.environ.get('OPENARTIFACT_CACHE_DIR') or ROOT / 'data' / 'cache').resolve()


def render_url() -> str | None:
    """The PDF render service (`render/`), from `OPENARTIFACT_RENDER_URL`; `None` means PDF export is not set up."""
    url = os.environ.get('OPENARTIFACT_RENDER_URL')
    return url.rstrip('/') if url else None


def internal_url() -> str:
    """This server as the render service reaches it, from `OPENARTIFACT_INTERNAL_URL`; the public URL by default.

    In compose the render container reaches the app at `http://app:8765`, not at the address a browser uses.
    """
    return (os.environ.get('OPENARTIFACT_INTERNAL_URL') or base_url()).rstrip('/')


def secret_key() -> bytes:
    """The server's secret, for signing upload URLs: `OPENARTIFACT_SECRET_KEY`, else the development token.

    With Google login the key is required anyway (see `auth.py`); in development the dev token is the one secret
    there is, so it doubles as the key rather than demanding a second variable.
    """
    key = os.environ.get('OPENARTIFACT_SECRET_KEY') or os.environ.get('OPENARTIFACT_DEV_TOKEN')
    if not key:
        raise RuntimeError('OPENARTIFACT_SECRET_KEY or OPENARTIFACT_DEV_TOKEN is required to sign upload URLs')
    return key.encode()
