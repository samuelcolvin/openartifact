# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in the OpenArtifact repository.

## What this is

OpenArtifact turns a small set of source files - one markdown file, an optional folder of HTML/SVG components, an optional CSS file and any images they reference - into a single self-contained HTML page that renders itself. The page needs no server and no network: it opens from `file://`, presents in a browser and prints to PDF with Chrome headless. That page is the "artifact". Today the only artifact type is a slide deck.

The repo has two halves:

- **`frontend/`** - the browser runtime, `deck.js`. This is the only thing with a JavaScript build step, and it exists solely to be embedded in the output page. All rendering (markdown, components, code highlighting, navigation, build steps, print layout) happens here, in the browser.
- **`backend/build.py`** - the builder. A single Python script with no dependencies beyond the standard library. It does no rendering: it validates the inputs, packs them into a JSON blob, writes that blob plus `deck.js` into an HTML page, and optionally drives Chrome to print a PDF.
- **`backend/mcp_server.py`** - an MCP server for agents, built on `fastmcp` and `pydantic-monty`. Its `new_artifact` tool creates a deck from markdown and builds it; its `run_code` tool runs agent-written Python in a monty sandbox with the artifact's directory mounted read-write at `/artifact`, which is how the agent edits files; its `build` tool calls `build.py` on that directory to produce the HTML page (PDF export is not an MCP concern; it will be a download button on the served artifact). Artifacts live under `OPENARTIFACT_ROOT` (default `artifacts/`, gitignored).
- **`backend/server.py`** - the FastAPI app that runs it all: the MCP server mounted at `/mcp/`, `deck.js` at `/deck.js`, and each built artifact's `dist/` at `/artifacts/{name}/`. `uv run backend/server.py` (or `make serve`) starts it with uvicorn.

The split is deliberate. The builder is thin enough to run anywhere Python 3.11 exists and to become a service later; the runtime is where the product lives.

### Where it is going

The next step is to run the builder as a standalone service in a Docker container, so callers (people or AI agents) can send source files and get an artifact back without a checkout of this repo. The shape of that service (HTTP API, what it returns, whether it stores anything, whether artifact types beyond slides exist) has **not been decided**. Do not add an HTTP layer, a framework, storage, or a second artifact type until asked. When that work starts, `build.py` should be reused, not rewritten: keep its functions importable and side-effect free apart from the explicit file writes.

### Rules that follow from this

- **The builder stays one dependency-free script.** No package, no console script, no third-party Python imports in `build.py`. The project's runtime dependencies (`fastapi`, `uvicorn`, `fastmcp`, `pydantic-monty`, `logfire`) belong to `mcp_server.py` and `server.py`, which import `build.py`, never the other way round.
- **The runtime does the work, the builder packages it.** If a feature can be implemented in `frontend/src/` it goes there. The builder only mirrors runtime logic where it lets a build fail early with a good error (see `validate_slides`).
- **Everything in the page is synchronous.** Headless Chrome prints at `load`, so the runtime must finish rendering before then. Never add async work to `main.ts`.
- **Output is self-contained.** Components, styles and images are inlined; the page must never reference anything outside `index.html` and the `deck.js` beside it.
- **This is the library, not a deck.** Don't add brand-specific content, custom slides or example brand palettes here; those belong in user projects or in `examples/`. `examples/pennylane/` is a real brand deck kept locally for testing and is gitignored.

### Origin

