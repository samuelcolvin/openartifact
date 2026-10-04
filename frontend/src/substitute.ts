/**
 * `{{ name }}` substitution, shared by components and pages. One regex, no template language:
 * lowercase names are a component's parameters (from the tag's attributes or the declared defaults),
 * uppercase names are the global context (`PAGE_NUMBER`, `PAGE_COUNT`, `PAGE_TITLE`, the `[context]`
 * table of artifact.toml) and `CONTENT`, the markup a component wraps. build.py validates every name
 * before the page is built; here unknown names are left as they are so a mistake stays visible.
 */

/** Mirrors PLACEHOLDER_RE in build.py. */
export const PLACEHOLDER_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g

/** The optional first line of a component file: `<!-- params: title, icon="x" -->`. Mirrors build.py. */
const PARAMS_RE = /^\s*<!--\s*params:\s*([\s\S]*?)\s*-->[ \t]*\r?\n?/
/** One declaration inside it: a name, optionally with a quoted default. */
const PARAM_DECL_RE = /([^\s,=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g

/** Values the global context holds; everything is a string by the time it is substituted. */
export type Context = Record<string, string>

/** A parsed component file: declared parameters (`null` = required) and the body without the declaration. */
export interface ParsedComponent {
  params: Record<string, string | null>
  body: string
}

/** Escape a value so it is safe as text and inside a single- or double-quoted attribute. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Split a component file into its `<!-- params: ... -->` declaration and body. */
export function parseParams(source: string): ParsedComponent {
  const params: Record<string, string | null> = {}
  const match = PARAMS_RE.exec(source)
  if (!match) return { params, body: source }
  for (const decl of match[1].matchAll(PARAM_DECL_RE)) {
    params[decl[1]] = decl[2] ?? decl[3] ?? null
  }
  return { params, body: source.slice(match[0].length) }
}

/**
 * Replace the uppercase placeholders of `context` in the text nodes under `root`, leaving code alone.
 * This is how `{{ PAGE_NUMBER }}` written in the markdown itself is filled; component files are
 * substituted as strings before they are parsed (see components.ts), so their output is already done.
 */
export function substituteText(root: Node, context: Context): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const pending: Text[] = []
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text
    if (text.data.includes('{{') && !text.parentElement?.closest('pre, code')) pending.push(text)
  }
  // Mutate after the walk so the walker never sees a node change under it.
  for (const text of pending) {
    text.data = text.data.replace(PLACEHOLDER_RE, (whole, name: string) => context[name] ?? whole)
  }
}
