# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in the OpenArtifact repository.

## What this is

OpenArtifact turns a small set of source files - one markdown file, an optional folder of HTML/SVG components, an optional CSS file and any images they reference - into one HTML page that renders itself in the browser. The page carries the markdown, components and CSS as a JSON blob, loads the runtime `openartifact.js` from the server and references its images relatively, so it is served by `backend/server.py` at `/artifacts/<id>/`, presents in a browser and prints to PDF with Chrome headless against that URL. That page is the "artifact".

An artifact has a **type**, set in `artifact.toml` and orthogonal to `theme`: `deck` (slides with navigation, one per page in print; the markdown has a `<slide .../>` line starting each slide), `document` (plain markdown rendered as a fixed-width sheet that prints to A4 pages) or `page` (plain markdown rendered as a continuous, fluid page like a Notion page). The type decides which stylesheets the runtime injects and whether it splits slides or renders the markdown whole.

The repo has two halves:

- **`frontend/`** - the browser runtime, `openartifact.js`. This is the only thing with a JavaScript build step, and it exists solely to be loaded by the output page. All rendering (markdown, components, code highlighting, navigation, build steps, print layout, the per-type layouts) happens here, in the browser.
- **`backend/build.py`** - the builder, a library module with no dependencies beyond the standard library and no CLI. It does no rendering: it validates the inputs (including that every referenced image exists), packs markdown, components and CSS into a JSON blob and writes an HTML page that loads `openartifact.js` by URL. **`backend/pdf.py`** drives Chrome headless to print the served page to PDF.
- **`backend/mcp_server.py`** - an MCP server for agents, built on `fastmcp` and `pydantic-monty`. `new_artifact` creates an artifact from markdown and builds it; `run_code` runs agent-written Python in a monty sandbox with the artifact's directory mounted read-write at `/artifact`, which is how the agent edits files; `build` calls `build.py` on that directory; `list_artifacts` lists the caller's. Artifact identifiers are UUIDs. PDF export is not an MCP concern; it will be a download button on the served artifact.
- **`backend/workspace.py`, `db.py`, `store.py`, `auth.py`** - storage and identity. Each user has a workspace: a git repository with one directory per artifact (`artifacts/<uuid>/`), kept as a working clone in a local cache, serialised as a `git bundle` into an object store (local directory or S3) under an immutable key per commit, with Postgres holding the current head and the users / workspaces / artifacts tables. The MCP endpoint is behind Google login (a static token for local development); artifact pages are public by UUID.
- **`backend/server.py`** - the FastAPI app that runs it all: the MCP server mounted at `/mcp/`, `openartifact.js` at `/openartifact.js`, and each artifact's page and media at `/artifacts/{uuid}/`, built on demand from the workspace checkout. `uv run backend/server.py` (or `make serve`) starts it with uvicorn.

The split is deliberate. The builder is thin enough to run anywhere Python 3.11 exists and to become a service later; the runtime is where the product lives.

### Where it is going

The service shape is now set: a FastAPI server hosting the MCP endpoint, with git-backed workspaces in an object store and Postgres. Next, in rough order: a PDF download button on served pages, a GitHub backend for workspaces (the local clone plus a `git push`/`fetch` remote instead of bundles, using a GitHub App with per-repo install so users pick the repo), orgs owning workspaces with several members, forking an artifact (copy a directory into another workspace with `forked_from` set), user-supplied themes stored the same way, and browser login for private pages. The schema already leaves room for each (`credentials`, `forked_from`, an owner column to relax, a `themes/` prefix in the repo). `build.py` stays the importable, side-effect-free builder throughout.

### Rules that follow from this

