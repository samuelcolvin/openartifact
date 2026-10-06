"""Entry point for the HTTP server: configures Logfire and serves `server.app`.

Run with `uv run backend/main.py` (`HOST` and `PORT` override the bind address). Tests import `server` directly, so
nothing they do is sent to Logfire.
"""

import os

import logfire
import uvicorn

from server import app

logfire.configure(service_name='openartifact', send_to_logfire='if-token-present')
# FastAPI and FastMCP pick up Logfire's global providers on their own; asyncpg and monty need hooking up.
logfire.instrument_asyncpg()
logfire.instrument_monty()

if __name__ == '__main__':
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8765')))
