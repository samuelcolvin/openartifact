"""Create every example artifact through the MCP server, the way an agent would.

Start the server first (`make docker-up`), then:

    uv run examples/create_examples.py

For each example directory next to this script, the script calls `new_artifact` with a one-line placeholder,
writes the example's files into the artifact with one `run_code` call (the files are passed as `inputs`, so
nothing is escaped inside the code), then calls `build` and prints the page URL. `OPENARTIFACT_MCP_URL` points
the client somewhere other than the local default; `OPENARTIFACT_DEV_TOKEN` is the bearer token the server was
started with.
"""

from __future__ import annotations

import asyncio
import os
import tomllib
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

MCP_URL = os.environ.get('OPENARTIFACT_MCP_URL', 'http://127.0.0.1:8765/mcp/')
TOKEN = os.environ.get('OPENARTIFACT_DEV_TOKEN', 'dev')
EXAMPLES = Path(__file__).resolve().parent
NAMES = ('starter', 'document', 'page')


def example_files(directory: Path) -> dict[str, str]:
    """Every source file of an example, keyed by its path inside the artifact; `dist/` is build output."""
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding='utf-8')
        for path in sorted(directory.rglob('*'))
        if path.is_file() and 'dist' not in path.relative_to(directory).parts
    }


def write_files_code(paths: list[str]) -> str:
    """Sandbox code that writes `file_0`, `file_1`, ... (bound from `inputs`) to the given paths."""
    lines = ['from pathlib import Path', '']
    for index, path in enumerate(paths):
        lines.append(f'Path({path!r}).parent.mkdir(parents=True, exist_ok=True)')
        lines.append(f'Path({path!r}).write_text(file_{index})')
    lines.append(f'print({len(paths)}, "files written")')
    return '\n'.join(lines) + '\n'


def artifact_id(result: str) -> str:
    """The identifier from `new_artifact`'s first line, `artifact: <id>`."""
    return result.partition('\n')[0].removeprefix('artifact: ')


async def create_example(client: Client[StreamableHttpTransport], name: str) -> str:
    """Create one example as a new artifact and return its page URL."""
    directory = EXAMPLES / name
    config = tomllib.loads((directory / 'artifact.toml').read_text(encoding='utf-8'))
    title = str(config.get('title', name))
    files = example_files(directory)

    print(f'> new_artifact ({name})')
    created = await client.call_tool(
        'new_artifact',
        {
            'title': title,
            'content': f'# {title}\n',
            'type': config.get('type', 'deck'),
            'theme': config.get('theme', 'light'),
        },
    )
    artifact = artifact_id(created.data)

    print(f'> run_code: write {len(files)} files')
    paths = list(files)
    inputs: dict[str, str | int] = {f'file_{i}': files[path] for i, path in enumerate(paths)}
    written = await client.call_tool(
        'run_code', {'artifact': artifact, 'code': write_files_code(paths), 'inputs': inputs}
    )
    print(written.data, end='')

    print('> build')
    built = await client.call_tool('build', {'artifact': artifact})
    print(built.data)
    return built.data.rstrip().rpartition('page: ')[2]


async def main() -> None:
    async with Client(StreamableHttpTransport(MCP_URL, auth=TOKEN)) as client:
        print(f'connected to {MCP_URL}\n')
        urls = {name: await create_example(client, name) for name in NAMES}
    print('created:')
    for name, url in urls.items():
        print(f'  {name:<10} {url}')


if __name__ == '__main__':
    asyncio.run(main())
