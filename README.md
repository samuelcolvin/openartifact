# OpenArtifact

Markdown slide decks, documents and pages that render in the browser and print to PDF.

Why?

* My taste
* Good support for HTML presentation - keyboard control, page persistence in URL, jump to page, title
* Good support for PDF generation - configure `page` css property properly
* No JavaScript toolchain needed to build a deck: the runtime is one prebuilt `openartifact.js`, the builder is one Python module with no dependencies

## Artifact types

An artifact is markdown split into pages on `---` lines. `type` in `artifact.toml` picks how the pages are laid out; `theme` picks the colours.

- `deck` (default) - one 16:9 page at a time with navigation; prints one page per sheet.
- `document` - fixed-width sheets, one per page, that print to A4 like a word processor.
- `page` - a continuous, fluid page in the manner of a Notion page.

`examples/starter`, `examples/document` and `examples/page` show one of each.

## How it works

A deck is a directory:

```
my-deck/
├── artifact.toml          # type, title, theme, page_component, [context], paths (all optional)
├── main.md             # the content: markdown, pages separated by ---
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
<!-- class: cover; title: My Deck -->

### Section Label

# My Deck

---

# Hello, world

- Bullets, tables, code blocks and inline HTML all work
- A line containing only `---` starts a new page; this is page {{ PAGE_NUMBER }} of {{ PAGE_COUNT }}

<component src="Card.html" title="Components take parameters">

And markdown children, between the tags.

</component>

---

<!-- class: statement -->

# One bold statement.
```

`artifact.toml`:

```toml
title = "My Deck"
theme = "light"           # light | dark | markdown-light | markdown-dark (default: light)
page_component = "Page.html"   # rendered around every page: a header, a counter, a footer, with {{ CONTENT }} inside

[context]
DATE = "October 2026"     # {{ DATE }} anywhere
```

The four built-in themes split on two axes: light vs dark backgrounds, and whether markdown-source decorations (`#` heading prefixes, `**` strong markers, diamond bullets) render on top. Pick `light` or `dark` for a clean baseline; pick a `markdown-*` variant for the opinionated annotated look.

`styles.css` overrides any of the CSS variables in the base stylesheet:

```css
:root {
  --bg-slide: #092224;
  --color-heading: #fbffea;
  --accent: #e520e9;
}
```

The full authoring guide - page directives, the page component, components and parameters, images, code blocks - lives at [`skills/openartifact/SKILL.md`](skills/openartifact/SKILL.md), with the CSS variable contract and class hooks, build steps and local builds in [`skills/openartifact/references/`](skills/openartifact/references/). It can be installed into Claude Code, Codex, Cursor, etc. via [skills.sh](https://skills.sh) (`bunx skills add samuelcolvin/openartifact`).

## The builder API

- `build.build_html(directory, output=None, runtime_url='/openartifact.js')` - build `directory` to `output` (default `directory/dist/index.html`), loading the runtime from `runtime_url`; returns the output path. Input problems, including a referenced image that does not exist, raise `build.BuildError` with the file and line.
- `chrome.pdf.print_to_pdf(url, pdf_path)` - print the served page to PDF with Chrome headless at the type's page size; returns the PDF path.

## Exports

Next to the page at `/artifacts/<id>/`, the server offers:

- `/artifacts/<id>.md` - the markdown source behind a frontmatter summary (title, type, theme, URL, dates, the list of source files); what an agent should read instead of parsing the page.
- `/artifacts/<id>.zip` - every source file as a zip.
- `/artifacts/<id>.pdf` - the page printed to PDF.

PDF printing happens in the chrome service, `chrome/`, which runs in its own image with Chromium and prints whatever page URL it is given; the app calls it at `OPENARTIFACT_CHROME_URL` and tells it to fetch the page at `OPENARTIFACT_INTERNAL_URL` (the app as seen from the chrome container). `make docker-up` runs both; on the host, `make chrome-dev` serves it on :8766 with the local Chrome and `make dev` points at it. Without a chrome service the `.pdf` URL answers 503.

To print by hand, with the server running:

```bash
PYTHONPATH=. python3 -c 'from pathlib import Path; from chrome import pdf; pdf.print_to_pdf("http://127.0.0.1:8765/artifacts/<id>/", Path("deck.pdf"))'
```

If Chrome isn't found, or the conversion fails, the error carries the exact command so you can fix the Chrome path or flags and run it yourself. It looks like:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=./deck.pdf "http://127.0.0.1:8765/artifacts/<id>/"
```

There are no paper-size flags: each artifact type's stylesheet sets `@page` (16:9 for a deck, A4 for a document or page) and Chrome honours it. (Use `google-chrome` or `chromium` on Linux - `pdf.find_chrome` looks for them automatically.)

## Developing OpenArtifact itself

The browser runtime is in `frontend/` (pnpm); the builder is `backend/build.py`, the MCP tools `backend/mcp_server.py`, the HTTP server `backend/server.py` and the PDF chrome service `chrome/` (uv, configured by the root `pyproject.toml`).

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
