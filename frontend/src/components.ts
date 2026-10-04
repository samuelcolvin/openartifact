/**
 * Component expansion: `<component src="Card.html" title="Fast">children</component>` is replaced by the
 * contents of that file (from the `components` map build.py embedded) with its placeholders filled in.
 *
 * - Each attribute other than `src` is a parameter: `{{ title }}` becomes the attribute's value, escaped, so it
 *   is safe as text and inside attribute values alike. A parameter the tag does not pass takes the default
 *   declared on the component's first line, `<!-- params: title, icon="x" -->`.
 * - `{{ CONTENT }}` is replaced by the tag's children, moved as live DOM so markdown already rendered inside the
 *   tag (and its `data-step` attributes) survives.
 * - Uppercase names come from the page's context: `PAGE_NUMBER`, `PAGE_COUNT`, `PAGE_TITLE`, `[context]` keys.
 *
 * Substitution happens on the file's text before it is parsed, so placeholders work in attributes too
 * (`href="#{{ PAGE_NUMBER }}"`). Nesting needs no recursion: components inside the moved children keep their
 * concrete attributes, and a component tag inside a component body has had its `{{ ... }}` filled by the time it
 * is parsed, so the next pass of the loop picks it up. build.py has already rejected unknown names, missing
 * required parameters and cycles; the warnings here are the backstop.
 *
 * Images need no processing: the page is served from `/artifacts/<id>/`, so relative `src` and CSS `url()`
 * references resolve to the server, which serves the artifact's files.
 */

import { type Context, escapeHtml, PLACEHOLDER_RE, parseParams } from './substitute.ts'

/** Expansions per root before giving up; a cycle the build missed would otherwise loop forever. */
const MAX_EXPANSIONS = 10_000
/** Stands in for `{{ CONTENT }}` in the parsed HTML. A comment, because the HTML tree builder never moves comments
 * (an element placeholder inside `<tbody>` would be hoisted out of the table). */
const CONTENT_SENTINEL = 'oa-content'

/**
 * CommonMark treats `<component ...></component>` on a line of its own as inline HTML, so markdown-it wraps it
 * in a paragraph. Unwrap that paragraph when the tag is all the paragraph holds, otherwise block-level
 * component content ends up nested inside a `<p>`.
 */
function unwrapParagraph(tag: Element): void {
  const parent = tag.parentElement
  if (parent?.tagName !== 'P') return
  for (const node of parent.childNodes) {
    if (node === tag) continue
    if (node.nodeType !== Node.TEXT_NODE || (node.textContent ?? '').trim() !== '') return
  }
  parent.replaceWith(tag)
}

/**
 * Render one component file with the given parameters, context and children, returning the fragment to
 * insert. Also used for the page component, with no parameters and the rendered page as children.
 */
export function renderComponent(
  source: string,
  attrs: Record<string, string>,
  context: Context,
  children: Node[],
): DocumentFragment {
  const { params, body } = parseParams(source)
  const html = body.replace(PLACEHOLDER_RE, (whole, name: string) => {
    if (name === 'CONTENT') return `<!--${CONTENT_SENTINEL}-->`
    if (name in context) return escapeHtml(context[name])
    if (name in attrs) return escapeHtml(attrs[name])
    const fallback = params[name]
    if (fallback !== undefined && fallback !== null) return escapeHtml(fallback)
    console.warn(`openartifact: no value for ${whole}`)
    return whole
  })
  const template = document.createElement('template')
  template.innerHTML = html
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_COMMENT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if ((node as Comment).data === CONTENT_SENTINEL) {
      ;(node as Comment).replaceWith(...children)
      break
    }
  }
  return template.content
}

/** Expand every `<component src>` under `root`, outermost first, until none is left. */
export function expandComponents(root: ParentNode, components: Record<string, string>, context: Context): void {
  for (let n = 0; n < MAX_EXPANSIONS; n++) {
    const tag = root.querySelector('component[src]')
    if (!tag) return
    unwrapParagraph(tag)
    const src = tag.getAttribute('src') ?? ''
    const source = components[src]
    if (source === undefined) {
      console.warn(`openartifact: missing component ${src}`)
      tag.replaceWith(`[missing component: ${src}]`)
      continue
    }
    const attrs: Record<string, string> = {}
    for (const name of tag.getAttributeNames()) {
      if (name !== 'src') attrs[name] = tag.getAttribute(name) ?? ''
    }
    tag.replaceWith(renderComponent(source, attrs, context, Array.from(tag.childNodes)))
  }
  console.warn(`openartifact: more than ${MAX_EXPANSIONS} component expansions, giving up`)
}
