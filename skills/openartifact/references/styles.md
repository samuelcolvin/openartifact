# Authoring `styles.css`

Part of the OpenArtifact skill; `SKILL.md` covers the format and `artifact.toml`. The base stylesheet handles all layout, typography, page dimensions, transitions and the PDF `@page` setup. `styles.css` only needs to override CSS variables on `:root` to set brand tokens, and can add rules on the class hooks below when the variables are not enough.

## Variable contract

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

Document and page types add `--document-width` / `--document-padding` (sheet size, default A4 with 20mm padding) and `--page-max-width` (column cap, default 52rem). To print a document on US paper, add `@page { size: letter; }`.

## Code colours

Token colours in code blocks derive from the accent variables (`--accent`, `--accent-secondary`, `--accent-tertiary`, `--accent-aqua`, `--color-muted`) and are deepened automatically on light slides, so a brand palette restyles code too. To tune them directly, override `--code-keyword`, `--code-string`, `--code-number`, `--code-title`, `--code-attr`, `--code-comment` or `--code-meta` on `.slide`.

## Layout helpers

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

## CSS class hooks

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

## Mapping a brand palette

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
