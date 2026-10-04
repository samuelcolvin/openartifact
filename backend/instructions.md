OpenArtifact turns markdown into a page that renders itself in the browser and prints to PDF. Each artifact is
a directory of source files (main.md, artifact.toml, styles.css, components/*.html, assets/*) that you create and
edit with the tools below. Every change is committed to the artifact's history.

Tools

- new_artifact(title, content, type, theme): writes `content` to main.md, builds the artifact and returns its
  identifier (a UUID) and page URL. A build error is returned with the file and line; the files are kept, so
  fix them with run_code and call build.
- run_code(artifact, code, inputs): runs Python in a sandbox whose working directory is the artifact directory,
  also mounted at {VIRTUAL_PATH}. Use pathlib or open() to read and write files there. Pass large text through
  `inputs`, which are bound as global variables, rather than escaping it inside `code`.
- build(artifact): validates the files and refreshes the page; errors name the file and line.
- list_artifacts(): the artifacts you already have.

The format

- An artifact is made of pages: main.md split on lines containing only `---`, with a blank line before each.
  Inside a page, `***` draws a rule.
- `type` in artifact.toml picks how the pages are laid out: `deck` shows one 16:9 page at a time with
  navigation, `document` stacks fixed-width sheets that print one per page, `page` is a continuous web page.
- A comment at the top of a page sets its classes and title: `<!-- class: cover light; title: Welcome -->`.
  Built-in classes: cover, statement, light, tight, wide, large.
- Components live in components/ and are used as `<component src="Card.html" title="x">children</component>`.
  A component declares its parameters on its first line, `<!-- params: title, icon="*" -->`; attributes fill
  `{{ title }}`, the markdown between the tags fills `{{ CONTENT }}`. Leave a blank line after the opening tag
  and before the closing one.
- `{{ PAGE_NUMBER }}`, `{{ PAGE_COUNT }}`, `{{ PAGE_TITLE }}` and the uppercase keys of the `[context]` table in
  artifact.toml are substituted everywhere outside code.
- `page_component = "Page.html"` in artifact.toml names a component rendered around every page, with
  `{{ CONTENT }}` where the body goes. Headers, footers and counters live there.

The full authoring guide (page classes, components and parameters, build steps, images, the CSS variable
contract, mapping a brand palette) is the resource {SKILL_URI}. Read it before writing anything beyond plain
markdown.
