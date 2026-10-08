"""Rendering an artifact's page through the chrome service: the PDF export and a screenshot of one page.

The chrome service (`chrome/`) runs Chrome headless against a URL this server serves. Chrome carries no session,
so it is sent to the print pass, `/print/{token}/artifacts/{id}/`, a short-lived signed URL (`print_token`) that
`server.py` answers with the page and its media. The page is built first, under the artifact lock, which is
released before the chrome service is called: Chrome fetches the page from this process, which takes the same
lock. Both `server.py` (the `.pdf` and `.png` routes) and `mcp_server.py` (the `screenshot` tool) call in here.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import httpx2

import build
import config
import signing
import workspace

PRINT_PURPOSE = b'openartifact print url'
# How long a print pass lives: the chrome service fetches the page within seconds of it being minted.
PRINT_TTL = 300
# The window a screenshot is taken in, per artifact type: a deck's 16:9 page fills it; a document's A4 sheet at
# 150 dpi; a page artifact's column with room to read.
VIEWPORTS: dict[str, tuple[int, int]] = {'deck': (1600, 900), 'document': (1240, 1754), 'page': (1280, 1600)}


class RenderError(Exception):
    """The chrome service is not configured (503), unreachable or failed (502); `status` is the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def chrome_client() -> httpx2.AsyncClient:
    """The HTTP client for the chrome service; tests swap it for one wired to a stub app."""
    return httpx2.AsyncClient(timeout=60)


def print_token(artifact_id: uuid.UUID) -> str:
    """A pass for the chrome service to fetch one artifact's page for the next few minutes."""
    return signing.token(PRINT_PURPOSE, str(artifact_id), expires=int(time.time()) + PRINT_TTL)


def verify_print_token(token: str, artifact_id: uuid.UUID) -> None:
    """Raise `signing.SignatureError` unless `token` is a live pass for this artifact."""
    signing.verify(PRINT_PURPOSE, token, str(artifact_id))


def print_url(found: workspace.Artifact, page: int | None = None) -> str:
    """The address the chrome service fetches the page at, with the page number as the hash when given."""
    url = f'{config.internal_url()}/print/{print_token(found.id)}/artifacts/{found.id}/'
    return f'{url}#{page}' if page is not None else url


async def ensure_built(found: workspace.Artifact, directory: Path) -> Path:
    """`dist/index.html`, built now if this process has not built the current version yet.

    A `build.BuildError` propagates. The page is served at `/artifacts/<id>/`, so the `.md` export it advertises
    is `../<id>.md`.
    """
    page = directory / 'dist' / 'index.html'
    if not page.is_file():
        await asyncio.to_thread(build.build_html, directory, markdown_url=f'../{found.id}.md')
    return page


async def call_chrome(endpoint: str, body: dict[str, object]) -> bytes:
    """POST `body` to the chrome service's `endpoint` and return the file it answers with."""
    chrome_url = config.chrome_url()
    if chrome_url is None:
        raise RenderError(503, 'rendering is not configured: OPENARTIFACT_CHROME_URL names the chrome service')
    try:
        async with chrome_client() as client:
            response = await client.post(f'{chrome_url}{endpoint}', json=body)
    except httpx2.HTTPError as exc:
        raise RenderError(502, f'chrome service unreachable: {exc}') from exc
    if response.status_code != 200:
        raise RenderError(502, f'chrome service failed ({response.status_code}): {response.text}')
    return response.content


async def pdf(found: workspace.Artifact) -> bytes:
    """The artifact's page printed to PDF. The page is built first, so a broken artifact fails as a build error."""
    async with workspace.open_artifact(found) as directory:
        await ensure_built(found, directory)
    return await call_chrome('/pdf/', {'url': print_url(found)})


async def screenshot(found: workspace.Artifact, page: int) -> bytes:
    """A PNG of one page of the artifact as a viewer sees it, in the window `VIEWPORTS` gives the type."""
    async with workspace.open_artifact(found) as directory:
        await ensure_built(found, directory)
    width, height = VIEWPORTS.get(found.type, VIEWPORTS['deck'])
    return await call_chrome('/screenshot/', {'url': print_url(found, page), 'width': width, 'height': height})
