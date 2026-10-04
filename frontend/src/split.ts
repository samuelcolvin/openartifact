/**
 * Split raw markdown into pages.
 *
 * A line consisting only of `---` ends a page; the next page starts on the following line. The
 * split happens on the raw source, before markdown-it sees anything, so `---` is never a
 * thematic break or a setext underline inside a page (use `***` for a rule). Breaks inside
 * fenced code blocks are ignored. The first page starts at the top of the file.
 *
 * A page may open with directive comments, `<!-- class: cover light; title: Welcome -->`: one
 * or more `key: value` entries separated by `;` or newlines, in one comment or several. `class`
 * adds classes to the page element, `title` sets its `PAGE_TITLE`. The directives are stripped
 * from the body.
 *
 * `build.py` runs the same scan to fail the build early with a line number; this module is the
 * runtime source of truth for what a page is.
 */

export interface RawPage {
  /** Classes from the `class` directive, e.g. `['cover', 'light']`. */
  classes: string[]
  /** The `title` directive, if given. */
  title?: string
  /** Markdown body of the page, directives removed. */
  body: string
  /** 1-based line number of the page's first line in the source, for messages. */
  line: number
}

/** A page break: `---` alone on its line, up to three spaces of indent. */
const PAGE_BREAK_RE = /^ {0,3}---[ \t]*$/
/** Opening or closing code fence: up to three spaces of indent then ``` or ~~~ (three or more). */
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/
/** A comment whose text opens with `key:` is a directive comment; any other comment is left to markdown. */
const COMMENT_START_RE = /^\s*<!--/
const DIRECTIVE_INNER_RE = /^\s*[a-z]+\s*:/
/** Entries are separated by newlines, or by `;` when a known key follows (so a title may contain `;`). */
const ENTRY_SPLIT_RE = /\r?\n|;\s*(?=(?:class|title)\s*:)/

/** Parse the entries of one directive comment's inner text into the page. */
function applyDirectives(inner: string, page: RawPage): void {
  for (const entry of inner.split(ENTRY_SPLIT_RE)) {
    const m = /^\s*([a-z]+)\s*:\s*(.*?)\s*$/.exec(entry)
    if (!m) continue
    if (m[1] === 'class') page.classes.push(...m[2].split(/\s+/).filter(Boolean))
    else if (m[1] === 'title') page.title = m[2]
    // Unknown keys are ignored here; build.py reports them.
  }
}

/** Turn the lines of one page into a `RawPage`, consuming leading directive comments. */
function parsePage(lines: string[], line: number): RawPage {
  const page: RawPage = { classes: [], body: '', line }
  let i = 0
  while (i < lines.length) {
    if (lines[i].trim() === '') {
      i++
      continue
    }
    if (!COMMENT_START_RE.test(lines[i])) break
    // The comment may span several lines; find the one that closes it.
    let end = i
    while (end < lines.length && !lines[end].includes('-->')) end++
    if (end === lines.length) break
    const text = lines.slice(i, end + 1).join('\n')
    const inner = text.slice(text.indexOf('<!--') + 4, text.lastIndexOf('-->'))
    if (!DIRECTIVE_INNER_RE.test(inner)) break // an ordinary comment: it stays in the body
    applyDirectives(inner, page)
    i = end + 1
  }
  page.body = lines.slice(i).join('\n')
  return page
}

export function splitPages(markdown: string): RawPage[] {
  const pages: RawPage[] = []
  let lines: string[] = []
  let start = 1
  // The marker of the open fence (e.g. "```"), or null when not inside a fence.
  let fence: string | null = null

  markdown.split(/\r?\n/).forEach((text, i) => {
    const fenceMatch = FENCE_RE.exec(text)
    if (fence === null) {
      if (fenceMatch) {
        fence = fenceMatch[1]
      } else if (PAGE_BREAK_RE.test(text)) {
        pages.push(parsePage(lines, start))
        lines = []
        start = i + 2
        return
      }
    } else if (fenceMatch && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) {
      // A closing fence uses the same character, is at least as long, and has nothing after it.
      if (text.slice(fenceMatch[0].length).trim() === '') fence = null
    }
    lines.push(text)
  })
  pages.push(parsePage(lines, start))
  return pages
}
