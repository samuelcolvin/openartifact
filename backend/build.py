"""Build an artifact page from markdown, HTML components and CSS.

An artifact directory looks like:

    artifact.toml   type, title, theme, page_component, [context], path overrides (all optional)
    main.md         the content: for a deck one `<slide .../>` line starts each slide; a document or page is plain markdown
    styles.css      CSS variable overrides (optional)
    components/     HTML or SVG files pulled in with <component src="Name.html"></component>
    assets/         images referenced from the markdown, components or styles

Nothing is rendered here. The markdown source, every referenced component and the user's
styles are written into the page verbatim as data blocks (see TEMPLATE), so the page reads as source to anyone
who fetches it, and `openartifact.js` (the browser runtime, built from frontend/src with `pnpm build`) renders
the artifact when the page loads. The output is `dist/index.html`. It is not self-contained: it links `openartifact.js` by URL (`/openartifact.js` by default)
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
    # Relative to `cwd`, as written in artifact.toml; the page links it relatively, so the server resolves it.
    favicon: str | None = None
    # A component file (in `components_dir`) rendered once around every page's body, with `{{ CONTENT }}`.
    page_component: str | None = None
    # The `[context]` table: uppercase keys available as `{{ KEY }}` everywhere a built-in is.
    context: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        """The `config` data block, matching `ArtifactConfig` in frontend/src/types.ts."""
        data: dict[str, object] = {'type': self.type, 'theme': self.theme}
        if self.title is not None:
            data['title'] = self.title
        if self.page_component is not None:
            data['page_component'] = self.page_component
        if self.context:
            data['context'] = self.context
        return data


def load_context(raw: object) -> dict[str, str]:
    """Validate the `[context]` table: uppercase names that are not built-ins, with scalar values stringified."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BuildError('artifact.toml: [context] must be a table of UPPERCASE keys')
    context: dict[str, str] = {}
    for key, value in cast('dict[str, object]', raw).items():
        if not CONTEXT_NAME_RE.match(key):
            raise BuildError(f'artifact.toml: [context] key {key!r} must be uppercase ([A-Z][A-Z0-9_]*)')
        if key in BUILTINS:
            raise BuildError(f'artifact.toml: [context] key {key!r} is a built-in')
        if isinstance(value, bool):
            context[key] = 'true' if value else 'false'
        elif isinstance(value, (str, int, float)):
            context[key] = str(value)
        else:
            raise BuildError(f'artifact.toml: [context] value for {key!r} must be a string, number or boolean')
    return context


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
    for key in ('footer', 'tabs'):
        if key in raw:
            raise BuildError(
                f'artifact.toml: `{key}` is no longer supported; render it from a `page_component` instead, '
                'which can use {{ PAGE_NUMBER }}, {{ PAGE_COUNT }} and {{ PAGE_TITLE }}'
            )

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

    page_component = optional_str('page_component') or None
    if page_component and not page_component.lower().endswith('.html'):
        raise BuildError(
            f'artifact.toml: `page_component` must be an .html file in components/, got {page_component!r}'
        )

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
        favicon=favicon,
        page_component=page_component,
        context=load_context(raw.get('context')),
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
# Placeholders: parameters, built-ins and context
# ---------------------------------------------------------------------------

# Mirrors PLACEHOLDER_RE in frontend/src/substitute.ts. Lowercase names are a component's parameters, uppercase
# names are built-ins or `[context]` keys; the runtime substitutes, the builder only checks.
PLACEHOLDER_RE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')
PARAM_NAME_RE = re.compile(r'^[a-z_][a-z0-9_]*$')
CONTEXT_NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')
# `CONTENT` is the markup a component wraps; the `PAGE_*` values describe the page being rendered.
BUILTINS = frozenset({'CONTENT', 'PAGE_NUMBER', 'PAGE_COUNT', 'PAGE_TITLE'})
# The optional first line of a component file, `<!-- params: title, icon="x" -->`, and one declaration in it.
PARAMS_DIRECTIVE_RE = re.compile(r'^\s*<!--\s*params:\s*(?P<decl>.*?)\s*-->[ \t]*\r?\n?', re.DOTALL)
PARAM_DECL_RE = re.compile(r"""\s*(?P<name>[^\s,=]+)(?:\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'))?\s*(?:,|$)""")
# `{{ CONTENT }}` inside a quoted attribute value: it inserts markup, which an attribute cannot hold.
CONTENT_IN_ATTR_RE = re.compile(r"""=\s*(?:"[^"]*\{\{\s*CONTENT\s*\}\}|'[^']*\{\{\s*CONTENT\s*\}\})""")


