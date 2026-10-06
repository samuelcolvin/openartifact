---
name: openartifact
description: Create a deck, document or page with OpenArtifact. Use when the user mentions "openartifact", "deck", "slides" or asks to build a slide deck, a printable document or a web page from markdown, to convert a brand palette into an OpenArtifact stylesheet, or to turn an OpenArtifact artifact into a PDF. Covers project layout, artifact.toml config (type, theme, page_component, [context]), main.md authoring with --- page breaks and <!-- class: ...; title: ... --> directives, HTML components with parameters and {{ CONTENT }}, the PAGE_NUMBER / PAGE_COUNT / PAGE_TITLE built-ins, images, code blocks, the styles.css token contract, and the Chrome headless PDF command.
---

# OpenArtifact

OpenArtifact builds one HTML page from one markdown file plus a CSS theme, an optional folder of HTML components and any images they reference. An artifact is made of pages, split on `---` lines; its `type` decides whether they show as slides (`deck`), as printable sheets (`document`) or as one continuous web page (`page`). The page renders itself in the browser and converts to PDF via Chrome headless. Building needs Python (via `uv`) and nothing else.

## Installation

OpenArtifact is not packaged. Clone the repo and build the browser runtime once (this is the only step that needs Node):

```bash
git clone https://github.com/samuelcolvin/openartifact
cd openartifact && pnpm -C frontend install && pnpm -C frontend build     # -> frontend/dist/openartifact.js
```

The builder is the module `backend/build.py` in that checkout, with PDF printing in `backend/pdf.py`. Both need Python 3.11+ and nothing else. The usual way to use them is through the MCP server (`make pg-start`, then `make dev`; tools `new_artifact`, `run_code`, `build`, `upload_url`, `list_artifacts`; artifacts are identified by the UUID `new_artifact` returns); by hand, import them with `PYTHONPATH=CHECKOUT/backend`. Below, `CHECKOUT` stands for the path to that checkout.

## Project layout

```
my-deck/
├── artifact.toml          # config: type, title, theme, page_component, [context], paths (all optional)
├── main.md             # the content: markdown, pages separated by ---
├── styles.css          # CSS variable overrides (optional)
├── components/         # optional HTML or SVG files pulled in with <component src="...">
│   └── Hero.html
└── assets/             # images referenced from the markdown, components or styles
```

```bash
# build to ./dist/index.html
PYTHONPATH=CHECKOUT/backend python3 -c 'from pathlib import Path; import build; build.build_html(Path("."))'
# then a PDF via Chrome headless, from the page the server is serving
PYTHONPATH=CHECKOUT/backend python3 -c 'from pathlib import Path; import pdf; pdf.print_to_pdf("http://127.0.0.1:8765/artifacts/<id>/", Path("deck.pdf"))'
```

The page is not self-contained: it loads `openartifact.js` from the server and its images relatively, so view and print it through the server rather than from `file://`. `build_html(directory, output=None, runtime_url='/openartifact.js')` takes an optional output path; `print_to_pdf(url, pdf_path)` prints a served page. Input problems raise `build.BuildError` naming the file and line.

## Artifact types

Every artifact is a list of pages: `main.md` split on lines containing only `---`. `type` in `artifact.toml` picks how those pages are laid out. It is independent of `theme`, which only picks colours.

| `type`     | What it is                                                         | How the pages show                                   | Printing                                        |
| ---------- | ------------------------------------------------------------------ | ---------------------------------------------------- | ----------------------------------------------- |
| `deck`     | Slides with next/previous navigation and build steps              | One 16:9 page at a time, scaled to the window        | one page per sheet, 16:9                        |
| `document` | Fixed-width sheets, like a word processor, centred on the screen   | Stacked sheets, one per page                         | A4, each page on a new sheet, headings kept with their text |
| `page`     | A continuous, fluid page, like a Notion page or a Claude artifact  | One column; several pages simply follow each other   | A4 with ordinary margins                        |

