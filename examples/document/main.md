# A document, not a deck

This example uses `type = "document"`. The markdown is rendered as one fixed-width sheet, the way a word processor lays out a page, and it prints to A4 with headings kept with their text.

## What goes in

Plain markdown, structured with headings. A line containing only `---` starts a new page, which prints on a new sheet; otherwise a document flows and the printer decides where the pages fall.

- Paragraphs, lists, tables and code blocks all work as on slides
- Components still work: a `component` tag pulls in a file from `components/`
- Images are referenced relatively and served next to the page

## A table

| Type       | Width        | Printing                    |
| ---------- | ------------ | --------------------------- |
| `deck`     | 16:9 slides  | one slide per page          |
| `document` | fixed sheet  | A4 pages, headings kept     |
| `page`     | fluid column | A4 pages, ordinary margins  |

## Some code

```python
def build(artifact: str) -> str:
    """Validate the files and write dist/index.html."""
    return artifact_url(artifact)
```

> Blockquotes are set in the accent colour, as on slides, and are kept on one page when printing.

---

## Styling

The same CSS variables apply. Override `--document-width` and `--document-padding` in `styles.css` to change the sheet, or add your own `@page { size: letter; }` for US paper.
