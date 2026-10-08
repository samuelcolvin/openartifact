# Running OpenArtifact locally

Part of the OpenArtifact skill, for working without the MCP server: the builder and the PDF printer are plain Python modules in the repo. `SKILL.md` covers the format.

## Installation

OpenArtifact is not packaged. Clone the repo and build the browser runtime once (this is the only step that needs Node):

```bash
git clone https://github.com/samuelcolvin/openartifact
cd openartifact && pnpm -C frontend install && pnpm -C frontend build     # -> frontend/dist/openartifact.js
```

The builder is the module `backend/build.py` in that checkout, with PDF printing in `chrome/pdf.py`. Both need Python 3.11+ and nothing else. Below, `CHECKOUT` stands for the path to that checkout. The server (`make pg-start`, then `make dev`) hosts the MCP endpoint and serves the built pages at `/artifacts/<id>/`, with `/artifacts/<id>.md`, `.zip`, `.pdf`, (decks) `.pptx` and (documents) `.docx` beside them; all but the first two need the chrome service (`make chrome-dev`, or the container from `make docker-up`).

## Building by hand

```bash
# build to ./dist/index.html
PYTHONPATH=CHECKOUT/backend python3 -c 'from pathlib import Path; import build; build.build_html(Path("."))'
# then a PDF via Chrome headless, from the page the server is serving
PYTHONPATH=CHECKOUT python3 -c 'from pathlib import Path; from chrome import pdf; pdf.print_to_pdf("http://127.0.0.1:8765/artifacts/<id>/", Path("deck.pdf"))'
```

The page is not self-contained: it loads `openartifact.js` from the server and its images relatively, so view and print it through the server rather than from `file://`. `build_html(directory, output=None, runtime_url='/openartifact.js')` takes an optional output path; `print_to_pdf(url, pdf_path)` prints a served page. Input problems raise `build.BuildError` naming the file and line.

## PDF

If Chrome / Chromium can't be found, or it exits with an error, the `ChromeError` message carries the exact command: copy it and run it yourself with the right binary path. On Linux `pdf.find_chrome` auto-detects `google-chrome`, `google-chrome-stable`, `chromium`, or `chromium-browser`.

The paper size comes from each type's stylesheet (`@page`): 16:9 for a deck, A4 for the others. If you override `--slide-width` / `--slide-height` in `styles.css`, add a matching `@page { size: ... }` there too.

To spot-check the PDF (requires `pdftoppm` from poppler):

```bash
mkdir -p ./tmp && pdftoppm -r 100 ./dist/deck.pdf ./tmp/page -png
```

One PNG per printed page lands in `./tmp/`, gitignore that path.
