# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in the OpenArtifact repository.

## What this is

OpenArtifact turns a small set of source files - one markdown file, an optional folder of HTML/SVG components, an optional CSS file and any images they reference - into one HTML page that renders itself in the browser. An artifact is made of **pages**: the markdown split on lines containing only `---`. The page carries the markdown, components and CSS verbatim as data blocks, so it reads as source to anyone who fetches it, loads the runtime `openartifact.js` from the server and references its images relatively, so it is served by `backend/server.py` at `/artifacts/<id>/`, presents in a browser and prints to PDF with Chrome headless against that URL. That page is the "artifact".

An artifact has a **type**, set in `artifact.toml` and orthogonal to `theme`, which decides how its pages are laid out: `deck` shows one 16:9 page at a time with navigation and prints one per sheet; `document` stacks fixed-width sheets, one per page, that print to A4; `page` is a continuous, fluid column like a Notion page. The type decides which stylesheets the runtime injects; the pages themselves are built the same way for every type. A page may open with a directive comment, `<!-- class: cover light; title: Welcome -->`, and `page_component` in `artifact.toml` names a component rendered around every page with `{{ CONTENT }}`, which is where headers, footers and counters live. Components take parameters as attributes and children between their tags; `{{ PAGE_NUMBER }}`, `{{ PAGE_COUNT }}`, `{{ PAGE_TITLE }}` and the uppercase keys of the `[context]` table are substituted everywhere outside code.

The repo has two halves:

- **`frontend/`** - the browser runtime, `openartifact.js`. This is the only thing with a JavaScript build step, and it exists solely to be loaded by the output page. All rendering (markdown, components, code highlighting, navigation, build steps, print layout, the per-type layouts) happens here, in the browser.
- **`backend/build.py`** - the builder, a library module with no dependencies beyond the standard library and no CLI. It does no rendering: it validates the inputs (including that every referenced image exists), writes markdown, components and CSS verbatim into an HTML page as data blocks and links `openartifact.js` by URL.
- **`render/`** - the PDF render service: `render/pdf.py` drives Chrome headless against a served page URL, `render/server.py` exposes it as `POST /pdf/`, and `render/Dockerfile` is the image with Chromium in it. The application server calls it for `/artifacts/<id>.pdf`; the application image has no Chrome.
- **`backend/mcp_server.py`** - an MCP server for agents, built on `fastmcp` and `pydantic-monty`. `new_artifact` creates an artifact from markdown and builds it; `run_code` runs agent-written Python in a monty sandbox with the artifact's directory mounted read-write at `/artifact`, which is how the agent edits files; `build` calls `build.py` on that directory; `upload_url` mints signed URLs the agent `PUT`s local files to (`backend/upload.py`), so bytes never pass through a tool call; `list_artifacts` lists the caller's. Artifact identifiers are UUIDs. PDF export is not an MCP concern: it is the `/artifacts/<id>.pdf` URL on the server.
- **`backend/workspace.py`, `db.py`, `store.py`, `auth.py`** - storage and identity. Each user has a workspace: a git repository with one directory per artifact (`artifacts/<uuid>/`), kept as a working clone in a local cache, serialised as a `git bundle` into an object store (local directory or S3) under an immutable key per commit, with Postgres holding the current head and the users / workspaces / artifacts tables. The MCP endpoint is behind Google login (a static token for local development); artifact pages are public by UUID.
- **`backend/server.py`** - the FastAPI app that runs it all: the MCP server mounted at `/mcp/`, `openartifact.js` at `/openartifact.js`, and each artifact's page and media at `/artifacts/{uuid}/`, built on demand from the workspace checkout, with `.md`, `.zip` and `.pdf` exports beside it. `backend/main.py` is the entry point: it configures Logfire and serves `server.app`; `make dev` (uvicorn with reload) or `uv run backend/main.py` starts it.

The split is deliberate. The builder is thin enough to run anywhere Python 3.11 exists and to become a service later; the runtime is where the product lives.

### Where it is going

