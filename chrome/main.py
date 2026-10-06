"""Entry point for the chrome service: configures Logfire and serves `chrome.server.app`.

Run with `python -m chrome.main` (`HOST` and `PORT` set the bind address; the image does this) or
`uv run uvicorn chrome.main:app --port 8766` (`make chrome-dev`). Tests import `chrome.server` directly, so nothing
they do is sent to Logfire.
"""

import os

import logfire
import uvicorn

from chrome.server import app

# FastAPI's built-in telemetry picks up the global providers this installs. `distributed_tracing=True` accepts the
# `traceparent` the application server sends, so this service's spans join its request's trace (without it Logfire
# warns on every request that trace context arrived unexpectedly); the Chrome run itself is a span in `chrome.server`.
logfire.configure(service_name='openartifact-chrome', send_to_logfire='if-token-present', distributed_tracing=True)

if __name__ == '__main__':
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8766')))
