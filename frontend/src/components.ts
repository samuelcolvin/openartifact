/**
 * Post-processing of a rendered slide body: `<component src="Name.html"></component>` is
 * replaced by the contents of that file (from the `components` map build.py embedded).
 * Components may contain components, so the substitution loops; a depth cap guards against
 * cycles the build missed.
 *
 * Images need no processing: the page is served from `/artifacts/<id>/`, so relative
 * `src` and CSS `url()` references resolve to the server, which serves the artifact's files.
 */

const MAX_COMPONENT_DEPTH = 32

/**
 * CommonMark treats `<component ...></component>` on a line of its own as inline HTML,
 * so markdown-it wraps it in a paragraph. Unwrap that paragraph, otherwise block-level
 * component content ends up nested inside a `<p>`.
 */
function unwrapParagraph(tag: Element): void {
  const parent = tag.parentElement
  if (parent?.tagName !== 'P') return
  if (parent.children.length !== 1 || (parent.textContent ?? '').trim() !== '') return
  parent.replaceWith(tag)
}

export function expandComponents(root: ParentNode, components: Record<string, string>): void {
  for (let depth = 0; depth < MAX_COMPONENT_DEPTH; depth++) {
    const tags = root.querySelectorAll('component[src]')
    if (tags.length === 0) return
    for (const tag of tags) {
      unwrapParagraph(tag)
      const src = tag.getAttribute('src') ?? ''
      const html = components[src]
      if (html === undefined) {
        console.warn(`openartifact: missing component ${src}`)
        tag.replaceWith(`[missing component: ${src}]`)
        continue
      }
      const template = document.createElement('template')
      template.innerHTML = html
      tag.replaceWith(template.content)
    }
  }
  console.warn(`openartifact: component nesting deeper than ${MAX_COMPONENT_DEPTH}, giving up`)
}
