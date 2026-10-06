---
name: openartifact
description: Create a deck, document or page with OpenArtifact. Use when the user mentions "openartifact", "deck", "slides" or asks to build a slide deck, a printable document or a web page from markdown, to convert a brand palette into an OpenArtifact stylesheet, or to turn an OpenArtifact artifact into a PDF. Covers project layout, artifact.toml config (type, theme, page_component, [context]), main.md authoring with --- page breaks and <!-- class: ...; title: ... --> directives, HTML components with parameters and {{ CONTENT }}, the PAGE_NUMBER / PAGE_COUNT / PAGE_TITLE built-ins, images and code blocks, with reference files for the styles.css token contract, deck build steps, and building or printing to PDF by hand.
---

# OpenArtifact

OpenArtifact builds one HTML page from one markdown file plus a CSS theme, an optional folder of HTML components and any images they reference. An artifact is made of pages, split on `---` lines; its `type` decides whether they show as slides (`deck`), as printable sheets (`document`) or as one continuous web page (`page`). The page renders itself in the browser and converts to PDF via Chrome headless.

This file is what every artifact needs. Read a reference file when the task calls for it:

- `references/styles.md` - writing `styles.css`: the CSS variable contract, column layouts for deck pages, class hooks, fonts, code colours and how to map a brand palette.
- `references/steps.md` - deck build steps, revealing a page's content step by step.
- `references/local.md` - installing the repo and building or printing to PDF by hand, without the MCP server.

Over MCP they are the resources `skill://openartifact/references/<name>.md`.

## Using the MCP server

The tools: `new_artifact(title, content, type, theme, build=True)` creates an artifact, a directory of source files identified by the UUID it returns, and builds it; `run_code(artifact, code, inputs)` runs Python in a sandbox with the artifact directory as its working directory, for writing and editing files; `upload_url(artifact, files)` returns signed URLs to `PUT` local files to; `build(artifact)` validates the files and refreshes the page; `list_artifacts()` lists yours. Every call that changes files is a commit. Build errors name the file and line.

Files you already have (images, fonts, components, a long `main.md`) go in with `upload_url`, never by retyping them into `run_code`. Pass `[(path, size), ...]`, the path each file will have inside the artifact and its exact size in bytes, and `PUT` each file to the URL returned for it:

```bash
curl -T assets/logo.png "https://.../artifacts/<id>/assets/logo.png?token=..."
```

The response is JSON with the file's `sha256`; compare it with `shasum -a 256 assets/logo.png`. Each upload is one commit. URLs last an hour and take up to 10 MB per file; the size must match exactly. If `main.md` already refers to files you have still to add, create the artifact with `build=False`, upload them, then call `build`.

Beside the page at `/artifacts/<id>/`, the server offers `/artifacts/<id>.md` (the markdown source behind a frontmatter summary: title, type, theme, URL, dates and the list of source files; read this rather than the page), `/artifacts/<id>.zip` (every source file) and `/artifacts/<id>.pdf` (the page printed to PDF). Fetching the page URL itself with `Accept: text/markdown` (or `text/plain`) returns the same markdown export.

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

The built page is served at `/artifacts/<id>/`. It is not self-contained: it loads `openartifact.js` from the server and its images relatively, so view and print it through the server rather than from `file://`.

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

### Columns and build steps

Markdown has no columns. For deck pages, the base stylesheet ships opt-in wrapper classes for HTML you write in `main.md` (`.row` / `.col`, `.cols-2` / `.cols-3`, `.shrink`, `.small-code`, `.center`); `references/styles.md` shows how to use them. Revealing a page's content step by step, with `data-step` attributes, is `references/steps.md`.

### Images

Reference images by path relative to the deck directory, from markdown, from a component, or from `url(...)` in `styles.css`:

```markdown
![Architecture](assets/architecture.png)

<img src="assets/logo.svg" alt="Logo" style="width: 96px">
```

Image paths are relative to the deck directory and stay that way in the page: the server serves the page at `/artifacts/<id>/` and the deck's `.png` / `.jpg` / `.gif` / `.svg` / `.webp` files under it, so the browser fetches them from there. The build checks each referenced file exists and fails if one is missing or points outside the deck. Absolute URLs (`https://...`) are left alone. Image files themselves go into the artifact with `upload_url` (see "Using the MCP server").

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

Token colours derive from the artifact's accent variables, so a brand palette in `styles.css` restyles code too; `references/styles.md` lists the `--code-*` variables for tuning them directly.

## `styles.css`

The base stylesheet handles all layout, typography, page dimensions, transitions and the print `@page` setup. `styles.css` only overrides CSS variables on `:root` to set brand colours and fonts, and adds rules on the class hooks when the variables are not enough. The variable contract, the layout helpers, the class hooks and the steps for mapping a brand palette are in `references/styles.md`; read it before writing any CSS.
