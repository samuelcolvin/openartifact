/**
 * Build the DOM for one page of an artifact, whatever its type.
 *
 *   section.page[.classes]
 *     ...the page component, or nothing around...
 *       div.page-body[.prose]   (the rendered markdown; `prose` for a document or page artifact)
 *
 * The page component (`page_component` in artifact.toml) is an ordinary component rendered once per
 * page with no attributes: `{{ CONTENT }}` is the rendered body, and the global context gives it
 * `PAGE_NUMBER`, `PAGE_COUNT`, `PAGE_TITLE` and the `[context]` keys. Without one the body stands alone.
 * The same context is then substituted into the body's own text (outside code), and nested components
 * are expanded.
 */

import { expandComponents, renderComponent } from './components.ts'
import { renderMarkdown } from './render.ts'
import type { RawPage } from './split.ts'
import { type Context, substituteText } from './substitute.ts'
import type { ArtifactConfig, ArtifactData } from './types.ts'

/** With no page component the body is the whole page. */
const DEFAULT_FRAME = '{{ CONTENT }}'

/** Render page `index` of `total`. `PAGE_TITLE` is the title directive, else the body's first `h1`, else empty. */
export function buildPage(
  page: RawPage,
  index: number,
  total: number,
  data: ArtifactData,
  config: ArtifactConfig,
): HTMLElement {
  // Parse through <template> so any <script> in the markdown or a component is inert.
  const template = document.createElement('template')
  template.innerHTML = renderMarkdown(page.body)
  const body = document.createElement('div')
  body.className = config.type === 'deck' ? 'page-body' : 'page-body prose'
  body.append(template.content)

  const title = page.title ?? body.querySelector('h1')?.textContent?.trim() ?? ''
  const context: Context = {
    ...(config.context ?? {}),
    PAGE_NUMBER: String(index + 1),
    PAGE_COUNT: String(total),
    PAGE_TITLE: title,
  }

  const section = document.createElement('section')
  section.className = ['page', ...page.classes].join(' ')
  section.dataset.pageTitle = title
  const frame = (config.page_component && data.components[config.page_component]) || DEFAULT_FRAME
  section.append(renderComponent(frame, {}, context, [body]))
  expandComponents(section, data.components, context)
  substituteText(section, context)
  return section
}