The service shape is now set: a FastAPI server hosting the MCP endpoint, with git-backed workspaces in an object store and Postgres. Next, in rough order: screenshots from the render service, a GitHub backend for workspaces (the local clone plus a `git push`/`fetch` remote instead of bundles, using a GitHub App with per-repo install so users pick the repo), orgs owning workspaces with several members, forking an artifact (copy a directory into another workspace with `forked_from` set), user-supplied themes stored the same way, and browser login for private pages. The schema already leaves room for each (`credentials`, `forked_from`, an owner column to relax, a `themes/` prefix in the repo). `build.py` stays the importable, side-effect-free builder throughout.

### Rules that follow from this

- **The builder stays dependency-free and programmatic.** No package, no console script, no argparse, no `print`, no third-party Python imports in `build.py` or `render/pdf.py`; they return paths and raise `BuildError` / `RenderError`. The project's runtime dependencies (`fastapi`, `uvicorn`, `fastmcp`, `pydantic-monty`, `logfire`, `asyncpg`, `obstore`) belong to the server modules, which import `build.py`, never the other way round.
- **Every change to an artifact is a commit.** File changes go through `workspace.edit()`, never by writing into a checkout directly; the commit, bundle upload and `head_sha` update happen together or not at all. Build output (`dist/`) is derived, gitignored and rebuilt on demand.
- **One cache directory per process.** The checkout cache is guarded by an in-process asyncio lock and the Postgres row lock; two processes sharing `OPENARTIFACT_CACHE_DIR` are not protected against each other.
- **The runtime does the work, the builder packages it.** If a feature can be implemented in `frontend/src/` it goes there. The builder only mirrors runtime logic where it lets a build fail early with a good error (see `validate_slides`).
- **Everything in the page is synchronous.** Headless Chrome prints at `load`, so the runtime must finish rendering before then. Never add async work to `main.ts`.
- **Output is served, not self-contained.** The page links `/openartifact.js` and references images relatively; `server.py` hosts the runtime at `/openartifact.js` and an artifact's images and fonts at `/artifacts/<id>/<path>`. Nothing is inlined as a data URI and `openartifact.js` is not copied next to the page. Opening `dist/index.html` from `file://` does not work and is not a goal.
- **A new artifact type is a stylesheet, not a fork.** Pages are built the same way for every type (`page.ts`); a type is how `main.ts` arranges them plus a stylesheet. Add the value to `TYPES` (build.py), `ArtifactType` (types.ts and mcp_server.py) and a `TYPE_STYLES` entry. Share `shared.css` and, for prose-like types, `prose.css`.
- **This is the library, not a deck.** Don't add brand-specific content, custom slides or example brand palettes here; those belong in user projects or in `examples/`. `examples/pennylane/` is a real brand deck kept locally for testing and is gitignored.

### Origin