def parse_params(source: str, path: Path) -> tuple[dict[str, str | None], str]:
    """Split a component file into its declared parameters (`None` = required) and the body that follows."""
    match = PARAMS_DIRECTIVE_RE.match(source)
    if not match:
        return {}, source
    params: dict[str, str | None] = {}
    decl, pos = match.group('decl'), 0
    while pos < len(decl):
        item = PARAM_DECL_RE.match(decl, pos)
        if not item or item.end() == pos:
            raise BuildError(
                f'{path}:1: cannot parse <!-- params: ... --> at {decl[pos:]!r}; '
                'write names separated by commas, with optional defaults: title, icon="x"'
            )
        name = item.group('name')
        if not PARAM_NAME_RE.match(name):
            raise BuildError(f'{path}:1: parameter {name!r} must match [a-z_][a-z0-9_]*')
        if name == 'src':
            raise BuildError(f"{path}:1: parameter name 'src' is reserved for the component file")
        if name in params:
            raise BuildError(f'{path}:1: parameter {name!r} is declared twice')
        default = item.group('dq') if item.group('dq') is not None else item.group('sq')
        params[name] = default
        pos = item.end()
    return params, source[match.end() :]


def describe_params(params: dict[str, str | None]) -> str:
    """`title, icon='x'`: how a component's parameters are listed in error messages."""
    return ', '.join(name if default is None else f'{name}={default!r}' for name, default in params.items()) or '(none)'


def line_of(text: str, pos: int) -> int:
    """1-based line number of offset `pos` in `text`."""
    return text.count('\n', 0, pos) + 1


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

