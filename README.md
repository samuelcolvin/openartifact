# OpenArtifact

Markdown slide decks that render in the browser and print to PDF.

Why?

* My taste
* Good support for HTML presentation - keyboard control, slide persistence in URL, jump to slide, title
* Good support for PDF generation - configure `page` css property properly
* No JavaScript toolchain needed to build a deck: the runtime is one prebuilt `openartifact.js`, the builder is one Python module with no dependencies

## Artifact types

`type` in `artifact.toml` picks the form of the artifact; `theme` picks the colours.

- `deck` (default) - slides with navigation, one 16:9 slide per PDF page. The markdown has a `<slide .../>` line starting each slide.
- `document` - a fixed-width sheet that prints to A4 pages like a word processor. Plain markdown, structured with headings.
- `page` - a continuous, fluid page in the manner of a Notion page. Plain markdown.

`examples/starter`, `examples/document` and `examples/page` show one of each.

## How it works

A deck is a directory:

```
my-deck/
├── artifact.toml          # title, theme, footer, tabs, paths (all optional)
├── main.md             # the content: slides, or plain markdown
├── styles.css          # theme tokens (optional)
├── components/         # HTML files pulled in with <component src="...">
│   └── Hero.html
└── assets/             # images
```

`backend/build.py` reads those files and writes `dist/index.html`: the markdown source, every referenced component and your CSS go into the page verbatim as data blocks (so the page reads as source), next to `<script src="/openartifact.js">`. `backend/server.py` serves that page at `/artifacts/<id>/` along with `openartifact.js` and the deck's images, which the page references relatively. When the page loads, `openartifact.js` splits the markdown into slides, renders it, expands the components, highlights code and wires up navigation. Chrome headless prints the served page to PDF.

## Quick start

Clone the repo and build the browser runtime once:

```bash
git clone https://github.com/samuelcolvin/openartifact
cd openartifact
pnpm -C frontend install && pnpm -C frontend build     # -> frontend/dist/openartifact.js
```

Then start Postgres and the server, and let an agent drive it over MCP:

```bash
uv sync
make pg-start                                 # Postgres 18 in Docker
make dev                                      # http://127.0.0.1:8765, MCP at /mcp/ with bearer token `dev`
make docker-up                                # or build the Docker image and run server and database together
```

Artifacts are stored as git repositories (one per user, one directory per artifact), bundled into an object store under `data/store/` locally or an `s3://` URL in production, with Postgres holding users and artifact metadata. In production set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `OPENARTIFACT_SECRET_KEY` instead of the dev token and the MCP endpoint is behind Google login.

The builder itself is a library, `backend/build.py`, with no dependencies beyond Python 3.11+. To build a deck directory by hand:

```bash
PYTHONPATH=backend python3 -c 'from pathlib import Path; import build; print(build.build_html(Path("examples/starter")))'
```

## Authoring

`main.md`:

```markdown
<slide layout="title"/>

### Section Label

# My Deck

<slide tab="intro"/>

# Hello, world

- Bullets, tables, code blocks and inline HTML all work
- A `<slide .../>` line starts each slide; there is no closing tag

<component src="Hero.html"></component>

<slide layout="statement"/>

# One bold statement.
```

`artifact.toml`:

```toml
title = "My Deck"
theme = "light"           # light | dark | markdown-light | markdown-dark (default: light)

tabs = [
  { id = "intro", label = "Intro" },
]
```

The four built-in themes split on two axes: light vs dark backgrounds, and whether markdown-source decorations (`#` heading prefixes, `**` strong markers, traffic-light dots, mono slide counter, diamond bullets) render on top. Pick `light` or `dark` for a clean baseline; pick a `markdown-*` variant for the opinionated annotated look.

`styles.css` overrides any of the CSS variables in the base stylesheet:

```css
:root {
  --bg-slide: #092224;
  --color-heading: #fbffea;
  --accent: #e520e9;
}
```

The full authoring guide - slide attributes, components, images, code blocks, the CSS variable contract and class hooks - lives at [`skills/openartifact/SKILL.md`](skills/openartifact/SKILL.md). It can be installed into Claude Code, Codex, Cursor, etc. via [skills.sh](https://skills.sh) (`bunx skills add samuelcolvin/openartifact`).

## The builder API

- `build.build_html(directory, output=None, runtime_url='/openartifact.js')` - build `directory` to `output` (default `directory/dist/index.html`), loading the runtime from `runtime_url`; returns the output path. Input problems, including a referenced image that does not exist, raise `build.BuildError` with the file and line.
- `pdf.print_to_pdf(url, pdf_path)` - print the served page to PDF with Chrome headless at the slide page size; returns the PDF path.

## Converting to PDF

With the server running:

```bash
PYTHONPATH=backend python3 -c 'from pathlib import Path; import pdf; pdf.print_to_pdf("http://127.0.0.1:8765/artifacts/<id>/", Path("deck.pdf"))'
```

If Chrome isn't found, or the conversion fails, the error carries the exact command so you can fix the Chrome path or flags and run it yourself. It looks like:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=./deck.pdf "http://127.0.0.1:8765/artifacts/<id>/"
```

There are no paper-size flags: each artifact type's stylesheet sets `@page` (16:9 for a deck, A4 for a document or page) and Chrome honours it. (Use `google-chrome` or `chromium` on Linux - `pdf.find_chrome` looks for them automatically.)

## Developing OpenArtifact itself

The browser runtime is in `frontend/` (pnpm); the builder is `backend/build.py` and `backend/pdf.py`, the MCP tools `backend/mcp_server.py` and the HTTP server `backend/server.py` (uv, configured by the root `pyproject.toml`).

```bash
pnpm -C frontend install
pnpm -C frontend build              # bundle frontend/src -> frontend/dist/openartifact.js
pnpm -C frontend typecheck
pnpm -C frontend lint
uv run ruff check
uv run basedpyright
uv run pytest
```

`pnpm -C frontend dev` rebuilds `frontend/dist/openartifact.js` on every change to `frontend/src/`; the server serves the new file on the next request. Headless Chrome (`--dump-dom`, `--screenshot`) is handy for checking the runtime without a browser session.
