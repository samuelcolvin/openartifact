"""Build an artifact page from markdown, HTML components and CSS.

An artifact directory looks like:

    artifact.toml   type, title, theme, footer, tabs, path overrides (all optional)
    main.md         the content: for a deck one `<slide .../>` line starts each slide; a document or page is plain markdown
    styles.css      CSS variable overrides (optional)
    components/     HTML or SVG files pulled in with <component src="Name.html"></component>
    assets/         images referenced from the markdown, components or styles

Nothing is rendered here. The markdown source, every referenced component and the user's
styles are written into the page as one JSON blob, and `openartifact.js` (the browser runtime, built
from frontend/src with `pnpm build`) renders the deck when the page loads. The output is
`dist/index.html`. It is not self-contained: it links `openartifact.js` by URL (`/openartifact.js` by default)
and leaves image references relative, so it is meant to be served by `server.py`, which
hosts the runtime and the artifact's images. Referenced images are checked to exist so a
build fails early; `pdf.py` prints the served page.

This module is used programmatically, by `mcp_server.py` and the tests; `build_html(directory)` is the entry
point. It has no dependencies beyond the standard library (Python 3.11+).
"""

from __future__ import annotations

import html
import json
import posixpath
import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

# Where the page loads the runtime from; `server.py` serves it there.
DEFAULT_RUNTIME_URL = '/openartifact.js'

# The overall form of the artifact; mirrors `ArtifactType` in frontend/src/types.ts. `deck` is slides, `document` a
# fixed-width sheet that prints to pages, `page` a continuous fluid page.
TYPES = 'deck', 'document', 'page'
THEMES = 'light', 'dark', 'markdown-light', 'markdown-dark'
IMAGE_EXTS = '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'
FAVICON_EXTS = '.svg', '.png', '.ico', '.jpg', '.jpeg'


class BuildError(Exception):
    """A problem in the input files, reported without a traceback."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Resolved artifact.toml with defaults applied and paths made absolute."""

    cwd: Path
    markdown_path: Path
    styles_path: Path
    components_dir: Path
    type: str = 'deck'
    title: str | None = None
    theme: str = 'light'
    footer: str | None = None
    # Relative to `cwd`, as written in artifact.toml; the page links it relatively, so the server resolves it.
    favicon: str | None = None
    tabs: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        """The `config` field of the JSON blob, matching `ArtifactConfig` in frontend/src/types.ts."""
        data: dict[str, object] = {'type': self.type, 'theme': self.theme, 'tabs': self.tabs}
        if self.title is not None:
            data['title'] = self.title
        if self.footer is not None:
            data['footer'] = self.footer
        return data


