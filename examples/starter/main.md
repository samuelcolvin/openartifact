<!-- class: cover; title: OpenArtifact Starter -->

### OpenArtifact Starter

# A minimal OpenArtifact deck

## Built from markdown, HTML and CSS

---

# Hello, page

- Pages are markdown, separated by a line containing only `---`
- A comment at the top of a page sets its classes and title: `<!-- class: light; title: ... -->`
- Bigger blocks of HTML live in `components/*.html`; the header above is `Page.html`
- Theme is plain CSS variables in `styles.css`

<div data-step="1">

Press next: this paragraph is a build step (`data-step="1"`).

</div>

---

<!-- title: Components -->

# A component

Anything longer than a few lines of HTML goes in a file and is pulled in with a
`component` tag. The hero below nests a second component and an inlined image.

<component src="Hero.html"></component>

---

# Components with parameters

Attributes on the tag become `{{ name }}` placeholders in the file; the markdown between the tags becomes `{{ CONTENT }}`.
Updated {{ DATE }}, from `[context]` in `artifact.toml`.

<div class="cols-3">
<component src="Card.html" title="Parameters" icon="1">

`Card.html` declares `<!-- params: title, icon="*" -->`; `title` is required, `icon` has a default.

</component>
<component src="Card.html" title="Children" icon="2">

Leave a blank line after the opening tag and before the closing one, so this text is **markdown**.

</component>
<component src="Card.html" title="Defaults"></component>
</div>

---

# Columns and an SVG component

<div class="row">
<div class="col">

- `.row` and `.col` are layout helpers from `base.css`
- The diagram is `components/Steps.svg`, inlined as markup
- Its colours are CSS variables, so it follows the theme

</div>
<div class="col">
<component src="Steps.svg"></component>
</div>
</div>

---

<!-- class: tight -->

# Details

| Feature       | Where it lives        |
| ------------- | --------------------- |
| Page content  | `main.md`             |
| Theme tokens  | `styles.css`          |
| Components    | `components/*.html`   |
| Config        | `artifact.toml`          |

This is page {{ PAGE_NUMBER }} of {{ PAGE_COUNT }}.

---

<!-- class: light -->

# A light page

Lorem ipsum dolor sit amet.

- One
- Two
- Three

---

# Code blocks

Highlighted in the browser by highlight.js:

```ts
export function greet(name: string): string {
  return `hello, ${name}`;
}

console.log(greet("openartifact"));
```

---

<!-- class: light -->

# Code on a light page

```py
def greet(name: str) -> str:
    return f"hello, {name}"

print(greet("openartifact"))
```

---

<!-- class: statement -->

# One bold statement.
