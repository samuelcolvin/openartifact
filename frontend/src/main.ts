/**
 * Browser entry point. Bundled by esbuild into `dist/openartifact.js`, which server.py serves at
 * `/openartifact.js` and every generated `index.html` loads with a classic `<script src>`.
 *
 * Everything below runs synchronously while the script executes at the end of <body>:
 * no DOMContentLoaded handler, no requestAnimationFrame, no dynamic imports. That keeps the
 * DOM complete before `load`, which is when headless Chrome prints to PDF.
 */

import { initDeck } from './deck.ts'
import hljsCss from './hljs.css'
import { buildPage, type PageInput } from './page.ts'
import { type RawSlide, splitSlides } from './split.ts'
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

/** Maps the `theme` attribute to its CSS class. Only `light` is an override; `dark` is the deck default. */
const THEME_CLASS: Record<string, string> = { light: 'light-slide' }
/** Maps the `layout` attribute to its CSS class. `content` is the default and adds nothing. */
const LAYOUT_CLASS: Record<string, string> = { title: 'title-slide', statement: 'statement-slide' }

/**
 * Reverse `encode_block` in build.py: the only sequences that can end a data block early are `</script`, `<script`
 * and `<!--`, so the builder writes their `<` as `&lt;` and protects pre-existing `&amp;` / `&lt;` by doubling the
 * ampersand. One left-to-right pass restores the exact source.
 */
function decodeBlock(node: Element | null, what: string): string {
  if (!node) throw new Error(`openartifact: ${what} block not found`)
  const text = node.textContent ?? ''
  // The builder puts the content on the line after the opening tag; drop exactly that newline.
  const raw = text.startsWith('\n') ? text.slice(1) : text
  return raw.replace(/&(amp|lt);/g, (_, name: string) => (name === 'lt' ? '<' : '&'))
}

/** Assemble the artifact from the data blocks `render_page` in build.py wrote into the page. */
function readData(): ArtifactData {
  const configNode = document.getElementById('artifact-config')
  if (!configNode) throw new Error('openartifact: <script type="application/json" id="artifact-config"> not found')
  const config = JSON.parse(configNode.textContent ?? '{}') as ArtifactConfig
  const markdown = decodeBlock(document.getElementById('artifact-markdown'), 'markdown')
  const components: Record<string, string> = {}
  for (const node of document.querySelectorAll<HTMLScriptElement>('script[type="text/html"][data-component]')) {
    components[node.dataset.component ?? ''] = decodeBlock(node, 'component')
  }
  return { config, markdown, components }
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

/** The `<slide .../>` attributes as page classes and title. */
function slideToPage(raw: RawSlide): PageInput {
  const { layout, theme, title, space, fontSize, id } = raw.attrs
  const classes = [
    theme && THEME_CLASS[theme],
    layout && LAYOUT_CLASS[layout],
    space && `space-${space}`,
    fontSize && `font-${fontSize}`,
    id && `id-${id}`,
  ].filter((c): c is string => Boolean(c))
  const page: PageInput = { classes, body: raw.body }
  if (title) page.title = title
  return page
}

function main(): void {
  const data = readData()
  const config: ArtifactConfig = {
    ...data.config,
    type: data.config.type ?? 'deck',
    theme: data.config.theme ?? 'light',
  }
  const root = document.getElementById('root') ?? document.body

  // Shared tokens first, then the type's own sheets, then code colours, then the user's styles.css so it wins. The
  // user's sheet is already in <head> as a live <style>; appending it again moves it after the ones just added.
  addStyle(sharedCss)
  for (const css of TYPE_STYLES[config.type] ?? TYPE_STYLES.deck) addStyle(css)
  addStyle(hljsCss)
  const userStyles = document.getElementById('artifact-styles')
  if (userStyles) document.head.append(userStyles)
  // The theme class goes on <html> too so `@media print` body rules can scope by theme.
  document.documentElement.classList.add(`theme-${config.theme}`)

  if (config.type !== 'deck') {
    const wrapper = document.createElement('div')
    wrapper.className = `artifact artifact-${config.type} theme-${config.theme}`
    const page = buildPage({ classes: [], body: data.markdown }, 0, 1, data, config)
    wrapper.append(page)
    root.replaceChildren(wrapper)
    const title = config.title || page.dataset.pageTitle
    if (title) document.title = title
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
  slides.forEach((raw, i) => {
    deck.append(buildPage(slideToPage(raw), i, slides.length, data, config))
  })
  presenter.append(deck)
  root.replaceChildren(presenter)

  initDeck(presenter, config)
}

main()