- **The builder stays dependency-free and programmatic.** No package, no console script, no argparse, no `print`, no third-party Python imports in `build.py` or `pdf.py`; they return paths and raise `BuildError`. The project's runtime dependencies (`fastapi`, `uvicorn`, `fastmcp`, `pydantic-monty`, `logfire`, `asyncpg`, `obstore`) belong to the server modules, which import `build.py`, never the other way round.
- **Every change to an artifact is a commit.** File changes go through `workspace.edit()`, never by writing into a checkout directly; the commit, bundle upload and `head_sha` update happen together or not at all. Build output (`dist/`) is derived, gitignored and rebuilt on demand.
- **One cache directory per process.** The checkout cache is guarded by an in-process asyncio lock and the Postgres row lock; two processes sharing `OPENARTIFACT_CACHE_DIR` are not protected against each other.
- **The runtime does the work, the builder packages it.** If a feature can be implemented in `frontend/src/` it goes there. The builder only mirrors runtime logic where it lets a build fail early with a good error (see `validate_slides`).
- **Everything in the page is synchronous.** Headless Chrome prints at `load`, so the runtime must finish rendering before then. Never add async work to `main.ts`.
- **Output is served, not self-contained.** The page links `/openartifact.js` and references images relatively; `server.py` hosts the runtime at `/openartifact.js` and an artifact's images and fonts at `/artifacts/<id>/<path>`. Nothing is inlined as a data URI and `openartifact.js` is not copied next to the page. Opening `dist/index.html` from `file://` does not work and is not a goal.
- **A new artifact type is a stylesheet and a renderer, not a fork.** Add the value to `TYPES` (build.py), `ArtifactType` (types.ts and mcp_server.py), a `TYPE_STYLES` entry, and validation of what its markdown may contain. Share `shared.css` and, for prose-like types, `prose.css`.
- **This is the library, not a deck.** Don't add brand-specific content, custom slides or example brand palettes here; those belong in user projects or in `examples/`. `examples/pennylane/` is a real brand deck kept locally for testing and is gitignored.

### Origin

