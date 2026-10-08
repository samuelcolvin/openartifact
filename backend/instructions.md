OpenArtifact turns markdown into a page that renders itself in the browser and prints to PDF. Each artifact is
a directory of source files (main.md, artifact.toml, styles.css, components/*.html, assets/*) that you create and
edit with the tools below. Every change is committed to the artifact's history.

Tools

- new_personal_artifact(title, content, public, type, theme, build=True) and
  new_org_artifact(title, content, org_editable, public, type, theme, build=True): write `content` to main.md,
  build the artifact and return its identifier (a UUID) and page URL. A personal artifact is yours alone unless
  public; an organisation artifact is seen by everyone in your Google Workspace organisation, edited by them
  too when org_editable, and open to anyone with the link when public. Ask the user which they want and who
  should see it; the permissions are deliberately not defaulted. A build error is returned with the file and
  line; the files are kept, so fix them with run_code and call build. Pass build=False when `content` uses
  components or images you still have to add, and call build once they are in place.
- run_code(artifact, code, inputs): runs Python in a sandbox whose working directory is the artifact directory,
  also mounted at {VIRTUAL_PATH}. Use pathlib or open() to read and write files there. Pass large text through
  `inputs`, which are bound as global variables, rather than escaping it inside `code`.
- build(artifact): validates the files and refreshes the page; errors name the file and line.
- screenshot(artifact, page=1): one page of the built artifact as an image, as a viewer sees it. Look at a page
  after building it when layout matters.
- upload_url(artifact, files): for files you already have locally (images, fonts, components, a long main.md),
  pass `[(path, size), ...]` with each file's path inside the artifact and exact size in bytes, and get one URL
  per file back. Then `curl -T local/file "<url>"` (quote the URL). Each upload is committed and answered with
  its sha256, to compare with `shasum -a 256`. Far cheaper than retyping file contents into run_code.
- set_access(artifact, public, org_editable=False): change who may see and edit an artifact you own.
- fork(artifact, org_editable=False, public=False): copy an artifact you can see into one of your own, history
  included.
- list_artifacts(): your artifacts, and the ones your organisation shares with you.

Artifacts shared with you read-only can be read and built but not edited: fork them first.

Beside each page at /artifacts/<id>/ the server serves /artifacts/<id>.md (the markdown source behind a
frontmatter summary; read this rather than the page), /artifacts/<id>.zip (every source file plus a .git with the artifact's history) and
/artifacts/<id>.pdf (the page printed to PDF) and /artifacts/<id>.png?page=N (one page as an image). The page URL itself answers with that markdown when fetched
with `Accept: text/markdown` or `text/plain`.

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

The authoring guide (page classes, the page component, components and parameters, images, code blocks) is the
resource {SKILL_URI}. Read it before writing anything beyond plain markdown. It points to reference files under
skill://openartifact/references/ for styles.css (the CSS variable contract, column layouts, class hooks, brand
palettes), deck build steps, and building or printing by hand; read those when the task needs them.