Forked on 2026-09-30 from the `markdown-runtime` branch of [deckx](https://github.com/samuelcolvin/deckx), keeping its history. deckx shipped a Python package with a `deckx` CLI and a `deckx.toml` config file; here those are the single script and `artifact.toml`. The slide vocabulary (`deck.md`, `deck.js`, `.deck`, `DeckData`) was kept because it names the artifact, not the tool.

DO NOT use the em dash "—" in source files or docs; always use a plain hyphen "-".

## Commands

Two toolchains. Use **pnpm** (never npm/yarn/bun) for the TypeScript browser runtime in `frontend/` and **uv** for the Python builder in `backend/`. `pyproject.toml` at the repo root holds the ruff / basedpyright / pytest config and the dev dependency group; uv installs nothing else. The pnpm commands run from `frontend/` (or `pnpm -C frontend ...` from the root); the uv commands run from anywhere in the repo.

```bash
pnpm -C frontend install              # install JS dependencies
pnpm -C frontend build                # bundle src/main.ts -> frontend/dist/deck.js (esbuild, minified)
pnpm -C frontend dev                  # same, in watch mode
pnpm -C frontend typecheck            # tsc --noEmit
pnpm -C frontend lint                 # biome check
pnpm -C frontend format               # biome check --fix

uv sync                               # create .venv with the dependencies and dev tools (uv run does this on demand too)
uv run backend/build.py html --dir examples/starter   # -> examples/starter/dist/index.html + deck.js
uv run backend/build.py pdf --dir examples/starter    # html, then Chrome headless -> dist/deck.pdf
uv run backend/server.py              # HTTP server on :8000: MCP at /mcp/, deck.js, built artifacts; files under $OPENARTIFACT_ROOT
uv run ruff check                     # lint backend/ and tests/
uv run ruff format                    # format them
uv run basedpyright                   # strict type check of backend/ and tests/
uv run pytest                         # run tests/
```

The `Makefile` wraps these: `make install`, `make format`, `make lint`, `make test`, `make main` (all three), `make serve`.

`build.py` reads `frontend/dist/deck.js`, so run `pnpm -C frontend build` once after cloning or after changing anything in `frontend/src/`.

**After every set of changes, before reporting work as done, run:**

```bash
make format
make lint
make test
```

If `format` modifies files, that's fine - those edits are correct. If `lint` or the tests report an error, fix it.

## Pre-commit hooks

The repo uses `.pre-commit-config.yaml` (biome format, typecheck, ruff, basedpyright, codespell, basic file hygiene). Use [`prek`](https://github.com/j178/prek) - a fast Rust reimplementation of `pre-commit` - rather than `pre-commit` itself:

```bash
prek install                 # install the git hooks (one-time)
prek run --all-files         # run every hook against the whole repo
prek run typecheck           # run a single hook by id
```

`pdftoppm` (poppler) is available for inspecting generated PDFs - render to `./tmp/` (gitignored), max 1800 × 1800 px. Headless Chrome with `--dump-dom` or `--screenshot` is the way to check the runtime without a browser session.

## Architecture

Two halves joined by a JSON blob.

**Browser runtime (`frontend/src/`, bundled by esbuild to `frontend/dist/deck.js`)**

Paths below are relative to `frontend/`. `package.json`, `tsconfig.json` and `biome.jsonc` live there too.

- **`src/main.ts`** - entry. Reads the blob from `<script type="application/json" id="deck-data">`, injects `base.css`, `hljs.css` and the user's CSS as `<style>` elements, splits and renders the slides, mounts `.deck-presenter > .deck`, then starts navigation. Everything runs synchronously so the DOM is complete before `load` (headless Chrome prints at `load`). Never add async work here.
- **`src/split.ts`** - splits raw markdown into slides on `<slide .../>` lines (fence-aware) and parses the tag's attributes. This is the source of truth for slide syntax; `build.py` mirrors the scan only to fail early.
- **`src/render.ts`** - markdown-it (default preset, `html: true`) with highlight.js `lib/core` plus a curated language list.
- **`src/slide.ts`** - builds the `section.slide` DOM (topbar with dots, tabs or title, nav slot; `.slide-content > .slide-body`; footer). Class names must match `base.css`.
- **`src/components.ts`** - replaces `<component src>` tags from the blob (looping for nesting, unwrapping the `<p>` markdown-it puts around an inline tag) and rewrites `img[src]` to embedded data URIs.
- **`src/deck.ts`** - navigation: `#N` hash routing (1-indexed), keyboard, wheel, prev/next buttons, tab links, traffic-light home link, viewport scaling via `--slide-scale`, `document.title`, and the `NN/NN` counter injected into every slide's `.topbar-nav`. Next/previous step through a slide's build steps before changing slide; shift + left/right jump a whole slide.
- **`src/steps.ts`** - in-slide build steps. Elements with `data-step="N"` (and optional `data-step-end="M"`) get a `data-step-state` of `pending` / `active` / `done`; the slide gets `data-step`. Print state is precomputed into `data-step-print`. All appearance lives in `base.css`, never in JS.
- **`src/types.ts`** - `DeckData` / `DeckConfig`, the JSON contract with `build.py`.
- **`src/styles/base.css`** - layout, typography, `@page`, transitions, theme variants, the opt-in layout helpers (`.row`, `.col`, `.cols-2`, `.cols-3`, `.shrink`, `.small-code`, `.center`) and the build-step rules. Relies on CSS variables that user `styles.css` overrides. `src/hljs.css` maps highlight.js token classes onto those variables.

**Builder (`backend/build.py`)**

- One script, no package. `uv run backend/build.py ...` (or `python3 backend/build.py ...`) runs it; `main()` parses the `html` / `pdf` / `html-to-pdf` subcommands.
- Loads and validates `artifact.toml` (`tomllib`), reads `deck.md`, collects every `<component src>` file (`.html` verbatim, `.svg` inlined with its XML prolog stripped; nesting, cycles, path escapes), inlines every referenced image as a data URI, writes the JSON blob into the `TEMPLATE` page (with `<` escaped as `\u003c`) and copies `frontend/dist/deck.js` next to the output. `pdf` and `html-to-pdf` run Chrome headless with the paper size from `base.css`.
- No third-party runtime Python dependencies. Keep it that way. The builder finds `deck.js` via the repo layout (`backend/` -> repo root -> `frontend/dist/`).

**MCP server (`backend/mcp_server.py`)**

- `FastMCP` server named `openartifact`, mounted into `server.py` (it has no entry point of its own). The tool functions `new_artifact`, `run_code` and `build_artifact` (registered as `build`) are plain coroutines so tests call them directly. Nothing in them blocks the event loop: the sandbox is `AsyncMonty` and the builder runs via `asyncio.to_thread`.
- The `AsyncMonty` worker pool is opened by the `monty_pool()` async context manager and lives for the app's lifespan (`server.py` enters it next to FastMCP's lifespan). It is bound to the loop that opened it, so never create pools lazily or at import time. Tests use a `pool` fixture.
- `new_artifact(title, content, theme, footer)` is the only way an artifact is created. The server picks the identifier (`new_artifact_id`: slugified title capped at 40 chars plus a 6-char random suffix), writes `artifact.toml` (`render_toml`, JSON string escaping is valid TOML) and `deck.md` from `content`, then runs `build_artifact`. A build failure is returned as the `ToolError` and the files are kept for `run_code` to fix. `theme` is the `Theme` Literal, which a test keeps equal to `build.THEMES`. Tabs are not a parameter; the agent edits `artifact.toml` for those.
- `run_code(artifact, code, inputs)` resolves `artifact` (a `[a-z0-9_-]` slug) to an existing `<root>/<artifact>/` (a missing one is a `ToolError`, never created implicitly) and runs `code` with a `MountDir` on that directory in `read-write` mode at `/artifact`, which is also the sandbox's working directory. `inputs` become globals. Printed output and the trailing expression value are returned; sandbox exceptions become `ToolError`s carrying the monty traceback.
- `build(artifact)` runs `build.build_html` in a thread, so output lands in `<root>/<artifact>/dist/` where the sandbox can read it. It returns the URL the page is served at, built from `OPENARTIFACT_BASE_URL` (default `http://127.0.0.1:8000`). `BuildError` becomes a `ToolError`. The builder's own `print` lines go to the server's stdout.
- The mount must contain only artifact data, never code the host executes; keep `OPENARTIFACT_ROOT` off `sys.path`.

**HTTP server (`backend/server.py`)**

- `mcp.http_app(path='/')` mounted at `/mcp`. The app's `lifespan` enters FastMCP's lifespan (its session manager will not start otherwise) and `mcp_server.monty_pool()`. The MCP endpoint is `/mcp/`.
- `/deck.js` serves `frontend/dist/deck.js`. `/artifacts/{name}/` serves `dist/index.html` and `/artifacts/{name}/{file}` the other files in `dist/`, after checking the name and that the resolved path is directly inside `dist/`. Source files are never served. `/` returns a JSON index.
- Configuration is by environment: `HOST`, `PORT`, `OPENARTIFACT_ROOT`, `OPENARTIFACT_BASE_URL`, and `LOGFIRE_TOKEN` to send telemetry.
- Observability: `configure_telemetry()` calls `logfire.configure()` and hands monty its tracer, meter and logger via `pydantic_monty.instrument_telemetry`. FastAPI's built-in telemetry and FastMCP's native spans pick up Logfire's global providers on their own, so do not add `logfire.instrument_fastapi` or `logfire.instrument_mcp`; they would duplicate spans. FastAPI is created with `telemetry={'auto_configure': False}` so it never adds OTLP exporters of its own.

**Supporting files**

- `skills/openartifact/SKILL.md` - the user-facing authoring guide. Update it whenever slide syntax, config keys or the CSS contract change.
- `examples/starter/` - smoke-test deck exercising every feature (components, nesting, image, tabs, light slide, code).
- `tests/test_build.py` - pytest for `backend/build.py` (imported as `build`; pytest adds `backend/` to `pythonpath`).
- `tests/test_mcp_server.py` - pytest for the MCP server: the tool functions directly, plus one in-memory `fastmcp.Client` round trip.
- `tests/test_server.py` - pytest for the HTTP routes with `TestClient`, plus an MCP round trip over real HTTP against a uvicorn thread.

## The JSON contract

```json
{
  "config":     { "title": "...", "theme": "light", "footer": "...", "tabs": [{ "id": "intro", "label": "Intro" }] },
  "markdown":   "raw deck.md source",
  "components": { "Hero.html": "<section>...</section>" },
  "styles":     "raw styles.css",
  "images":     { "assets/logo.svg": "data:image/svg+xml;base64,..." }
}
```

Image keys are `posixpath.normpath` of the path as written; `components.ts` normalises `img[src]` the same way before lookup.

## Slide syntax in one paragraph

A line containing only `<slide .../>` starts a slide; the body runs to the next marker or end of file. Attributes mirror the old `<Slide>` props: `layout` (`title` | `statement`), `theme` (`light`), `tab`, `title`, `space` (`tight` | `wide`), `fontSize` (`large`), `id`. Anything before the first marker is a build error. Any element inside a slide can carry `data-step="N"` / `data-step-end="M"` to take part in build steps. `<component src="Name.html"></component>` needs its closing tag; `.svg` components are inlined as markup. See `SKILL.md` for the full reference.

## Code style

Add concise but thorough JSDoc-style comments on exported functions, and docstrings on Python functions. Comment any line of code that's complex or esoteric; otherwise let well-named identifiers speak. TypeScript is formatted by biome (single quotes, no semicolons, 119 columns); Python by ruff (single quotes, 120 columns).