def load_config(cwd: Path) -> Config:
    """Load artifact.toml from `cwd` if present and validate it. A missing main.md is fatal."""
    cwd = cwd.resolve()
    toml_path = cwd / 'artifact.toml'
    raw: dict[str, object] = {}
    if toml_path.is_file():
        try:
            raw = tomllib.loads(toml_path.read_text(encoding='utf-8'))
        except tomllib.TOMLDecodeError as exc:
            raise BuildError(f'{toml_path}: {exc}') from exc

    for key in ('code_light_theme', 'code_dark_theme', 'mdx'):
        if key in raw:
            raise BuildError(f'artifact.toml: `{key}` is no longer supported (code is highlighted in the browser)')

    def optional_str(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise BuildError(f'artifact.toml: `{key}` must be a string, got {value!r}')
        return value

    markdown_path = cwd / (optional_str('markdown') or 'main.md')
    if not markdown_path.is_file():
        raise BuildError(f'markdown file not found: {markdown_path}')

    artifact_type = optional_str('type') or 'deck'
    if artifact_type not in TYPES:
        raise BuildError(f'artifact.toml: invalid type {artifact_type!r}. Valid values: {", ".join(TYPES)}')

    theme = optional_str('theme') or 'light'
    if theme not in THEMES:
        raise BuildError(f'artifact.toml: invalid theme {theme!r}. Valid values: {", ".join(THEMES)}')

    raw_tabs = raw.get('tabs', [])
    if artifact_type != 'deck' and raw_tabs:
        raise BuildError(f'artifact.toml: `tabs` are only used when type = "deck", not {artifact_type!r}')
    if not isinstance(raw_tabs, list):
        raise BuildError('artifact.toml: `tabs` must be an array of {id, label} tables')
    tabs: list[dict[str, str]] = []
    for tab in raw_tabs:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(tab, dict):
            raise BuildError(f'artifact.toml: every `tabs` entry needs string `id` and `label`, got {tab!r}')
        entry = cast('dict[str, object]', tab)
        tab_id, label = entry.get('id'), entry.get('label')
        if not (isinstance(tab_id, str) and isinstance(label, str)):
            raise BuildError(f'artifact.toml: every `tabs` entry needs string `id` and `label`, got {tab!r}')
        tabs.append({'id': tab_id, 'label': label})

    favicon = optional_str('favicon') or None
    if favicon:
        if is_external(favicon):
            raise BuildError(f'artifact.toml: `favicon` must be a relative path inside the deck, got {favicon!r}')
        favicon_path = cwd / favicon
        if not favicon_path.is_file():
            raise BuildError(f'favicon not found: {favicon_path}')
        if favicon_path.suffix.lower() not in FAVICON_EXTS:
            raise BuildError(
                f'unsupported favicon extension {favicon_path.suffix!r}. Use one of: {", ".join(FAVICON_EXTS)}'
            )

    return Config(
        cwd=cwd,
        markdown_path=markdown_path,
        styles_path=cwd / (optional_str('styles') or 'styles.css'),
        components_dir=cwd / (optional_str('components') or 'components'),
        type=artifact_type,
        title=optional_str('title'),
        theme=theme,
        footer=optional_str('footer'),
        favicon=favicon,
        tabs=tabs,
    )


# ---------------------------------------------------------------------------
# Slide structure
# ---------------------------------------------------------------------------

# Mirrors SLIDE_RE / FENCE_RE in frontend/src/split.ts; the runtime does the real split.
SLIDE_RE = re.compile(r'^\s*<slide\b[^>]*?\s*/?>\s*$', re.IGNORECASE)
FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
SELF_CLOSING_COMPONENT_RE = re.compile(r'<component\b[^>]*/>', re.IGNORECASE)


def unfenced_lines(source: str) -> Iterator[tuple[int, str]]:
    """Yield `(line number, line)` for every line outside a fenced code block, fence lines excluded."""
    fence: str | None = None
    for number, line in enumerate(source.splitlines(), start=1):
        fence_match = FENCE_RE.match(line)
        if fence is None:
            if fence_match:
                fence = fence_match.group(1)
                continue
            yield number, line
        elif fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
            if not line[fence_match.end() :].strip():
                fence = None


def check_components_closed(line: str, path: Path, number: int) -> None:
    """A self-closing `<component .../>` is not valid HTML: the parser would swallow everything after it."""
    if SELF_CLOSING_COMPONENT_RE.search(line):
        raise BuildError(
            f'{path}:{number}: self-closing <component .../> is not valid HTML; write <component src="Name.html"></component>'
        )


def validate_slides(source: str, path: Path) -> int:
    """Check a deck's slide structure the way the runtime will read it; returns the slide count.

    Errors: content before the first `<slide/>` line, no slides at all, or a self-closing `<component .../>`.
    """
    count = 0
    for number, line in unfenced_lines(source):
        if SLIDE_RE.match(line):
            count += 1
            continue
        if count == 0 and line.strip():
            raise BuildError(f'{path}:{number}: content before the first <slide .../> line: {line.strip()!r}')
        check_components_closed(line, path, number)
    if count == 0:
        raise BuildError(f'{path}: no slides found; start each slide with a <slide .../> line')
    return count


def validate_prose(source: str, path: Path, artifact_type: str) -> None:
    """Check a document or page: the markdown is rendered whole, so slide markers are a mistake."""
    if not source.strip():
        raise BuildError(f'{path}: no content')
    for number, line in unfenced_lines(source):
        if SLIDE_RE.match(line):
            raise BuildError(
                f'{path}:{number}: <slide .../> markers are only used when type = "deck"; this artifact is a '
                f'{artifact_type}, so write plain markdown'
            )
        check_components_closed(line, path, number)


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

COMPONENT_RE = re.compile(r"""<component\s+src=(["'])(?P<src>[^"']+)\1\s*>\s*</component>""", re.IGNORECASE)
COMPONENT_EXTS = '.html', '.svg'
# The XML prolog and doctype that editors put at the top of an SVG file. They are meaningless once the
# SVG is inline in an HTML document and the HTML parser would turn them into a bogus comment.
SVG_PROLOG_RE = re.compile(r'^\s*(?:<\?xml[^>]*\?>\s*|<!DOCTYPE[^>]*>\s*)*', re.IGNORECASE)


def collect_components(body: str, components_dir: Path) -> dict[str, str]:
    """Load every component reachable from `body`, following nested references; keyed by `src`."""
    components: dict[str, str] = {}

    def visit(source_html: str, chain: tuple[str, ...]) -> None:
        for match in COMPONENT_RE.finditer(source_html):
            src = match.group('src')
            if src in chain:
                raise BuildError(f'component cycle: {" -> ".join((*chain, src))}')
            if src in components:
                continue
            components[src] = load_component(src, components_dir, chain)
            visit(components[src], (*chain, src))

    visit(body, ())
    return components


def load_component(src: str, components_dir: Path, chain: tuple[str, ...]) -> str:
    """Read one component file, refusing paths that leave the components directory.

    `.html` files are used verbatim. `.svg` files are inlined as SVG markup (so they can use the deck's CSS
    variables, which an `<img>` cannot) with any XML prolog and doctype removed.
    """
    if not components_dir.is_dir():
        raise BuildError(f'components directory not found: {components_dir} (needed for <component src="{src}">)')
    path = (components_dir / src).resolve()
    if components_dir.resolve() not in path.parents:
        raise BuildError(f'component src {src!r} escapes {components_dir}')
    referenced_from = f' (referenced from {chain[-1]})' if chain else ''
    if not path.is_file():
        raise BuildError(f'component not found: {path}{referenced_from}')
    if path.suffix.lower() not in COMPONENT_EXTS:
        raise BuildError(f'component {src!r} must be one of {", ".join(COMPONENT_EXTS)}{referenced_from}')
    source = path.read_text(encoding='utf-8')
    if SELF_CLOSING_COMPONENT_RE.search(source):
        raise BuildError(f'{path}: self-closing <component .../> is not valid HTML')
    if path.suffix.lower() == '.svg':
        source = SVG_PROLOG_RE.sub('', source, count=1)
        if not source.lstrip().lower().startswith('<svg'):
            raise BuildError(f'{path}: expected an <svg> root element')
    return source


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

# `src="..."` in HTML, `![alt](path)` in markdown, `url(...)` in CSS.
HTML_SRC_RE = re.compile(r"""\bsrc\s*=\s*(["'])(?P<path>[^"']+)\1""", re.IGNORECASE)
MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(\s*(?P<path>[^)\s]+)')
CSS_URL_RE = re.compile(r"""url\(\s*(["']?)(?P<path>[^"')]+)\1\s*\)""", re.IGNORECASE)


def is_external(path: str) -> bool:
    """True for URLs, data URIs and absolute paths, which are left alone."""
    return path.startswith('/') or re.match(r'^[a-z][a-z0-9+.-]*:', path, re.IGNORECASE) is not None


def check_images(texts: list[str], base: Path) -> list[str]:
    """Find relative image references in `texts` and check each file exists; returns the normalised paths, sorted.

    Nothing is embedded: the browser fetches the images from the server relative to the page URL. The check only
    makes a typo fail the build instead of showing a broken image.
    """
    found: list[str] = []
    for text in texts:
        # A <component src="X.svg"> is a component reference, not an image.
        text = COMPONENT_RE.sub('', text)
        for regex in HTML_SRC_RE, MD_IMAGE_RE, CSS_URL_RE:
            for match in regex.finditer(text):
                raw = match.group('path')
                if is_external(raw) or not raw.lower().endswith(IMAGE_EXTS):
                    continue
                key = posixpath.normpath(raw)
                if key in found:
                    continue
                if key.startswith('..'):
                    raise BuildError(f'image {raw!r} escapes the deck directory')
                if not (base / key).is_file():
                    raise BuildError(f'image not found: {base / key} (referenced as {raw!r})')
                found.append(key)
    return sorted(found)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

# The whole output page. `{title}`, `{favicon}`, `{blob}` and `{runtime}` are replaced with plain string substitution
# (not `str.format`, so braces in the substituted values are safe).
TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{favicon}
</head>
<body>
<div id="root"></div>
<script type="application/json" id="artifact-data">{blob}</script>
<script src="{runtime}"></script>
</body>
</html>
"""


def build_deck_data(cfg: Config) -> dict[str, object]:
    """Assemble the JSON blob the runtime reads; shape matches `ArtifactData` in frontend/src/types.ts."""
    markdown = cfg.markdown_path.read_text(encoding='utf-8')
    if cfg.type == 'deck':
        validate_slides(markdown, cfg.markdown_path)
    else:
        validate_prose(markdown, cfg.markdown_path, cfg.type)
    components = collect_components(markdown, cfg.components_dir)
    styles = cfg.styles_path.read_text(encoding='utf-8') if cfg.styles_path.is_file() else ''
    check_images([markdown, *components.values(), styles], cfg.cwd)
    return {
        'config': cfg.to_json(),
        'markdown': markdown,
        'components': components,
        'styles': styles,
    }


def render_page(title: str, favicon: str | None, data: dict[str, object], runtime_url: str) -> str:
    """Fill TEMPLATE with the title, favicon link, JSON blob and the URL of the runtime."""
    # `<` is escaped to the JSON sequence `\u003c`, which is still valid JSON. That defeats `</script>` and the
    # `<!--` sequence, which would otherwise put the HTML tokenizer into a state where the real
    # closing tag is ignored. Component HTML can contain both.
    blob = json.dumps(data, ensure_ascii=False, indent=2).replace('<', '\\u003c')
    favicon_tag = f'\n<link rel="icon" href="{html.escape(favicon, quote=True)}">' if favicon else ''
    return (
        TEMPLATE.replace('{title}', html.escape(title))
        .replace('{favicon}', favicon_tag)
        .replace('{blob}', blob)
        .replace('{runtime}', html.escape(runtime_url, quote=True))
    )


def build_html(cwd: Path, output: Path | None = None, runtime_url: str = DEFAULT_RUNTIME_URL) -> Path:
    """Build `cwd` into one HTML file (default `cwd/dist/index.html`) that loads the runtime from `runtime_url`.

    The page must be served with the deck's images reachable relative to it; `server.py` does that at
    `/artifacts/<id>/`. Returns the path written.
    """
    cfg = load_config(cwd)
    data = build_deck_data(cfg)
    page = render_page(cfg.title or cfg.markdown_path.stem, cfg.favicon, data, runtime_url)
    out = (output or cwd / 'dist' / 'index.html').resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding='utf-8')
    return out
