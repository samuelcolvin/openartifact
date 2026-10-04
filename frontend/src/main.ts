/**
 * Browser entry point. Bundled by esbuild into `dist/openartifact.js`, which server.py serves at
 * `/openartifact.js` and every generated `index.html` loads with a classic `<script src>`.
 *
 * Everything below runs synchronously while the script executes at the end of <body>:
 * no DOMContentLoaded handler, no requestAnimationFrame, no dynamic imports. That keeps the
 * DOM complete before `load`, which is when headless Chrome prints to PDF.
 */

import { buildArticle } from './article.ts'
import { expandComponents } from './components.ts'
import { initDeck } from './deck.ts'
import hljsCss from './hljs.css'
import { renderMarkdown } from './render.ts'
import { buildSlide } from './slide.ts'
import { splitSlides } from './split.ts'
import deckCss from './styles/deck.css'
import documentCss from './styles/document.css'
import pageCss from './styles/page.css'
import proseCss from './styles/prose.css'
import sharedCss from './styles/shared.css'
import type { ArtifactConfig, ArtifactData, ArtifactType } from './types.ts'

/** Stylesheets per type, injected after shared.css and before hljs.css and the user's styles.css. */
const TYPE_STYLES: Record<ArtifactType, string[]> = {
  deck: [deckCss],
  document: [proseCss, documentCss],
  page: [proseCss, pageCss],
}

function readData(): ArtifactData {
  const node = document.getElementById('artifact-data')
  if (!node) throw new Error('openartifact: <script type="application/json" id="artifact-data"> not found')
  return JSON.parse(node.textContent ?? '') as ArtifactData
}

function addStyle(css: string): void {
  if (!css) return
  const style = document.createElement('style')
  style.textContent = css
  document.head.append(style)
}

/** Replace the page with a readable error. build.py catches these first; this is the backstop. */
function showError(root: HTMLElement, message: string): void {
  const pre = document.createElement('pre')
  pre.style.cssText = 'margin:2rem;padding:1rem;font:14px/1.5 monospace;color:#ff6b6b;white-space:pre-wrap'
  pre.textContent = `openartifact: ${message}`
  root.replaceChildren(pre)
}

function main(): void {
  const data = readData()
  const config: ArtifactConfig = {
    ...data.config,
    type: data.config.type ?? 'deck',
    theme: data.config.theme ?? 'light',
    tabs: data.config.tabs ?? [],
  }
  const root = document.getElementById('root') ?? document.body

  // Shared tokens first, then the type's own sheets, then code colours, then the user's styles.css so it wins.
  addStyle(sharedCss)
  for (const css of TYPE_STYLES[config.type] ?? TYPE_STYLES.deck) addStyle(css)
  addStyle(hljsCss)
  addStyle(data.styles)
  // The theme class goes on <html> too so `@media print` body rules can scope by theme.
  document.documentElement.classList.add(`theme-${config.theme}`)

  if (config.type !== 'deck') {
    root.replaceChildren(buildArticle(data, config))
    return
  }

  const { preamble, slides } = splitSlides(data.markdown)
  if (slides.length === 0) {
    showError(root, 'no slides found: start each slide with a <slide .../> line')
    return
  }
  if (preamble.trim() !== '') {
    showError(root, `content before the first <slide .../> line:\n\n${preamble.trim()}`)
    return
  }

  const presenter = document.createElement('div')
  presenter.className = `artifact artifact-deck deck-presenter theme-${config.theme}`
  const deck = document.createElement('div')
  deck.className = 'deck'
  for (const raw of slides) {
    const section = buildSlide(raw, renderMarkdown(raw.body), config)
    expandComponents(section, data.components)
    deck.append(section)
  }
  presenter.append(deck)
  root.replaceChildren(presenter)

  initDeck(presenter, config)
}

main()