The default is `deck`. A deck page must fit **279.4mm × 157.2mm** (16:9); a document page is as long as its content and prints across as many sheets as it needs. Navigation and build steps apply to decks only; components, images, code blocks, the page component, the CSS variables and the `markdown-*` decorations apply to all three.

Document and page add two variables to override in `styles.css`: `--document-width` / `--document-padding` (sheet size, default A4 with 20mm padding) and `--page-max-width` (column cap, default 52rem). To print a document on US paper, add `@page { size: letter; }` to `styles.css`. Hook type-specific rules on `.artifact-document` / `.artifact-page` (the artifact root); each page is `section.page` and the rendered markdown sits in `.page-body` (`.page-body.prose` for the two prose types).

## `artifact.toml`

All fields are optional - an artifact with only `main.md` works.

```toml
type = "deck"                         # deck | document | page   (default: deck)
title = "My Deck - April 2026"        # browser tab title, and the fallback when a page has no title or h1

# light | dark | markdown-light | markdown-dark   (default: light)
# "markdown-*" variants render source-style decorations on top:
# heading "#"/"##" prefixes, "**" strong markers, diamond bullets.
theme = "light"

# A component rendered once around every page, with {{ CONTENT }} where the page body goes.
# This is where a header, a footer, a page counter or a logo lives; see "The page component".
page_component = "Page.html"

# Relative path to a favicon for the browser tab. .svg / .png / .ico / .jpg.
# Served by the server next to the artifact's other images.
favicon = "assets/favicon.svg"

# Path overrides (defaults shown).
markdown = "main.md"
styles = "styles.css"
components = "components"

# Global variables. Keys must be UPPERCASE; values are strings, numbers or booleans.
# Available as {{ KEY }} in the markdown and in every component, next to the built-ins.
# Tables must come last in a TOML file, so keep [context] at the end.
[context]
AUTHOR = "Jane Doe"
DATE = "October 2026"
```

There is no `footer` or `tabs` key: both are a few lines of HTML in the page component, which can use `{{ PAGE_NUMBER }}` and `{{ PAGE_COUNT }}`.

## `main.md`

Plain markdown (CommonMark plus GFM tables and strikethrough). A line containing only `---` ends a page; the next page starts on the following line. The first page starts at the top of the file.

```markdown
<!-- class: cover; title: Investor Deck / April 2026 -->

### Section Label

# My Deck

## A subtitle

---

# Hello world

- Bullet one
- Bullet two

<component src="Hero.html"></component>

---

<!-- class: statement -->

# One big idea.
```

Rules:

- Put a blank line before every `---`. Directly under a line of text, `---` would be a heading underline in markdown; the blank line makes it a page break everywhere, and the build insists on it.
- `---` is always a page break, so draw a horizontal rule inside a page with `***`.
- `---` inside a fenced code block is ignored, so you can show the syntax in a code sample.
- An empty page is a build error, and so is the old `<slide .../>` marker.
- Use `-` (hyphen-minus), never `—` (em dash).

### Page directives

A page may start with a directive comment, before any content:

```markdown
<!-- class: cover light; title: Welcome -->
```

- `class` adds classes to the page element (`section.page`), space separated. The built-in ones are listed below; any other name is a hook for your `styles.css`.
- `title` sets the page's `PAGE_TITLE` (see "Built-in placeholders") and, in a deck, the browser tab title while the page is shown. Without it the page's first `h1` is used, then the artifact `title`.
- Both keys may share one comment, separated by `;` or by newlines, or each may have its own comment. A `;` only separates entries when a key follows it, so a title may contain one.
- A comment that does not start with `key:` is an ordinary HTML comment and is left alone. A directive comment after the page's content is a build error, as is an unknown key.

### Built-in page classes

