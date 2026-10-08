# OpenArtifact

Markdown slide decks, documents and pages that render in the browser and print to PDF.

Why?

* My taste
* Good support for HTML presentation - keyboard control, page persistence in URL, jump to page, title
* Good support for PDF generation - configure `page` css property properly
* No JavaScript toolchain needed to build a deck: the runtime is one prebuilt `openartifact.js`, the builder is one Python module with no dependencies

## The web app

Signed in, `/` lists your artifacts and the ones your organisation shares with you, with three buttons that start a deck, a document or a page in one click (the first thing you say in the chat names it) and an MCP button with the snippet that connects Claude Code, Codex, Cursor, VS Code, Claude or ChatGPT to this server, and `/edit/<id>` opens the editor: a chat with an agent on the left, the live page on the right. The agent is a pydantic-ai agent whose tools are this server's own MCP tools (`run_code` and `build`), run in-process as you, so its edits are commits in the artifact's history like any other. It can also search the web through the model's own search tool, for current facts and sources, and look at a page of the artifact as you see it (the `screenshot` tool, through the chrome service); the chat tells it which page you have open, so "this slide" means the one in the preview. The editor's Prompt button gives you the text to paste into your own coding agent instead, once it is connected over MCP, so it edits the same artifact with the same tools. Each turn streams over the Vercel AI SDK protocol; the conversation is kept per artifact and per user. The model picker offers the models configured on the server: set `PYDANTIC_AI_GATEWAY_API_KEY` for the built-in list, Claude Opus 5.5 (the default) and Sonnet 5.5 and the three newest OpenAI models, all through the [Pydantic AI Gateway](https://pydantic.dev/docs/ai/overview/gateway/), or `OPENARTIFACT_MODELS="gateway/anthropic:claude-opus-5-5=Opus,openai-responses:gpt-6-astra=GPT-6 Astra"` to name them yourself (a model without the `gateway/` prefix uses that provider's own key).

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
pnpm -C frontend install && pnpm -C frontend build     # -> frontend/dist/openartifact.js and frontend/dist/app/
```

Then start Postgres and the server, and let an agent drive it over MCP:

```bash
uv sync
make pg-start                                 # Postgres 18 in Docker
make dev                                      # http://127.0.0.1:8765, MCP at /mcp/ with bearer token `dev`
make docker-up                                # or build the Docker image and run server and database together
```

Each artifact is a git repository, bundled into an object store under `data/store/` locally or an `s3://` URL in production, with Postgres holding users and artifact metadata. In production set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `OPENARTIFACT_SECRET_KEY` instead of the dev token: the MCP endpoint is then behind Google login, and so are artifact pages. Register two redirect URIs on the Google OAuth client, `<base URL>/auth/callback` for MCP clients and `<base URL>/login/callback` for browsers.

Anyone signing in with a Google Workspace account joins the organisation of their domain (samuel@pydantic.dev lands in pydantic.dev). An artifact is personal (private, or public to anyone with the link) or belongs to the owner's organisation (visible to it, optionally editable by it, optionally public too); the owner always sees and edits it. The viewer toolbar shows the visibility, signs people in and out, and forks an artifact into the viewer's own space. Without Google configured, `/login` offers a development sign-in that doubles as the MCP dev token's user.

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
- `chrome.pdf.print_to_pdf(url, pdf_path)` - print the served page to PDF with Chrome headless at the type's page size; returns the PDF path and Chrome's stderr. A page that fails to load is a `ChromeError` with that stderr, since Chrome itself exits 0 and says why only there.
- `chrome.pdf.screenshot(url, png_path, width, height, scale=1)` - the page as a viewer with a window that size sees it; `#3` on the URL shows the third page; `scale` is the device pixel ratio.
- `chrome.pdf.dump_dom(url)` - the page's DOM after the runtime has run, which is how the PowerPoint and Word exports read what a deck or document opened with `?scene` writes into itself.

## Exports

Next to the page at `/artifacts/<id>/`, the server offers:

- `/artifacts/<id>.md` - the markdown source behind a frontmatter summary (title, type, theme, URL, dates, the list of source files); what an agent should read instead of parsing the page. The page's `<head>` links it as `rel="alternate"` with a comment saying so.
- `/artifacts/<id>.zip` - the artifact as a git repository: every source file at the head, plus `.git` with the artifact's own history (one commit per change, with the original messages and dates). Unzip it and `git log`.
- `/artifacts/<id>.pdf` - the page printed to PDF.
- `/artifacts/<id>.pptx` - a deck as a PowerPoint file: a picture of each slide as its background, with every block of text laid over it as an editable text box at the browser's position, font, size and colour. Retyping works; moving a text box leaves its background behind.
- `/artifacts/<id>.docx` - a document as a Word file: headings, paragraphs, lists, code, quotes, tables and images as ordinary Word paragraphs in Word's own styles, with none of the theme's colours, one page break per page of the artifact. Images keep their size on the page; SVGs, which Word cannot show, are rendered to pictures by the chrome service.
- `/artifacts/<id>.png?page=N` - one page as a PNG, the way a viewer sees it: a deck's slide, or a document scrolled to that page.

The zip, the PDF, the PowerPoint and the Word file download as `<title> <commit>.zip` / `.pdf` / `.pptx` / `.docx`, the title from `artifact.toml` and the short sha of the artifact's last change.

The page itself carries a viewer toolbar, added by `openartifact.js` and hidden in print: the artifact's title, a Download menu with the three exports, the OpenArtifact brand, and for a deck previous / next, the page counter and a full-screen toggle. It shows when the pointer nears the top of the window and slides away a second after it leaves.

PDF printing and screenshots happen in the chrome service, `chrome/`, which runs in its own image with Chromium and renders whatever page URL it is given; the app calls it at `OPENARTIFACT_CHROME_URL` and tells it to fetch the page at `OPENARTIFACT_INTERNAL_URL` (the app as seen from the chrome container). `make docker-up` runs both; on the host, `make chrome-dev` serves it on :8766 with the local Chrome and `make dev` points at it. Without a chrome service the `.pdf`, `.png`, `.pptx` and `.docx` URLs answer 503, and the editing agent cannot look at a page.

`render.yaml` deploys the whole thing to [Render](https://render.com) as a Blueprint: the server with a persistent disk for its artifacts, the chrome service on the private network, and a managed Postgres; the Google OAuth client, the signing key and the API keys are entered in the dashboard when the instance is created.

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

`pnpm -C frontend dev` rebuilds `frontend/dist/openartifact.js` on every change to `frontend/src/`; the server serves the new file on the next request. `pnpm -C frontend app:dev` runs Vite's dev server for the web app (`frontend/app/`) on :5173 with hot reload, proxying everything else to the Python server on :8765; `pnpm -C frontend build:app` writes the production bundle the Python server serves. Headless Chrome (`--dump-dom`, `--screenshot`) is handy for checking the runtime without a browser session.
