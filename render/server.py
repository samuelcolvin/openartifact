"""The render service: an HTTP endpoint that prints a page to PDF with Chrome headless.

It runs in its own image (`render/Dockerfile`, which has Chromium) because the application image has none, and
the application server calls it for `/artifacts/{id}.pdf`. It is internal: it prints whatever http(s) URL it is
given, so it must not be reachable from outside the deployment. Screenshots will join `/pdf/` later.

Run with `uv run uvicorn render.server:app --port 8766` (`make render-dev`) or `python -m render.server`; `HOST`
and `PORT` set the bind address.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from render import pdf

app = FastAPI(title='openartifact-render')


class PdfRequest(BaseModel):
    """What to print: the URL of a served artifact page."""

    url: str


@app.get('/health/')
def health() -> dict[str, str]:
    """Liveness probe for the container health check."""
    return {'status': 'ok'}


@app.post('/pdf/')
async def render_pdf(request: PdfRequest) -> Response:
    """Print the page at `url` and return the PDF; Chrome failing is a 502 whose detail carries the command."""
    if urlsplit(request.url).scheme not in ('http', 'https'):
        raise HTTPException(422, 'url must be http or https')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'page.pdf'
        try:
            # Chrome is a subprocess that takes a second or more; keep the event loop free meanwhile.
            await asyncio.to_thread(pdf.print_to_pdf, request.url, path)
        except pdf.RenderError as exc:
            raise HTTPException(502, str(exc)) from exc
        if not path.is_file():
            raise HTTPException(502, 'Chrome exited without writing a PDF')
        data = path.read_bytes()
    return Response(data, media_type='application/pdf')


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8766')))