Forked on 2026-09-30 from the `markdown-runtime` branch of [deckx](https://github.com/samuelcolvin/deckx), keeping its history. deckx shipped a Python package with a `deckx` CLI and a `deckx.toml` config file; here those are the single script and `artifact.toml`. The slide vocabulary (`.deck`, `DeckData`) was kept because it names the artifact, not the tool; the markdown file was `deck.md` until the `document` and `page` types arrived, and is now `main.md` for every type; the runtime bundle is `openartifact.js` because it names the tool.

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
make pg-start                         # Postgres 18 in Docker (docker-compose.yml) at DATABASE_URL's default; `make pg-stop` stops it
make dev                              # the server on the host at :8765 with reload, dev token `dev` (OPENARTIFACT_DEV_TOKEN overrides)
make docker-up                        # build the image (Dockerfile) and run server and database with compose; docker-down, docker-logs
PYTHONPATH=backend uv run python -c 'from pathlib import Path; import build; build.build_html(Path("examples/starter"))'  # by hand
OPENARTIFACT_DEV_TOKEN=dev uv run backend/main.py     # HTTP server on :8765: MCP at /mcp/ (bearer token `dev`), openartifact.js, artifact pages
uv run ruff check                     # lint backend/ and tests/
uv run ruff format                    # format them
uv run basedpyright                   # strict type check of backend/ and tests/
uv run pytest                         # run tests/
```

The `Makefile` wraps these: `make install`, `make format`, `make lint`, `make test`, `make main` (all three, the default goal), `make build`, plus the `pg-*`, `dev` and `docker-*` recipes above; `make help` lists them.

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

Two halves joined by the page's data blocks.

**Browser runtime (`frontend/src/`, bundled by esbuild to `frontend/dist/openartifact.js`)**

Paths below are relative to `frontend/`. `package.json`, `tsconfig.json` and `biome.jsonc` live there too.

- **`src/main.ts`** - entry. `readData()` assembles `ArtifactData` from the page's data blocks (`#artifact-config` JSON, `#artifact-markdown`, one `script[type="text/html"][data-component]` per component), reversing `encode_block` with `decodeBlock`; injects `shared.css`, the type's sheets (`TYPE_STYLES`) and `hljs.css` as `<style>` elements, then moves the page's own `<style id="artifact-styles">` (the user's CSS) to the end of `<head>` so it wins; splits the markdown with `splitPages` and builds every page with `buildPage`; a deck's pages go into `.deck-presenter > .deck` and navigation starts, a document's or page's into `.artifact-<type>`. Everything runs synchronously so the DOM is complete before `load` (headless Chrome prints at `load`). Never add async work here.
- **`src/split.ts`** - `splitPages`: splits raw markdown into `RawPage`s on `---` lines (fence-aware) and parses the leading directive comments (`class`, `title`; one or several comments, `;` or newline separated; a comment not starting with `key:` is left in the body). This is the source of truth for what a page is; `build.py` mirrors the scan to fail early.
- **`src/page.ts`** - `buildPage(page, index, total, data, config)`: renders the body into `div.page-body` (plus `prose` for the two prose types), computes `PAGE_TITLE` (directive, else first `h1`) and the page's context, wraps the body in the page component (`renderComponent` with the body as children; `{{ CONTENT }}` alone when there is none) inside `section.page[.classes]`, expands nested components and substitutes the context into the body's text. One code path for every type; `data-page-title` carries the title for `deck.ts`.
- **`src/render.ts`** - markdown-it (default preset, `html: true`) with highlight.js `lib/core` plus a curated language list.
- **`src/components.ts`** - `expandComponents(root, components, context)` replaces each `<component src>` tag, outermost first, with `renderComponent(source, attrs, context, children)`: the file's `<!-- params: ... -->` line is parsed off, one regex pass fills `{{ name }}` from the tag's attributes or the declared defaults (escaped), uppercase names from the context, and `{{ CONTENT }}` with a comment sentinel that is then replaced by the tag's live child nodes. Substitution happens on the string before parsing so placeholders work in attributes; nesting falls out of the loop. `unwrapParagraph` removes the `<p>` markdown-it puts around an inline tag. Images are untouched: relative `src` and CSS `url()` resolve against the page URL, which the server answers.
- **`src/substitute.ts`** - `PLACEHOLDER_RE` (mirrored in build.py), `escapeHtml`, `parseParams` and `substituteText(root, context)`, the tree walk that fills uppercase placeholders in the markdown's own text nodes, skipping `pre` and `code`.
- **`src/deck.ts`** - navigation: `#N` hash routing (1-indexed), keyboard, wheel, click delegation for `[data-nav="prev"|"next"|"first"|"last"]` elements a page component may contain, viewport scaling via `--page-scale`, `document.title` from the active page's `data-page-title`. Next/previous step through a page's build steps before changing page; shift + left/right jump a whole page. No chrome of its own.
- **`src/steps.ts`** - in-page build steps. Elements with `data-step="N"` (and optional `data-step-end="M"`) get a `data-step-state` of `pending` / `active` / `done`; the page gets `data-step`. Print state is precomputed into `data-step-print`. All appearance lives in `deck.css`, never in JS.
- **`src/types.ts`** - `ArtifactData` / `ArtifactConfig` / `ArtifactType`, the contract with `build.py`'s `render_page`.
- **`src/styles/shared.css`** - the CSS variables, theme token assignments and reset every type loads. User `styles.css` overrides the variables. The `--slide-*` token names are the user contract and stay as they are.
- **`src/styles/deck.css`** - the deck: page geometry, the 16:9 `@page`, transitions, `.page-body` padding, page typography, the directive classes (`.page.cover`, `.statement`, `.light` (which reassigns the `--slide-*` tokens), `.tight`, `.wide`, `.large`), the opt-in layout helpers (`.row`, `.col`, `.cols-2`, `.cols-3`, `.shrink`, `.small-code`, `.center`) and the build-step rules. Everything is scoped to `.page` / `.deck-presenter`. No header, footer or counter: those come from the page component.
- **`src/styles/prose.css`** - typography for `.page-body.prose`, shared by document and page: the deck typography at reading sizes, the same `markdown-*` decorations, and print rules that keep headings with their text and blocks unsplit.
- **`src/styles/document.css`** / **`page.css`** - the two layouts: each `.page` a centred sheet at `--document-width` with an A4 `@page` and `break-before: page` between pages in print, or pages stacked in a fluid column capped at `--page-max-width`. Each sets the body background and its own `@page`. Note the clash of words: `.artifact-page` is the root of `type = "page"`, `.page` is one page of any artifact.
- **`src/hljs.css`** - maps highlight.js token classes onto the brand variables, scoped to `.page, .prose`.

**Builder (`backend/build.py`)**

- A module, not a script: `build_html(directory, output=None, runtime_url='/openartifact.js')` is the entry point and returns the output path. There is no CLI; `mcp_server.py` and the tests are the callers.
- Loads and validates `artifact.toml` (`tomllib`; `type` in `TYPES`, `tabs` only for a deck, `favicon` a relative path that exists, `[context]` via `load_context`: uppercase keys, scalar values, no built-in names), reads `main.md` and splits it with `split_pages` (mirror of `split.ts`: `---` breaks outside fences, the directive comments; errors for an old `<slide .../>` marker, a `---` without a blank line before it, an empty page, an unknown or late directive), finds every component tag with `find_component_uses` (a regex scanner over the markdown with fenced and inline code blanked: attributes, children, nesting, the blank-line rules a block tag needs, self-closing and unclosed tags), loads each file once as a `ComponentSpec` (`.html` verbatim, `.svg` with its XML prolog stripped; `parse_params` reads the `<!-- params -->` line), and checks both directions with `check_component_file` and `check_component_use` (undeclared or unused parameters, missing required ones, children without `{{ CONTENT }}`, unknown uppercase names against `BUILTINS` plus the `[context]` keys; `check_body_placeholders` does the same for the markdown), checks every relatively referenced image exists inside the deck (`check_images`; nothing is embedded), rejects `</style` in the CSS (`check_styles`), and writes the `TEMPLATE` page: a `<link rel="alternate" type="text/markdown">` to the source, the CSS as a live `<style id="artifact-styles">`, the config as JSON (`<` as `\u003c`), the markdown and each component as a data block (`<script type="text/markdown">` / `<script type="text/html" data-component>`) encoded with `encode_block`, and `<script src="{runtime_url}">`. `encode_block` escapes only the `<` of `</script`, `<script` and `<!--` (the sequences that move the HTML tokenizer out of script data) as `&lt;`, after doubling any pre-existing `&amp;` / `&lt;`; `decode_block` is its inverse and `main.ts` mirrors it.
- No third-party runtime Python dependencies, and no knowledge of where `openartifact.js` lives on disk: that is `server.py`'s concern.

**Render service (`render/`)**

- `render/pdf.py`: `print_to_pdf(url, pdf_path)` runs Chrome headless (`find_chrome`: the macOS app bundle, then names on PATH) against the served page URL and returns the PDF path. No paper flags: each type's stylesheet sets `@page` (16:9 for a deck, A4 for the others) and Chrome honours it. It takes a URL, not a file, because the page loads `openartifact.js` and images from the server. A missing Chrome or a non-zero exit raises `RenderError` carrying the full command so it can be run by hand. `container_flags()` adds `--no-sandbox --disable-dev-shm-usage` when `RENDER_NO_SANDBOX` is set, which the image does. Standard library only.
- `render/server.py`: a FastAPI app with `POST /pdf/` (`{"url": ...}`, http(s) only, answers `application/pdf`; a Chrome failure is a 502 whose detail is the `RenderError`) and `GET /health/`. It prints any URL it is given, so it is internal: compose keeps it on the private network and binds it to 127.0.0.1 only for `make dev`. `render/Dockerfile` installs the `render` dependency group (`fastapi`, `uvicorn`, `httpx2`) and Debian's `chromium`; `make render-dev` runs it on the host with the local Chrome. `render` is a package imported from the repo root (`from render import pdf`), unlike `backend/`'s modules.

**Storage (`backend/db.py`, `store.py`, `workspace.py`, `config.py`)**

- `db.py`: `db_pool()` opens the asyncpg pool for the lifespan, `pool()` returns it. `migrate(conn)` applies `backend/migrations/NNNN_name.sql` in order under an advisory lock and records them in `schema_migrations`; versions must be consecutive. Tables: `users`, `credentials` (reserved for GitHub), `workspaces` (`head_sha`), `artifacts` (uuid pk, `workspace_id`, `title`, `type`, `forked_from`).
- `store.py`: `ObjectStore` over obstore with `get`/`put`/`exists`/`list`/`delete`, opened by `object_store()` from `OPENARTIFACT_STORE_URL`. Keys are immutable. A missing object raises `StoreMissing` (obstore reports it as `FileNotFoundError`).
- `workspace.py`: `checkout_path(ws)` is the clone in `config.cache_dir()`; `sync_checkout(ws, head_sha)` makes it match the head (fetching the bundle with `git fetch <file> main`, never `git clone`, then `reset --hard` and `clean`), deleting `dist/` for artifacts whose sources changed. `edit(ws, message)` is the write protocol: workspace asyncio lock, `SELECT ... FOR UPDATE` on the row with a 60s `lock_timeout` (`WorkspaceBusy`), sync, yield an `Edit` (`artifact_dir(id)`, `conn`, mutable `message`), then `git add -A`, commit if anything changed, `git bundle create`, upload to `workspaces/<ws>/<sha>.bundle`, `UPDATE workspaces SET head_sha`; any failure rolls back and the next sync repairs the checkout. `open_artifact(artifact)` is the read side. Row helpers: `insert_artifact`, `touch_artifact`, `get_artifact` (global, for public pages), `get_artifact_in` (scoped, for tools), `list_artifacts`, `import_directory`. Git runs via `asyncio.create_subprocess_exec` with a fixed env (`GIT_ENV`) and a Logfire span per call.
- `config.py`: `base_url()`, `cache_dir()`, `render_url()`, `internal_url()` and `secret_key()`, read from the environment on each call so tests can monkeypatch them.

**Auth (`backend/auth.py`)**

- `make_auth_provider()` builds the FastMCP `auth=`: `GoogleProvider` when `GOOGLE_CLIENT_ID` is set (needs `GOOGLE_CLIENT_SECRET` and `OPENARTIFACT_SECRET_KEY`, a Fernet key; the OAuth proxy's state lives in Postgres through `PostgreSQLStore` wrapped in `FernetEncryptionWrapper`, table `oauth_state`), else `StaticTokenVerifier` with `OPENARTIFACT_DEV_TOKEN`, else a startup error. Tests set the dev token in `conftest.py` before importing the server.
- `current_principal()` turns the verified token's claims (`sub`, `email`, `name`, `picture`) into a `Principal(user_id, workspace_id, email)` via `upsert_user`, creating the user's workspace on first sight, and caches by `sub`. `as_principal(p)` overrides it for code outside an MCP request (tests). Not authenticated is a `ToolError`.

**MCP server (`backend/mcp_server.py`)**

- `FastMCP` server named `openartifact`, mounted into `server.py` (it has no entry point of its own). Its instructions, the text an agent reads before calling anything, are `backend/instructions.md`, loaded by `load_instructions()` with `{VIRTUAL_PATH}` and `{SKILL_URI}` filled in; edit the file, not the module. It also serves `skills/openartifact/` as an agent skill through FastMCP's `SkillProvider` (`SKILL_DIR`): the resource `skill://openartifact/SKILL.md` (`SKILL_URI`), a `_manifest` and a file template; the server instructions tell agents to read it. The Docker image copies `skills/` for this reason. The tool functions `new_artifact`, `run_code`, `build_artifact` (registered as `build`) and `list_artifacts` are plain coroutines so tests call them directly under `auth.as_principal`. Nothing in them blocks the event loop: the sandbox is `AsyncMonty` and the builder runs via `asyncio.to_thread`.
- The `AsyncMonty` worker pool is opened by the `monty_pool()` async context manager and lives for the app's lifespan. It is bound to the loop that opened it, so never create pools lazily or at import time. Tests use a `pool` fixture.
- `new_artifact(title, content, type, theme, build=True)` is the only way an artifact is created: inside one `edit()` it writes `artifact.toml` (`render_toml`, JSON string escaping is valid TOML) and `main.md` under `artifacts/<uuid4>/` and inserts the row; then it builds unless `build=False`, for content that refers to components or images the agent has still to add. A build failure is returned as the `ToolError` but the artifact exists and is listed, for `run_code` to fix. `type` and `theme` are Literals kept equal to `build.TYPES` / `build.THEMES` by a test. `page_component` and `[context]` are not parameters; the agent edits `artifact.toml` for those.
- `run_code(artifact, code, inputs)` resolves the UUID within the caller's workspace (`resolve`; another workspace's artifact is reported as missing), mounts the directory read-write at `/artifact` inside an `edit()`, and commits even when the sandbox raised (message `run_code (failed): <id>`) before re-raising, so partial writes persist. Printed output and the trailing expression value are returned.
- `build(artifact)` runs `build.build_html` in a thread under `open_artifact`; output lands in the checkout's `dist/`, where the sandbox can read it, and the returned URL is `OPENARTIFACT_BASE_URL/artifacts/<uuid>/`.
- `upload_url(artifact, files)` takes `(path, size)` pairs and returns one URL per file, `PUT /artifacts/<uuid>/<path>?token=...`, for the agent to `curl -T` a local file to. It writes nothing itself. `backend/upload.py` holds the contract: `validate_path` (relative, normalised, no `.git*`, not under `dist/`), `validate_size` (10 MB cap), and `make_token` / `verify_token`, an HMAC-SHA256 over artifact, path, size and expiry (one hour) keyed from `config.secret_key()` (`OPENARTIFACT_SECRET_KEY`, else the dev token). Signing the size is what lets the server accept uploads without storing anything about the mint. The route is in `server.py`.
- The mount must contain only artifact data, never code the host executes; keep the cache directory off `sys.path`.

**HTTP server (`backend/server.py`)**

- `mcp.http_app(path='/')` mounted at `/mcp`. The app's `lifespan` enters FastMCP's lifespan (its session manager will not start otherwise), `db.db_pool()`, `store.object_store()` and `mcp_server.monty_pool()`, then runs migrations. The MCP endpoint is `/mcp/`.
- `/openartifact.js` serves `frontend/dist/openartifact.js` (`RUNTIME_JS_PATH`); every built page loads it from there. `/artifacts/{uuid}/` looks the artifact up (any workspace: pages are public by UUID), syncs the checkout under `open_artifact`, builds `dist/index.html` if missing (a `BuildError` is a 422) and returns the HTML read under the lock. `/artifacts/{uuid}/{path}` serves images and fonts, and the source files (`SOURCE_EXTS`: `main.md` as `text/markdown`, `artifact.toml`, `styles.css` and `components/*` as `text/plain`) from the artifact directory after the containment and extension check in `contained_file`; anything under `dist/` is a 404, as is a non-UUID. The sources are public because the page already contains them, and `main.md` is what an agent should read instead of parsing the page. `PUT /artifacts/{uuid}/{path}?token=...` accepts an upload for a URL the `upload_url` tool minted: `Content-Length` is required (411) and must be the signed size, the token must verify (403), the body is read in full before the workspace lock is taken, then the file is written and committed as `upload: <path>` inside `workspace.edit()`; the JSON answer carries the file's `sha256`. A path that resolves outside the artifact through a symlink is a 403, a directory in the way a 409, a busy workspace a 409. Three exports sit beside the page, routed before the bare `/artifacts/{uuid}` redirect: `/artifacts/{uuid}.md` is the markdown source behind a YAML frontmatter summary (`artifact_summary`: id, title, type and theme from `artifact.toml` where it parses, else the row; url, created_at, updated_at, and `files`, the source files from `source_files`, which skips `dist/` and `.git*`); `/artifacts/{uuid}.zip` is those files zipped under a folder named by the id; `/artifacts/{uuid}.pdf` builds the page (422 on failure), then asks the render service at `config.render_url()` to print `config.internal_url()/artifacts/{uuid}/` and relays the PDF (503 when no render URL is configured, 502 when the service is unreachable or fails). The artifact lock is released before the render service is called: it fetches the page from this process, which takes the same lock. `/` returns a JSON index without artifacts; `list_artifacts` is per user.
- Configuration is by environment: `HOST`, `PORT` (default 8765), `DATABASE_URL`, `OPENARTIFACT_STORE_URL`, `OPENARTIFACT_CACHE_DIR`, `OPENARTIFACT_BASE_URL`, `OPENARTIFACT_RENDER_URL` (the render service; unset disables `.pdf`), `OPENARTIFACT_INTERNAL_URL` (this server as the render service reaches it, default the base URL), the auth variables above, and `LOGFIRE_TOKEN` to send telemetry.
- `Dockerfile` builds the application image (`make docker-up` builds and runs it): a node stage bundles `openartifact.js`, a uv stage installs the locked dependencies, and the final `python:3.14-slim` image adds `git`, runs as a non-root user and defaults the cache and local store to `/data`. Chrome is not in it; `render/Dockerfile` is the render image. `make docker-up` runs both images against the compose Postgres, the app on :8765 with the dev token (`OPENARTIFACT_PORT` moves the host port) pointed at `render` over the compose network, and `render` bound to 127.0.0.1:8766 for `make dev`. The compose database and the image's `/data` volume belong together: a `head_sha` whose bundle is not in the configured store is reported as such by `sync_checkout`.
- Observability: `main.py` calls `logfire.configure()`, `logfire.instrument_asyncpg()` and `logfire.instrument_monty()`; `server.py` never touches Logfire, so tests importing it send nothing. FastAPI's built-in telemetry and FastMCP's native spans pick up Logfire's global providers on their own, so do not add `logfire.instrument_fastapi` or `logfire.instrument_mcp`; they would duplicate spans. FastAPI is created with `telemetry={'auto_configure': False}` so it never adds OTLP exporters of its own.

**Supporting files**

- `skills/openartifact/SKILL.md` - the authoring guide, also served to agents as the MCP skill. It holds what every artifact needs and is kept short because agents load it whole; the rest is read on demand from `references/styles.md` (the CSS variable contract, layout helpers, class hooks, brand palettes), `references/steps.md` (deck build steps) and `references/local.md` (installing, building and printing by hand). Update them whenever page syntax, config keys or the CSS contract change, and keep SKILL.md pointing at each reference.
- `examples/create_examples.py` - creates every example through the MCP server (`new_artifact`, one `run_code` that writes the files from `inputs`, `build`), the way an agent would; run it against `make dev` to smoke-test the tools. The gitignored `mcp_demo.py` at the root is the shorter walkthrough of the same.
- `examples/starter/` - smoke-test deck exercising every feature (a page component with header and counter, directives, components with parameters and children, nesting, an SVG component, `[context]`, a light page, build steps, code). `examples/document/` (two pages, a page component with a footer) and `examples/page/` are the same for the other two types.
- `tests/test_build.py` - pytest for `backend/build.py` (imported as `build`; pytest adds `backend/` to `pythonpath`): the page splitter and directives, the component scanner and parameter checks, the context table, the page component, the block codec, and the examples end to end.
- `tests/test_render.py` - pytest for `render/`: `pdf.py`'s command assembly and error paths, and the `/pdf/` endpoint with `TestClient`; Chrome is a shell script. No Postgres.
- `tests/test_upload.py` - pytest for `backend/upload.py`: path and size validation, the token round trip, expiry and key selection; no Postgres.
- `tests/conftest.py` - creates a throwaway database per session on the server at `DATABASE_URL` (`make pg-start` first), migrates it, and provides `db_pool`, `storage` (file store and cache in `tmp_path`), `principal` and `server_env` (for tests that run the whole app). Tests needing Postgres are the ones using those fixtures; `test_build.py` and `test_pdf.py` run without it.
- `tests/test_db.py`, `test_store.py`, `test_workspace.py` - the storage layer, including the edit protocol's failure paths (body error, upload failure, a second cache syncing from the bundle, stale `dist/` removal).
- `tests/test_mcp_server.py` - the tool functions directly as a signed-in user (`auth.as_principal`), plus one in-memory `fastmcp.Client` round trip.
- `tests/test_server.py` - the HTTP routes with `TestClient` (seeding through `client.portal` so rows and checkouts belong to the app's loop), plus MCP over real HTTP against a uvicorn thread with the dev token.

## The page's data blocks

```html
<link rel="alternate" type="text/markdown" href="main.md">
<style id="artifact-styles">/* raw styles.css */</style>
<script type="application/json" id="artifact-config">{"type": "deck", "title": "...", "theme": "light", "tabs": [...]}</script>
<script type="text/markdown" id="artifact-markdown">
raw main.md source
</script>
<script type="text/html" data-component="Hero.html">
raw Hero.html
</script>
```

The non-JavaScript `type` makes a `<script>` an inert data block whose content stays an exact string; the only escaping is `encode_block`'s. Images are not in the page. The page is served at `/artifacts/<id>/`, so relative image paths in the markdown, components and CSS resolve to `/artifacts/<id>/<path>`, which `server.py` serves from the artifact directory. The builder only checks that each referenced file exists.

## Page syntax in one paragraph

A line containing only `---`, with a blank line before it, ends a page, in every type; `***` is a rule inside a page. A page may open with `<!-- class: cover light; title: Welcome -->` (`class` adds classes to `section.page`, the built-in ones being `cover`, `statement`, `light`, `tight`, `wide`, `large`; `title` sets `PAGE_TITLE`, otherwise the first `h1`). `page_component` in `artifact.toml` names a component rendered around every page with `{{ CONTENT }}`; the built-ins `PAGE_NUMBER`, `PAGE_COUNT`, `PAGE_TITLE` and the `[context]` keys are substituted in the markdown and in components, outside code. Any element inside a deck page can carry `data-step="N"` / `data-step-end="M"` to take part in build steps. `<component src="Name.html" title="x">children</component>` needs its closing tag and, when it has children, a blank line after the opening tag and before the closing one; attributes fill the component's declared `{{ name }}` parameters, children fill `{{ CONTENT }}`; `.svg` components are inlined as markup. Uppercase `{{ KEY }}` placeholders come from `[context]` and the built-ins. See `SKILL.md` for the full reference.

## Code style

Add concise but thorough JSDoc-style comments on exported functions, and docstrings on Python functions. Comment any line of code that's complex or esoteric; otherwise let well-named identifiers speak. TypeScript is formatted by biome (single quotes, no semicolons, 119 columns); Python by ruff (single quotes, 120 columns).