Forked on 2026-09-30 from the `markdown-runtime` branch of [deckx](https://github.com/samuelcolvin/deckx), keeping its history. deckx shipped a Python package with a `deckx` CLI and a `deckx.toml` config file; here those are the single script and `artifact.toml`. The slide vocabulary (`deck.md`, `.deck`, `DeckData`) was kept because it names the artifact, not the tool; the runtime bundle is `openartifact.js` because it names the tool.

DO NOT use the em dash "—" in source files or docs; always use a plain hyphen "-".

## Commands

Two toolchains. Use **pnpm** (never npm/yarn/bun) for the TypeScript browser runtime in `frontend/` and **uv** for the Python builder in `backend/`. `pyproject.toml` at the repo root holds the ruff / basedpyright / pytest config and the dev dependency group; uv installs nothing else. The pnpm commands run from `frontend/` (or `pnpm -C frontend ...` from the root); the uv commands run from anywhere in the repo.

```bash
pnpm -C frontend install              # install JS dependencies
pnpm -C frontend build                # bundle src/main.ts -> frontend/dist/openartifact.js (esbuild, minified)
pnpm -C frontend dev                  # same, in watch mode
pnpm -C frontend typecheck            # tsc --noEmit
pnpm -C frontend lint                 # biome check
pnpm -C frontend format               # biome check --fix

uv sync                               # create .venv with the dependencies and dev tools (uv run does this on demand too)
make postgres                         # Postgres 17 in Docker (docker-compose.yml) at DATABASE_URL\'s default
PYTHONPATH=backend uv run python -c 'from pathlib import Path; import build; build.build_html(Path("examples/starter"))'  # by hand
OPENARTIFACT_DEV_TOKEN=dev uv run backend/server.py   # HTTP server on :8000: MCP at /mcp/ (bearer token `dev`), openartifact.js, artifact pages
uv run ruff check                     # lint backend/ and tests/
uv run ruff format                    # format them
uv run basedpyright                   # strict type check of backend/ and tests/
uv run pytest                         # run tests/
```

The `Makefile` wraps these: `make install`, `make format`, `make lint`, `make test`, `make main` (all three), `make serve`.

`server.py` serves `frontend/dist/openartifact.js`, so run `pnpm -C frontend build` once after cloning or after changing anything in `frontend/src/`.

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

**Browser runtime (`frontend/src/`, bundled by esbuild to `frontend/dist/openartifact.js`)**

Paths below are relative to `frontend/`. `package.json`, `tsconfig.json` and `biome.jsonc` live there too.

- **`src/main.ts`** - entry. Reads the blob from `<script type="application/json" id="artifact-data">`, injects `shared.css`, the type's sheets (`TYPE_STYLES`), `hljs.css` and the user's CSS as `<style>` elements, then dispatches on `config.type`: a deck is split and rendered into `.deck-presenter > .deck` and navigation starts; a document or page goes to `article.ts`. Everything runs synchronously so the DOM is complete before `load` (headless Chrome prints at `load`). Never add async work here.
- **`src/article.ts`** - the `document` and `page` types: the whole markdown rendered into `.artifact-<type> > article.prose`, components expanded, the `footer` appended once, `document.title` from the config or the first h1. No navigation and no build steps.
- **`src/split.ts`** - splits raw markdown into slides on `<slide .../>` lines (fence-aware) and parses the tag's attributes. This is the source of truth for slide syntax; `build.py` mirrors the scan only to fail early.
- **`src/render.ts`** - markdown-it (default preset, `html: true`) with highlight.js `lib/core` plus a curated language list.
- **`src/slide.ts`** - builds the `section.slide` DOM (topbar with dots, tabs or title, nav slot; `.slide-content > .slide-body`; footer). Class names must match `deck.css`.
- **`src/components.ts`** - replaces `<component src>` tags from the blob (looping for nesting, unwrapping the `<p>` markdown-it puts around an inline tag). Images are untouched: relative `src` and CSS `url()` resolve against the page URL, which the server answers.
- **`src/deck.ts`** - navigation: `#N` hash routing (1-indexed), keyboard, wheel, prev/next buttons, tab links, traffic-light home link, viewport scaling via `--slide-scale`, `document.title`, and the `NN/NN` counter injected into every slide's `.topbar-nav`. Next/previous step through a slide's build steps before changing slide; shift + left/right jump a whole slide.
- **`src/steps.ts`** - in-slide build steps. Elements with `data-step="N"` (and optional `data-step-end="M"`) get a `data-step-state` of `pending` / `active` / `done`; the slide gets `data-step`. Print state is precomputed into `data-step-print`. All appearance lives in `deck.css`, never in JS.
- **`src/types.ts`** - `ArtifactData` / `ArtifactConfig` / `ArtifactType`, the JSON contract with `build.py`.
- **`src/styles/shared.css`** - the CSS variables, theme token assignments and reset every type loads. User `styles.css` overrides the variables.
- **`src/styles/deck.css`** - the deck: slide geometry, the 16:9 `@page`, transitions, topbar, slide typography, the opt-in layout helpers (`.row`, `.col`, `.cols-2`, `.cols-3`, `.shrink`, `.small-code`, `.center`) and the build-step rules. Everything is scoped to `.slide` / `.deck-presenter`.
- **`src/styles/prose.css`** - typography for `article.prose`, shared by document and page: the slide typography at reading sizes, the same `markdown-*` decorations, and print rules that keep headings with their text and blocks unsplit.
- **`src/styles/document.css`** / **`page.css`** - the two layouts: a centred sheet at `--document-width` with an A4 `@page`, and a fluid column capped at `--page-max-width`. Each sets the body background and its own `@page`.
- **`src/hljs.css`** - maps highlight.js token classes onto the brand variables, scoped to `.slide, .prose`.

**Builder (`backend/build.py`)**

- A module, not a script: `build_html(directory, output=None, runtime_url='/openartifact.js')` is the entry point and returns the output path. There is no CLI; `mcp_server.py` and the tests are the callers.
- Loads and validates `artifact.toml` (`tomllib`; `type` in `TYPES`, `tabs` only for a deck, `favicon` a relative path that exists), reads `deck.md`, checks it per type (`validate_slides` for a deck; `validate_prose` for the others, which rejects slide markers and empty content; both skip fenced code via `unfenced_lines`), collects every `<component src>` file (`.html` verbatim, `.svg` inlined with its XML prolog stripped; nesting, cycles, path escapes), checks every relatively referenced image exists inside the deck (`check_images`; nothing is embedded), and writes the JSON blob into the `TEMPLATE` page (with `<` escaped as `\u003c`) with `<script src="{runtime_url}">`.
- No third-party runtime Python dependencies, and no knowledge of where `openartifact.js` lives on disk: that is `server.py`'s concern.

**PDF (`backend/pdf.py`)**

- `print_to_pdf(url, pdf_path)` runs Chrome headless (`find_chrome`: the macOS app bundle, then names on PATH) against the served page URL and returns the PDF path. No paper flags: each type's stylesheet sets `@page` (16:9 for a deck, A4 for the others) and Chrome honours it. It takes a URL, not a file, because the page loads `openartifact.js` and images from the server. A missing Chrome or a non-zero exit raises `BuildError` carrying the full command so it can be run by hand. Nothing calls it yet; the planned download button on the served artifact will.

**Storage (`backend/db.py`, `store.py`, `workspace.py`, `config.py`)**

- `db.py`: `db_pool()` opens the asyncpg pool for the lifespan, `pool()` returns it. `migrate(conn)` applies `backend/migrations/NNNN_name.sql` in order under an advisory lock and records them in `schema_migrations`; versions must be consecutive. Tables: `users`, `credentials` (reserved for GitHub), `workspaces` (`head_sha`), `artifacts` (uuid pk, `workspace_id`, `title`, `type`, `forked_from`).
- `store.py`: `ObjectStore` over obstore with `get`/`put`/`exists`/`list`/`delete`, opened by `object_store()` from `OPENARTIFACT_STORE_URL`. Keys are immutable. A missing object raises `StoreMissing` (obstore reports it as `FileNotFoundError`).
- `workspace.py`: `checkout_path(ws)` is the clone in `config.cache_dir()`; `sync_checkout(ws, head_sha)` makes it match the head (fetching the bundle with `git fetch <file> main`, never `git clone`, then `reset --hard` and `clean`), deleting `dist/` for artifacts whose sources changed. `edit(ws, message)` is the write protocol: workspace asyncio lock, `SELECT ... FOR UPDATE` on the row with a 60s `lock_timeout` (`WorkspaceBusy`), sync, yield an `Edit` (`artifact_dir(id)`, `conn`, mutable `message`), then `git add -A`, commit if anything changed, `git bundle create`, upload to `workspaces/<ws>/<sha>.bundle`, `UPDATE workspaces SET head_sha`; any failure rolls back and the next sync repairs the checkout. `open_artifact(artifact)` is the read side. Row helpers: `insert_artifact`, `touch_artifact`, `get_artifact` (global, for public pages), `get_artifact_in` (scoped, for tools), `list_artifacts`, `import_directory`. Git runs via `asyncio.create_subprocess_exec` with a fixed env (`GIT_ENV`) and a Logfire span per call.
- `config.py`: `base_url()` and `cache_dir()`, read from the environment on each call so tests can monkeypatch them.

**Auth (`backend/auth.py`)**

- `make_auth_provider()` builds the FastMCP `auth=`: `GoogleProvider` when `GOOGLE_CLIENT_ID` is set (needs `GOOGLE_CLIENT_SECRET` and `OPENARTIFACT_SECRET_KEY`, a Fernet key; the OAuth proxy's state lives in Postgres through `PostgreSQLStore` wrapped in `FernetEncryptionWrapper`, table `oauth_state`), else `StaticTokenVerifier` with `OPENARTIFACT_DEV_TOKEN`, else a startup error. Tests set the dev token in `conftest.py` before importing the server.
- `current_principal()` turns the verified token's claims (`sub`, `email`, `name`, `picture`) into a `Principal(user_id, workspace_id, email)` via `upsert_user`, creating the user's workspace on first sight, and caches by `sub`. `as_principal(p)` overrides it for code outside an MCP request (tests). Not authenticated is a `ToolError`.

**MCP server (`backend/mcp_server.py`)**

- `FastMCP` server named `openartifact`, mounted into `server.py` (it has no entry point of its own). The tool functions `new_artifact`, `run_code`, `build_artifact` (registered as `build`) and `list_artifacts` are plain coroutines so tests call them directly under `auth.as_principal`. Nothing in them blocks the event loop: the sandbox is `AsyncMonty` and the builder runs via `asyncio.to_thread`.
- The `AsyncMonty` worker pool is opened by the `monty_pool()` async context manager and lives for the app's lifespan. It is bound to the loop that opened it, so never create pools lazily or at import time. Tests use a `pool` fixture.
- `new_artifact(title, content, type, theme, footer)` is the only way an artifact is created: inside one `edit()` it writes `artifact.toml` (`render_toml`, JSON string escaping is valid TOML) and `deck.md` under `artifacts/<uuid4>/` and inserts the row; then it builds. A build failure is returned as the `ToolError` but the artifact exists and is listed, for `run_code` to fix. `type` and `theme` are Literals kept equal to `build.TYPES` / `build.THEMES` by a test. Tabs are not a parameter; the agent edits `artifact.toml` for those.
- `run_code(artifact, code, inputs)` resolves the UUID within the caller's workspace (`resolve`; another workspace's artifact is reported as missing), mounts the directory read-write at `/artifact` inside an `edit()`, and commits even when the sandbox raised (message `run_code (failed): <id>`) before re-raising, so partial writes persist. Printed output and the trailing expression value are returned.
- `build(artifact)` runs `build.build_html` in a thread under `open_artifact`; output lands in the checkout's `dist/`, where the sandbox can read it, and the returned URL is `OPENARTIFACT_BASE_URL/artifacts/<uuid>/`.
- The mount must contain only artifact data, never code the host executes; keep the cache directory off `sys.path`.

**HTTP server (`backend/server.py`)**

- `mcp.http_app(path='/')` mounted at `/mcp`. The app's `lifespan` enters FastMCP's lifespan (its session manager will not start otherwise), `db.db_pool()`, `store.object_store()` and `mcp_server.monty_pool()`, then runs migrations. The MCP endpoint is `/mcp/`.
- `/openartifact.js` serves `frontend/dist/openartifact.js` (`RUNTIME_JS_PATH`); every built page loads it from there. `/artifacts/{uuid}/` looks the artifact up (any workspace: pages are public by UUID), syncs the checkout under `open_artifact`, builds `dist/index.html` if missing (a `BuildError` is a 422) and returns the HTML read under the lock. `/artifacts/{uuid}/{path}` serves images and fonts (`SERVED_EXTS`) from the artifact directory after the containment and extension check in `contained_file`. A non-UUID is a 404. `deck.md`, `artifact.toml`, `styles.css` and components are not served as files. `/` returns a JSON index without artifacts; `list_artifacts` is per user.
- Configuration is by environment: `HOST`, `PORT`, `DATABASE_URL`, `OPENARTIFACT_STORE_URL`, `OPENARTIFACT_CACHE_DIR`, `OPENARTIFACT_BASE_URL`, the auth variables above, and `LOGFIRE_TOKEN` to send telemetry. The deployment image needs `git`.
- Observability: `configure_telemetry()` calls `logfire.configure()`, `logfire.instrument_asyncpg()`, and hands monty its tracer, meter and logger via `pydantic_monty.instrument_telemetry`. FastAPI's built-in telemetry and FastMCP's native spans pick up Logfire's global providers on their own, so do not add `logfire.instrument_fastapi` or `logfire.instrument_mcp`; they would duplicate spans. FastAPI is created with `telemetry={'auto_configure': False}` so it never adds OTLP exporters of its own.

**Supporting files**

- `skills/openartifact/SKILL.md` - the user-facing authoring guide. Update it whenever slide syntax, config keys or the CSS contract change.
- `examples/starter/` - smoke-test deck exercising every feature (components, nesting, image, tabs, light slide, code). `examples/document/` and `examples/page/` are the same for the other two types.
- `tests/test_build.py` - pytest for `backend/build.py` (imported as `build`; pytest adds `backend/` to `pythonpath`).
- `tests/test_pdf.py` - pytest for `backend/pdf.py`; Chrome is stubbed, the command assembly and error paths are checked.
- `tests/conftest.py` - creates a throwaway database per session on the server at `DATABASE_URL` (`make postgres` first), migrates it, and provides `db_pool`, `storage` (file store and cache in `tmp_path`), `principal` and `server_env` (for tests that run the whole app). Tests needing Postgres are the ones using those fixtures; `test_build.py` and `test_pdf.py` run without it.
- `tests/test_db.py`, `test_store.py`, `test_workspace.py` - the storage layer, including the edit protocol's failure paths (body error, upload failure, a second cache syncing from the bundle, stale `dist/` removal).
- `tests/test_mcp_server.py` - the tool functions directly as a signed-in user (`auth.as_principal`), plus one in-memory `fastmcp.Client` round trip.
- `tests/test_server.py` - the HTTP routes with `TestClient` (seeding through `client.portal` so rows and checkouts belong to the app's loop), plus MCP over real HTTP against a uvicorn thread with the dev token.

## The JSON contract

```json
{
  "config":     { "type": "deck", "title": "...", "theme": "light", "footer": "...", "tabs": [{ "id": "intro", "label": "Intro" }] },
  "markdown":   "raw deck.md source",
  "components": { "Hero.html": "<section>...</section>" },
  "styles":     "raw styles.css"
}
```

Images are not in the blob. The page is served at `/artifacts/<id>/`, so relative image paths in the markdown, components and CSS resolve to `/artifacts/<id>/<path>`, which `server.py` serves from the artifact directory. The builder only checks that each referenced file exists.

## Slide syntax in one paragraph

For `type = "document"` and `type = "page"` the markdown is rendered whole and a `<slide .../>` line is a build error. For a deck: a line containing only `<slide .../>` starts a slide; the body runs to the next marker or end of file. Attributes mirror the old `<Slide>` props: `layout` (`title` | `statement`), `theme` (`light`), `tab`, `title`, `space` (`tight` | `wide`), `fontSize` (`large`), `id`. Anything before the first marker is a build error. Any element inside a slide can carry `data-step="N"` / `data-step-end="M"` to take part in build steps. `<component src="Name.html"></component>` needs its closing tag; `.svg` components are inlined as markup. See `SKILL.md` for the full reference.

## Code style

Add concise but thorough JSDoc-style comments on exported functions, and docstrings on Python functions. Comment any line of code that's complex or esoteric; otherwise let well-named identifiers speak. TypeScript is formatted by biome (single quotes, no semicolons, 119 columns); Python by ruff (single quotes, 120 columns).
