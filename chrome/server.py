"""The chrome service: an HTTP endpoint that prints a page to PDF with Chrome headless.

It runs in its own image (`chrome/Dockerfile`, which has Chromium) because the application image has none, and
the application server calls it for `/artifacts/{id}.pdf`. It is internal: it prints whatever http(s) URL it is
given, so it must not be reachable from outside the deployment. Screenshots will join `/pdf/` later.

`chrome/main.py` configures Logfire and serves this app; this module never touches Logfire, so tests importing
it send nothing. The span around the Chrome run uses the OpenTelemetry API, which is a no-op until `main.py`
installs a provider.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from opentelemetry import trace
from pydantic import BaseModel

from chrome import pdf

app = FastAPI(title='openartifact-chrome')
tracer = trace.get_tracer('openartifact.chrome')


class PdfRequest(BaseModel):
    """What to print: the URL of a served artifact page."""

    url: str


@app.get('/health/')
def health() -> dict[str, str]:
    """Liveness probe for the container health check."""
    return {'status': 'ok'}


@app.post('/pdf/')
async def print_pdf(request: PdfRequest) -> Response:
    """Print the page at `url` and return the PDF; Chrome failing is a 502 whose detail carries the command."""
    if urlsplit(request.url).scheme not in ('http', 'https'):
        raise HTTPException(422, 'url must be http or https')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'page.pdf'
        with tracer.start_as_current_span('chrome print to pdf', attributes={'url.full': request.url}) as span:
            try:
                # Chrome is a subprocess that takes a second or more; keep the event loop free meanwhile.
                await asyncio.to_thread(pdf.print_to_pdf, request.url, path)
            except pdf.ChromeError as exc:
                span.record_exception(exc)
                raise HTTPException(502, str(exc)) from exc
            if not path.is_file():
                raise HTTPException(502, 'Chrome exited without writing a PDF')
            data = path.read_bytes()
            span.set_attribute('pdf.size', len(data))
    return Response(data, media_type='application/pdf')
