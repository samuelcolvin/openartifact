/**
 * Build the DOM for one page of an artifact, whatever its type.
 *
 *   section.slide[.classes]            (`section.page` for a document or page artifact)
 *     ...the page component, or nothing around...
 *       div.slide-body | article.prose   (the rendered markdown)
 *
 * The page component (`page_component` in artifact.toml) is an ordinary component rendered once per
 * page with no attributes: `{{ CONTENT }}` is the rendered body, and the global context gives it
 * `PAGE_NUMBER`, `PAGE_COUNT`, `PAGE_TITLE` and the `[context]` keys. Without one the body stands alone.
 * The same context is then substituted into the body's own text (outside code), and nested components
 * are expanded.
 */

import { expandComponents, renderComponent } from './components.ts'
import { renderMarkdown } from './render.ts'
import { type Context, substituteText } from './substitute.ts'
import type { ArtifactConfig, ArtifactData } from './types.ts'

/** What a page is made of before rendering: its classes, an optional title and its markdown. */
export interface PageInput {
  classes: string[]
  /** Explicit title; otherwise the first `h1` of the rendered body is used, then nothing. */
  title?: string
  body: string
}

/** With no page component the body is the whole page. */
const DEFAULT_FRAME = '{{ CONTENT }}'

/** Render page `index` of `total`. */
export function buildPage(
  page: PageInput,
  index: number,
  total: number,
  data: ArtifactData,
  config: ArtifactConfig,
): HTMLElement {
  // Parse through <template> so any <script> in the markdown or a component is inert.
  const template = document.createElement('template')
  template.innerHTML = renderMarkdown(page.body)
  const isDeck = config.type === 'deck'
  const body = document.createElement(isDeck ? 'div' : 'article')
  body.className = isDeck ? 'slide-body' : 'prose'
  body.append(template.content)

  const title = page.title ?? body.querySelector('h1')?.textContent?.trim() ?? ''
  const context: Context = {
    ...(config.context ?? {}),
    PAGE_NUMBER: String(index + 1),
    PAGE_COUNT: String(total),
    PAGE_TITLE: title,
  }

  const section = document.createElement('section')
  section.className = [isDeck ? 'slide' : 'page', ...page.classes].join(' ')
  section.dataset.pageTitle = title
  const frame = (config.page_component && data.components[config.page_component]) || DEFAULT_FRAME
  section.append(renderComponent(frame, {}, context, [body]))
  expandComponents(section, data.components, context)
  substituteText(section, context)
  return section
}