# An opening tag with its raw attribute text (so attribute case is visible), or a closing tag. Mirrors what the
# browser's parser will see; the builder is deliberately stricter about the surrounding lines.
COMPONENT_TAG_RE = re.compile(
    r'<component\b(?P<attrs>(?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s"\'=<>`]+))?)*)\s*(?P<self>/?)>'
    r'|</component\s*>',
    re.IGNORECASE,
)
ATTR_RE = re.compile(r"""([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
COMPONENT_START_RE = re.compile(r'<component\b[^>]*>', re.IGNORECASE)
COMPONENT_EXTS = '.html', '.svg'
# The XML prolog and doctype that editors put at the top of an SVG file. They are meaningless once the
# SVG is inline in an HTML document and the HTML parser would turn them into a bogus comment.
SVG_PROLOG_RE = re.compile(r'^\s*(?:<\?xml[^>]*\?>\s*|<!DOCTYPE[^>]*>\s*)*', re.IGNORECASE)
INLINE_CODE_RE = re.compile(r'(`+)(.+?)\1(?!`)')


@dataclass
class ComponentUse:
    """One `<component src="...">...</component>` in a file."""

    src: str
    attrs: dict[str, str]
    path: Path
    line: int
    has_content: bool

    def describe(self) -> str:
        return f'{self.path}:{self.line}: <component src="{self.src}">'


@dataclass
class ComponentSpec:
    """A component file as loaded: what is embedded in the page, plus what the checks need."""

    src: str
    path: Path
    source: str
    params: dict[str, str | None]
    body: str
    # Line number of the first body line in the file (2 when there is a declaration).
    body_offset: int

    def placeholders(self) -> list[tuple[str, int]]:
        """`(name, line)` for every `{{ name }}` in the body."""
        return [
            (m.group(1), self.body_offset + line_of(self.body, m.start()) - 1)
            for m in PLACEHOLDER_RE.finditer(self.body)
        ]

    def content_slots(self) -> int:
        return sum(1 for name, _line in self.placeholders() if name == 'CONTENT')


def fenced_line_numbers(source: str) -> set[int]:
    """The complement of `unfenced_lines`: every line inside a fenced block, fence lines included."""
    unfenced = {number for number, _line in unfenced_lines(source)}
    return {number for number in range(1, source.count('\n') + 2) if number not in unfenced}


def blank_code(source: str) -> str:
    """`source` with fenced blocks and inline code spans replaced by whitespace, line numbers preserved.

    Tags and placeholders shown in code are examples, not references, and must not be checked as such.
    """
    fenced = fenced_line_numbers(source)
    lines = ['' if number in fenced else line for number, line in enumerate(source.split('\n'), start=1)]
    return INLINE_CODE_RE.sub(lambda m: ' ' * len(m.group(0)), '\n'.join(lines))


def find_component_uses(text: str, path: Path, *, markdown: bool) -> list[ComponentUse]:
    """Every component tag in `text`, with the checks that need the raw source.

    `markdown=True` (main.md) also blanks code and enforces the blank lines CommonMark needs around a block-level
    tag: without a blank line after the opening tag the children are not rendered as markdown, and without one
    before the closing tag the browser drops it and the component swallows the rest of the page.
    """
    if markdown:
        text = blank_code(text)
    lowered = text.lower()
    uses: list[ComponentUse] = []
    stack: list[tuple[re.Match[str], str, dict[str, str], int]] = []
    seen_end = 0
    for match in COMPONENT_TAG_RE.finditer(text):
        # A `<component` the regex could not read as a tag (an unterminated quote, say) is left between matches.
        if (stray := lowered.find('<component', seen_end, match.start())) != -1:
            raise BuildError(f'{path}:{line_of(text, stray)}: cannot parse this <component> tag; check its quotes')
        seen_end = match.end()
        number = line_of(text, match.start())
        if match.group(0).startswith('</'):
            if not stack:
                raise BuildError(f'{path}:{number}: </component> without an opening <component> tag')
            opening, src, attrs, open_line = stack.pop()
            inner = text[opening.end() : match.start()]
            if markdown and open_line != number:
                check_block_component_lines(text, opening, match, src, path, open_line)
            uses.append(ComponentUse(src, attrs, path, open_line, has_content=inner.strip() != ''))
            continue
        if match.group('self'):
            raise BuildError(
                f'{path}:{number}: self-closing <component .../> is not valid HTML; '
                'write <component src="Name.html"></component>'
            )
        if '\n' in match.group(0):
            raise BuildError(f'{path}:{number}: the opening <component> tag must be on one line')
        attrs: dict[str, str] = {}
        for attr in ATTR_RE.finditer(match.group('attrs')):
            name = attr.group(1)
            value = next((v for v in attr.groups()[1:] if v is not None), '')
            if name != 'src' and not PARAM_NAME_RE.match(name):
                raise BuildError(
                    f'{path}:{number}: attribute {name!r} on <component> must match [a-z_][a-z0-9_]* '
                    '(the HTML parser lowercases attribute names)'
                )
            if name in attrs:
                raise BuildError(f'{path}:{number}: attribute {name!r} given twice on <component>')
            if markdown and PLACEHOLDER_RE.search(value):
                raise BuildError(
                    f'{path}:{number}: {value!r} in an attribute of <component> is not substituted; '
                    'use the placeholder inside the component file instead'
                )
            attrs[name] = value
        src = attrs.pop('src', None)
        if src is None:
            raise BuildError(f'{path}:{number}: <component> needs a src attribute')
        stack.append((match, src, attrs, number))
    if (stray := lowered.find('<component', seen_end)) != -1:
        raise BuildError(f'{path}:{line_of(text, stray)}: cannot parse this <component> tag; check its quotes')
    if stack:
        _opening, src, _attrs, open_line = stack[-1]
        raise BuildError(f'{path}:{open_line}: <component src="{src}"> is never closed')
    return uses


def check_block_component_lines(
    text: str, opening: re.Match[str], closing: re.Match[str], src: str, path: Path, open_line: int
) -> None:
    """For a tag alone on its line with its closing tag on a later line, require the blank lines CommonMark needs."""
    before_open = text[: opening.start()].rsplit('\n', 1)[-1]
    after_open = text[opening.end() :].split('\n', 1)[0]
    if before_open.strip() or after_open.strip():
        return  # inline use inside a paragraph: ordinary inline HTML, no block rules
    inner_lines = text[opening.end() : closing.start()].split('\n')
    # inner_lines[0] is the rest of the opening line (blank), [1] the next line, [-1] the start of the closing line.
    if len(inner_lines) < 3 or inner_lines[1].strip():
        raise BuildError(
            f'{path}:{open_line}: put a blank line after <component src="{src}"> so its children are rendered as markdown'
        )
    if inner_lines[-2].strip():
        raise BuildError(
            f'{path}:{line_of(text, closing.start())}: put a blank line before </component>, otherwise the browser '
            'treats it as text and the component swallows the rest of the page'
        )


def collect_components(
    roots: list[tuple[str, Path]], components_dir: Path, names: frozenset[str], page_component: str | None = None
) -> dict[str, ComponentSpec]:
    """Load every component reachable from the markdown `roots` and the page component, checking each file and
    each use; keyed by `src`.

    `names` are the uppercase placeholders that are valid everywhere: the built-ins plus the `[context]` keys.
    """
    specs: dict[str, ComponentSpec] = {}

    def visit(text: str, path: Path, chain: tuple[str, ...], *, markdown: bool) -> None:
        for use in find_component_uses(text, path, markdown=markdown):
            if use.src in chain:
                raise BuildError(f'component cycle: {" -> ".join((*chain, use.src))}')
            spec = specs.get(use.src)
            if spec is None:
                spec = load_component(use.src, components_dir, chain)
                check_component_file(spec, names)
                specs[use.src] = spec
                visit(spec.body, spec.path, (*chain, use.src), markdown=False)
            check_component_use(use, spec)

    for text, path in roots:
        visit(text, path, (), markdown=True)
    if page_component is not None:
        spec = specs.get(page_component)
        if spec is None:
            spec = load_component(page_component, components_dir, ())
            check_component_file(spec, names)
            specs[page_component] = spec
            visit(spec.body, spec.path, (page_component,), markdown=False)
        check_page_component(spec)
    return specs


def check_page_component(spec: ComponentSpec) -> None:
    """A page component wraps the body exactly once and is rendered with no attributes."""
    slots = spec.content_slots()
    if slots != 1:
        raise BuildError(f'{spec.path}: a page component must contain exactly one {{{{ CONTENT }}}}, found {slots}')
    for name, default in spec.params.items():
        if default is None:
            raise BuildError(
                f'{spec.path}:1: a page component is rendered without attributes, so parameter {name!r} needs a default'
            )


def load_component(src: str, components_dir: Path, chain: tuple[str, ...]) -> ComponentSpec:
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
    if path.suffix.lower() == '.svg':
        source = SVG_PROLOG_RE.sub('', source, count=1)
        if not source.lstrip().lower().startswith('<svg'):
            raise BuildError(f'{path}: expected an <svg> root element')
    params, body = parse_params(source, path)
    body_offset = source[: len(source) - len(body)].count('\n') + 1
    return ComponentSpec(src=src, path=path, source=source, params=params, body=body, body_offset=body_offset)


def check_component_file(spec: ComponentSpec, names: frozenset[str]) -> None:
    """Every placeholder in a component is a declared parameter or a known uppercase name, and vice versa."""
    used: set[str] = set()
    for name, line in spec.placeholders():
        if name in spec.params:
            used.add(name)
        elif name not in names:
            raise BuildError(
                f'{spec.path}:{line}: unknown placeholder {{{{ {name} }}}}; declared parameters: '
                f'{describe_params(spec.params)}; built-ins and [context]: {", ".join(sorted(names))}'
            )
    for name in spec.params:
        if name not in used:
            raise BuildError(f'{spec.path}:1: parameter {name!r} is declared but never used')
    if spec.content_slots() > 1:
        raise BuildError(f'{spec.path}: {{{{ CONTENT }}}} may appear only once')
    if (attr := CONTENT_IN_ATTR_RE.search(spec.body)) is not None:
        line = spec.body_offset + line_of(spec.body, attr.start()) - 1
        raise BuildError(
            f'{spec.path}:{line}: {{{{ CONTENT }}}} is markup and cannot be used inside an attribute value'
        )


def check_component_use(use: ComponentUse, spec: ComponentSpec) -> None:
    """The tag passes only declared parameters, all required ones, and children only where there is a slot."""
    for name in use.attrs:
        if name not in spec.params:
            raise BuildError(f'{use.describe()} has no parameter {name!r}; declared: {describe_params(spec.params)}')
    for name, default in spec.params.items():
        if default is None and name not in use.attrs:
            raise BuildError(f'{use.describe()} is missing required parameter {name!r}')
    if use.has_content and spec.content_slots() == 0:
        raise BuildError(f'{use.describe()} has children but {spec.src} has no {{{{ CONTENT }}}}')


def check_body_placeholders(markdown: str, path: Path, names: frozenset[str]) -> None:
    """Uppercase placeholders in the markdown must be known; lowercase ones are ordinary text and left alone."""
    text = blank_code(markdown)
    for match in PLACEHOLDER_RE.finditer(text):
        name = match.group(1)
        if not name[0].isupper():
            continue
        number = line_of(text, match.start())
        if name == 'CONTENT':
            raise BuildError(f'{path}:{number}: {{{{ CONTENT }}}} is only available inside a component')
        if name not in names:
            raise BuildError(
                f'{path}:{number}: unknown placeholder {{{{ {name} }}}}; '
                f'built-ins and [context]: {", ".join(sorted(names))}'
            )


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
        # A <component src="X.svg"> is a component reference, not an image; its children may still hold images.
        text = COMPONENT_START_RE.sub('', text)
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

# The whole output page. Placeholders are replaced with plain string substitution (not `str.format`, so braces in
# the substituted values are safe). The source files go in as data blocks: `<script>` elements with a non-JavaScript
# `type` are never executed and keep their content as an exact string, which is what the runtime needs and what a
# reader fetching the page wants to see. The user's CSS is a live `<style>` so it applies without the runtime.
TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{favicon}
<link rel="alternate" type="text/markdown" href="{markdown_href}">
<style id="artifact-styles">
{styles}</style>
</head>
<body>
<div id="root"></div>
<script type="application/json" id="artifact-config">{config}</script>
<script type="text/markdown" id="artifact-markdown">
{markdown}</script>
{components}<script src="{runtime}"></script>
</body>
</html>
"""
COMPONENT_BLOCK = '<script type="text/html" data-component="{name}">\n{source}</script>\n'

# Inside script data the HTML tokenizer reacts to exactly three sequences: `</script` ends the element, and `<!--`
# followed by `<script` puts it in a state where `</script>` no longer does. Escaping the `<` of those as `&lt;`
# (and protecting any `&amp;` / `&lt;` already in the text) leaves everything else readable; the runtime reverses it.
BLOCK_AMP_RE = re.compile(r'&(?=(?:amp|lt);)')
BLOCK_LT_RE = re.compile(r'<(?=/?script|!--)', re.IGNORECASE)
BLOCK_DECODE_RE = re.compile(r'&(amp|lt);')
STYLE_END_RE = re.compile(r'</style', re.IGNORECASE)


def encode_block(text: str) -> str:
    """Make `text` safe as the content of a data block; `decode_block` (and the runtime) reverse it exactly."""
    return BLOCK_LT_RE.sub('&lt;', BLOCK_AMP_RE.sub('&amp;', text))


def decode_block(text: str) -> str:
    """The inverse of `encode_block`; mirrors `decodeBlock` in frontend/src/main.ts (used by the tests)."""
    return BLOCK_DECODE_RE.sub(lambda m: '<' if m.group(1) == 'lt' else '&', text)


def check_styles(css: str, path: Path) -> None:
    """The user's CSS goes into a live `<style>`, where `</style` would end it early; there is no way to escape it."""
    for number, line in enumerate(css.splitlines(), start=1):
        if STYLE_END_RE.search(line):
            raise BuildError(
                f"{path}:{number}: '</style' cannot appear in CSS embedded in the page; inside a string write '<\\/style'"
            )


def build_page_data(cfg: Config) -> dict[str, object]:
    """Validate the inputs and gather what goes into the page: `config`, `markdown`, `components`, `styles`."""
    markdown = cfg.markdown_path.read_text(encoding='utf-8')
    if cfg.type == 'deck':
        validate_slides(markdown, cfg.markdown_path)
    else:
        validate_prose(markdown, cfg.markdown_path, cfg.type)
    names = BUILTINS | frozenset(cfg.context)
    check_body_placeholders(markdown, cfg.markdown_path, names)
    specs = collect_components([(markdown, cfg.markdown_path)], cfg.components_dir, names, cfg.page_component)
    components = {src: spec.source for src, spec in specs.items()}
    styles = cfg.styles_path.read_text(encoding='utf-8') if cfg.styles_path.is_file() else ''
    check_styles(styles, cfg.styles_path)
    check_images([markdown, *components.values(), styles], cfg.cwd)
    return {
        'config': cfg.to_json(),
        'markdown': markdown,
        'components': components,
        'styles': styles,
    }


def render_page(title: str, favicon: str | None, data: dict[str, object], runtime_url: str, markdown_href: str) -> str:
    """Fill TEMPLATE: title, favicon link, the data blocks, the user's CSS and the URL of the runtime.

    `markdown_href` is where the server serves the markdown source relative to the page (`main.md` by default); it
    is advertised with `<link rel="alternate">` so a reader can find the source without parsing the page.
    """
    # `<` in the JSON is written as `\u003c`, which is still JSON, so a title cannot contain `</script>`.
    config = json.dumps(data.get('config', {}), ensure_ascii=False).replace('<', '\\u003c')
    markdown = cast('str', data.get('markdown', ''))
    components = cast('dict[str, str]', data.get('components', {}))
    styles = cast('str', data.get('styles', ''))
    favicon_tag = f'\n<link rel="icon" href="{html.escape(favicon, quote=True)}">' if favicon else ''
    component_blocks = ''.join(
        COMPONENT_BLOCK.replace('{name}', html.escape(name, quote=True)).replace('{source}', encode_block(source))
        for name, source in components.items()
    )
    return (
        TEMPLATE.replace('{title}', html.escape(title))
        .replace('{favicon}', favicon_tag)
        .replace('{markdown_href}', html.escape(markdown_href, quote=True))
        .replace('{styles}', styles)
        .replace('{config}', config)
        .replace('{markdown}', encode_block(markdown))
        .replace('{components}', component_blocks)
        .replace('{runtime}', html.escape(runtime_url, quote=True))
    )


def build_html(cwd: Path, output: Path | None = None, runtime_url: str = DEFAULT_RUNTIME_URL) -> Path:
    """Build `cwd` into one HTML file (default `cwd/dist/index.html`) that loads the runtime from `runtime_url`.

    The page must be served with the deck's images reachable relative to it; `server.py` does that at
    `/artifacts/<id>/`. Returns the path written.
    """
    cfg = load_config(cwd)
    data = build_page_data(cfg)
    markdown_href = cfg.markdown_path.relative_to(cfg.cwd).as_posix()
    page = render_page(cfg.title or cfg.markdown_path.stem, cfg.favicon, data, runtime_url, markdown_href)
    out = (output or cwd / 'dist' / 'index.html').resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding='utf-8')
    return out
