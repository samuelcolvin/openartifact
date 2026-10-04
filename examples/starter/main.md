<slide layout="title"/>

### OpenArtifact Starter

# A minimal OpenArtifact deck

## Built from markdown, HTML and CSS

<slide tab="intro"/>

# Hello, slide

- Slides are markdown, separated by `<slide .../>` lines
- Bigger blocks of HTML live in `components/*.html`
- Theme is plain CSS variables in `styles.css`

<div data-step="1">

Press next: this paragraph is a build step (`data-step="1"`).

</div>

<slide tab="intro" title="Components"/>

# A component

Anything longer than a few lines of HTML goes in a file and is pulled in with a
`component` tag. The hero below nests a second component and an inlined image.

<component src="Hero.html"></component>

<slide tab="intro"/>

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

<slide tab="intro"/>

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

<slide tab="details" space="tight"/>

# Details

| Feature       | Where it lives        |
| ------------- | --------------------- |
| Slide content | `main.md`             |
| Theme tokens  | `styles.css`          |
| Components    | `components/*.html`   |
| Config        | `artifact.toml`          |

<slide theme="light"/>

# A light slide

Lorem ipsum dolor sit amet.

- One
- Two
- Three

<slide tab="details"/>

# Code blocks

Highlighted in the browser by highlight.js:

```ts
export function greet(name: string): string {
  return `hello, ${name}`;
}

console.log(greet("openartifact"));
```

<slide theme="light"/>

# Code on a light slide

```py
def greet(name: str) -> str:
    return f"hello, {name}"

print(greet("openartifact"))
```

<slide layout="statement"/>

# One bold statement.