- `cover` - a cover or section page. Bottom-aligns the hero; h1 is 4rem with tight letter-spacing; h2 renders in `--accent`. Pair with a leading `### Section Label` for a mono uppercase eyebrow.
- `statement` - a centered one-liner. The body is centered both vertically and horizontally; h1 is 3.4rem; paragraphs cap at 80% width. Use for transitions between sections or "one bold idea" beats.
- `light` - forces this page onto the light palette (`--bg-light`, `--color-text-light`, `--color-heading-light`) regardless of the artifact theme. Useful when one page needs to break out, e.g. a screenshot of a light-themed UI on an otherwise dark deck. There is no inverse: on a light theme, a single dark page is a class of your own plus a few lines of CSS.
- `tight` - reduces bullet and paragraph spacing; try it first when a deck page is close to overflowing, then drop content.
- `wide` - increases padding and line-height, for pages with very little text.
- `large` - bumps body text from 1.15rem to 1.35rem and h1/h2 proportionally, for decks read from the back of a room.

### Built-in placeholders

`{{ PAGE_NUMBER }}` (1-based), `{{ PAGE_COUNT }}` and `{{ PAGE_TITLE }}` can be written anywhere in the markdown and in any component, and are replaced per page. The keys of `[context]` in `artifact.toml` work the same way. Inside code they are left alone. In a component file `{{ CONTENT }}` is the component's children; it is not available in the markdown itself. Unknown uppercase placeholders fail the build; lowercase `{{ x }}` in the markdown is just text.

### The page component

`page_component = "Page.html"` in `artifact.toml` names a component rendered once around every page. It takes no attributes; `{{ CONTENT }}` is the rendered page body, and the built-ins above describe the page. Everything that used to be deck chrome is written here, so it is yours to style:

```html
<header class="page-header">
  <span>{{ PAGE_TITLE }}</span>
  <nav>
    <button data-nav="prev">&larr;</button>
    {{ PAGE_NUMBER }} / {{ PAGE_COUNT }}
    <button data-nav="next">&rarr;</button>
  </nav>
</header>
{{ CONTENT }}
<footer>Confidential - {{ DATE }}</footer>
```

- `{{ CONTENT }}` must appear exactly once. The body it inserts is `<div class="page-body">` (plus `prose` for documents and pages), which carries the page padding; a header or footer outside it sits flush with the page edge.
- In a deck, an element with `data-nav="prev" | "next" | "first" | "last"` navigates when clicked, and `<a href="#3">` jumps to page 3. These controls are hidden when printing.
- For a document the component wraps each sheet, so a footer lands at the bottom of each printed page's content; for a `page` artifact it wraps the whole column.
- The component may declare parameters, but since nothing passes attributes they all need defaults. It may use other components.
- `examples/starter/components/Page.html` is a complete header with traffic lights, the title and a counter.

### Inline HTML and components

Short HTML can sit directly in the markdown - a `<mark>`, a small `<div class="note">`, an `<img>`. Anything longer belongs in a file under `components/` and is pulled in with a component tag:

```markdown
<component src="Hero.html"></component>
```

- The closing `</component>` is required. The self-closing form is not real HTML (the parser would swallow everything after it) and the build rejects it.
- `src` is relative to the `components` directory and may not escape it.
- Components are plain HTML files. They may contain other `<component>` tags (nesting is resolved in the browser; cycles fail the build) and may reference images the same way the markdown does.
- A component can also be an `.svg` file. It is inlined as SVG markup rather than as an image, so it can use the deck's CSS variables (`fill="var(--accent)"`, `font-family="var(--font-mono)"`) and follows the theme. Any XML prolog or doctype is stripped. Use `![](assets/x.svg)` instead when the SVG is a fixed picture that should not pick up the theme.
- Scripts inside components do not run. Components see the same CSS variables your `styles.css` defines, so read from variables (`color: var(--accent)`) rather than hard-coding colors.

#### Parameters and children

A component takes parameters as attributes on the tag and its children as the markdown between the tags:

```markdown
<component src="Card.html" title="Fast builds" icon="1">

Body text, rendered as **markdown** because of the blank lines.

</component>
```

```html
<!-- params: title, icon="*" -->
<div class="card">
  <h3>{{ icon }} {{ title }}</h3>
  {{ CONTENT }}
</div>
```

