/**
 * The `document` and `page` artifact types: the whole markdown rendered into one
 * `<article class="prose">`, components expanded, wrapped in `.artifact-<type>` so the type's
 * stylesheet can lay it out. No navigation, no build steps; the browser scrolls and prints it.
 */

import { expandComponents } from './components.ts'
import { renderMarkdown } from './render.ts'
import type { ArtifactConfig, ArtifactData } from './types.ts'

export function buildArticle(data: ArtifactData, config: ArtifactConfig): HTMLElement {
  const root = document.createElement('div')
  root.className = `artifact artifact-${config.type} theme-${config.theme}`

  const article = document.createElement('article')
  article.className = 'prose'
  article.innerHTML = renderMarkdown(data.markdown)
  expandComponents(article, data.components)

  if (config.footer) {
    const footer = document.createElement('footer')
    footer.className = 'prose-footer'
    footer.textContent = config.footer
    article.append(footer)
  }
  root.append(article)

  const title = config.title || article.querySelector('h1')?.textContent?.trim()
  if (title) document.title = title
  return root
}
