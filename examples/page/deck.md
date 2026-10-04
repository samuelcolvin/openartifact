# A continuous page

This example uses `type = "page"`: one fluid column of content that fills the viewport, in the manner of a Notion page or a Claude artifact. Resize the window and the column shrinks with it; the text stays readable up to `--page-max-width`.

## When to use it

Choose `page` for content that is read on screen and scrolled: notes, specs, write-ups, anything that is not going to be presented or printed. Choose `document` when the printed form matters, and `deck` when it will be presented.

## Everything else is the same

1. Markdown, with headings to structure the page
2. Components from `components/`, pulled in with a `component` tag
3. Images referenced relatively, served next to the page
4. Code with syntax highlighting:

```typescript
const TYPE_STYLES: Record<ArtifactType, string[]> = {
  deck: [deckCss],
  document: [proseCss, documentCss],
  page: [proseCss, pageCss],
}
```

Themes apply too: this page uses `theme = "dark"`.