- The first line of the component declares its parameters: `<!-- params: title, icon="*" -->`. A name without a default is required; `icon="*"` has a default. Names are lowercase (`[a-z_][a-z0-9_]*`), because the HTML parser lowercases attribute names. `src` is reserved.
- `{{ title }}` is replaced by the attribute's value as escaped text, so it is safe in text and inside attribute values alike (`<a href="{{ href }}">`).
- `{{ CONTENT }}` is replaced by the tag's children as markup, at most once per component. A component without it cannot take children; a component with it can still be used empty (`<component src="Card.html" title="x"></component>`).
- Uppercase placeholders are global: the `[context]` keys from `artifact.toml` plus the built-ins `PAGE_NUMBER`, `PAGE_COUNT` and `PAGE_TITLE`. They work in components and in the markdown itself, except inside code. Lowercase `{{ x }}` in the markdown is plain text.
- When the tag holds children, put it on its own line, leave a blank line after the opening tag and a blank line before `</component>`. Without them CommonMark does not render the children as markdown and the browser drops the closing tag, so the component swallows the rest of the page. Without children, `<component src="X.html"></component>` can sit on one line, or inline in a sentence.
- Substitution is plain text replacement: no expressions, loops or conditionals. Generate repeated markup with code in `run_code` and write the tags out.
- The build checks everything and names the file and line: a parameter the tag passes but the component does not declare (listing the declared ones), a required parameter that is missing, an undeclared `{{ name }}` in the component, a declared parameter that is never used, children on a component without `{{ CONTENT }}`, an uppercase name that is neither built-in nor in `[context]`, and the missing blank lines above.

### Build steps

A deck page can reveal its content in steps, like Keynote builds. Put `data-step="N"` on any element, in the markdown or inside a component, and it stays hidden until the slide reaches step N. Add `data-step-end="M"` to hide it again after step M. The slide's step count is the highest step mentioned plus one; a slide with no `data-step` attributes has a single step.

The next/previous keys, the wheel and any `data-nav` control in the page component step through a page's builds before moving to the next page, and a slide entered backwards opens on its last step. Shift+Right and Shift+Left jump a whole slide, skipping the builds, and land on the target's first step.

```html
<ul>
  <li>Always visible</li>
  <li data-step="1">Appears on the first press</li>
  <li data-step="2">Appears on the second press</li>
</ul>
```

Mutually exclusive frames (a diagram that changes rather than grows) are ranges. Stack them with `position: absolute` or a one-cell grid so they occupy the same space:

```html
<div style="position: relative; height: 20rem;">
  <img data-step="0" data-step-end="0" src="assets/before.svg" style="position: absolute; inset: 0;">
  <img data-step="1" data-step-end="1" src="assets/during.svg" style="position: absolute; inset: 0;">
  <img data-step="2" src="assets/after.svg" style="position: absolute; inset: 0;">
</div>
```

