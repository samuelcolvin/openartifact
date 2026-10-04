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