Hidden elements keep their layout (`visibility: hidden`, so build-up lists don't reflow) and fade in when revealed. The runtime only writes `data-step-state` (`pending` | `active` | `done`) on stepped elements and `data-step` on the slide; the look is plain CSS you can override from `styles.css`, for example to dim finished elements instead of hiding them:

```css
[data-step-state='done'] { visibility: visible; opacity: 0.35; }
```

PDF output shows every page at its final step.

### Images

Reference images by path relative to the deck directory, from markdown, from a component, or from `url(...)` in `styles.css`:

```markdown
![Architecture](assets/architecture.png)

<img src="assets/logo.svg" alt="Logo" style="width: 96px">
```

Image paths are relative to the deck directory and stay that way in the page: the server serves the page at `/artifacts/<id>/` and the deck's `.png` / `.jpg` / `.gif` / `.svg` / `.webp` files under it, so the browser fetches them from there. The build checks each referenced file exists and fails if one is missing or points outside the deck. Absolute URLs (`https://...`) are left alone.

Through the MCP server, get images (or any local file: fonts, components, a long `main.md`) into the artifact with `upload_url`: pass `[(path, size), ...]`, the path each file will have inside the artifact and its exact size in bytes, and `PUT` each file to the URL returned for it (create the artifact with `build=False` first if `main.md` already refers to them, then `build` once they are uploaded):

```bash
curl -T assets/logo.png "https://.../artifacts/<id>/assets/logo.png?token=..."
```

The response is JSON with the file's `sha256`; compare it with `shasum -a 256 assets/logo.png`. Each upload is one commit. URLs last an hour and take up to 10 MB per file; the size must match exactly.

### Code blocks

Fenced code blocks are syntax-highlighted in the browser by [highlight.js](https://highlightjs.org). Tag the fence with a language so tokens get coloured:

````markdown
```ts
export function greet(name: string): string {
  return `hello, ${name}`;
}
```
````

Bundled grammars: `typescript` (`ts`, `tsx`), `javascript` (`js`, `jsx`), `python` (`py`), `bash` (`sh`, `zsh`), `json`, `toml` / `ini`, `css`, `xml` / `html` / `svg`, `sql`, `rust` (`rs`), `go`, `yaml` (`yml`), `markdown` (`md`), `diff`. Other languages render as plain, unhighlighted code.

Token colours derive from the deck's accent variables (`--accent`, `--accent-secondary`, `--accent-tertiary`, `--accent-aqua`, `--color-muted`) and are deepened automatically on light slides, so a brand palette in `styles.css` restyles code too. To tune them directly, override `--code-keyword`, `--code-string`, `--code-number`, `--code-title`, `--code-attr`, `--code-comment` or `--code-meta` on `.slide`.

## Authoring `styles.css`

The base stylesheet handles all layout, typography, page dimensions, transitions, and the PDF `@page` setup. `styles.css` only needs to override CSS variables on `:root` to set brand tokens.

### Variable contract

Backgrounds:

- `--bg-deck` (default `#0d0d0d`) - background outside the slide, presenter mode only.
- `--bg-slide` (default `#1a1a1a`) - default slide background.
- `--bg-light` (default `#ffffff`) - page bg for `light` / `markdown-light` themes and pages with the `light` class.
- `--surface` (default `#2a2a2a`) - inline code background, table headers.

Text:

- `--color-text` (default white @ 85%) - body text on dark slides.
- `--color-heading` (default `#ffffff`) - h1, h2, h4, strong on dark slides.
- `--color-muted` (default `#8f888e`) - heading prefixes, subdued UI; `--topbar-muted` and `--topbar-divider` derive from it for a page component's header.
- `--color-text-light` (default `#2a2230`) - body text on light slides.
- `--color-heading-light` (default `#1a1018`) - headings on light slides.

Accents:

- `--accent` (default `#4a9eff`) - primary accent: bullets, h3, links, blockquote bar.
- `--accent-secondary` (default `#ff6b6b`) - em, link hover.
- `--accent-tertiary` (default `#b388ff`) - hr gradient stop.
- `--accent-aqua` (default `#4ad7c5`) - inline code text.

Fonts:

- `--font-body` (default system sans stack) - body and headings, unless `--font-heading` overrides.
- `--font-heading` (default inherits body) - headings.
- `--font-mono` (default system mono) - inline code, code blocks, h3.
- `--font-terminal` (default inherits body) - body inside `.page-body`.

### Layout helpers

Markdown has no columns, so `deck.css` ships a few opt-in classes for the wrapper HTML you write in `main.md` (decks only). Leave a blank line between the wrapper tags and the markdown inside them, or the markdown is not rendered.

- `.row` - a flex row of `.col` children, vertically centred, filling the remaining slide height. Add `.row-top` to align children to the top.
- `.col` - an equal-width column inside `.row`. Override with inline `style="flex: 0 0 40%"` for an uneven split.
- `.cols-2` / `.cols-3` - a two or three column grid. Columns are `minmax(0, 1fr)`, so a wide code block shrinks instead of pushing the other column off the slide.
- `.shrink` - scales its content from the top centre. Set the factor with `style="--shrink: 0.85"` (default 0.9). Use it when a diagram or table is slightly too tall for the slide.
- `.small-code` - smaller font in code blocks inside it.
- `.center` - centred text.

```markdown
<div class="row">
<div class="col">

- Bullets on the left

</div>
<div class="col small-code">

```py
print("code on the right")
```

</div>
</div>
```

### CSS class hooks

For finer control beyond the variable contract, target these classes from `styles.css`. Most artifacts won't need them - prefer overriding variables first.

Structure:

- `.artifact` - the artifact root, also `.artifact-deck` / `.artifact-document` / `.artifact-page` by type. In a deck it is the `.deck-presenter`, which owns the viewport background and the fit-to-window scaling; its child `.deck` is the page stream.
- `.page` - one page (`<section>`): a 16:9 slide in a deck, a sheet in a document, a block of the column in a page artifact. The page component renders inside it.
- `.page-body` - the rendered markdown, padded by `--slide-padding` in a deck. `.page-body.prose` in the two prose types.
- `.page--active` - the page a deck is currently showing.

Page classes from the `class` directive: `.cover`, `.statement`, `.light`, `.tight`, `.wide`, `.large`, always as `.page.cover` and so on, plus any of your own.

Theme classes (applied to both `<html>` and the artifact root based on `theme` in `artifact.toml`):

- `.theme-light` / `.theme-dark` / `.theme-markdown-light` / `.theme-markdown-dark`

Markdown inside `.page-body` renders as plain HTML (`h1`-`h4`, `p`, `ul`, `ol`, `pre`, `code`, `table`, `blockquote`, `a`, `img`, `hr`) - target those tags directly with `.page <tag>` selectors rather than expecting OpenArtifact to add wrapper classes. Header, footer and navigation markup is whatever your page component contains, with whatever classes you give it.

### Mapping a brand palette

1. Pick the **most distinctive** brand color, assign to `--accent`. Pick a warm counterpoint as `--accent-secondary`.
2. Pick a slightly off-white for `--color-heading` (pure white reads sterile under projector light).
3. Pick a tinted dark for `--bg-slide` (pure black is harsh).
4. For light slides, pick a tinted light bg (cream, eggshell, lavender - not pure white) plus a near-black text color → `--bg-light` / `--color-heading-light` / `--color-text-light`.
5. For custom fonts, self-host woff / woff2 files in `assets/` and declare them with `@font-face` in `styles.css` using relative `url(assets/...)`, then point `--font-body` / `--font-mono` at the family. The server serves font files from the deck directory just like images. Use [Google Webfonts Helper](https://gwfh.mranftl.com/fonts) to download woff2 files.

```css
@font-face {
  font-family: 'YourFont';
  src: url('./assets/YourFont-Regular.woff2') format('woff2');
  font-weight: 400;
  font-display: swap;
}

:root {
  --bg-slide: #...;       /* tinted dark */
  --bg-light: #...;       /* tinted light */
  --surface: #...;

  --color-heading: #...;  /* off-white */
  --color-text: rgba(...);
  --color-heading-light: #...;
  --color-text-light: #...;

  --accent: #...;            /* signature brand color */
  --accent-secondary: #...;  /* warm counterpoint */

  --font-body: 'YourFont', system-ui, sans-serif;
  --font-mono: 'YourFontMono', ui-monospace, monospace;
}
```

## Building & PDF

Build the HTML with `build.build_html`, serve it, then print it with `pdf.print_to_pdf` (both commands are in "Project layout" above).

If Chrome / Chromium can't be found, or it exits with an error, the `BuildError` message carries the exact command: copy it and run it yourself with the right binary path. On Linux `pdf.find_chrome` auto-detects `google-chrome`, `google-chrome-stable`, `chromium`, or `chromium-browser`.

The paper size comes from each type's stylesheet (`@page`): 16:9 for a deck, A4 for the others. If you override `--slide-width` / `--slide-height` in `styles.css`, add a matching `@page { size: ... }` there too.

To spot-check the PDF (requires `pdftoppm` from poppler):

```bash
mkdir -p ./tmp && pdftoppm -r 100 ./dist/deck.pdf ./tmp/page -png
```

One PNG per printed page lands in `./tmp/`, gitignore that path.
