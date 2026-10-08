/**
 * Browser entry point. Bundled by esbuild into `dist/openartifact.js`, which server.py serves at
 * `/openartifact.js` and every generated `index.html` loads with a classic `<script src>`.
 *
 * Everything below runs synchronously while the script executes at the end of <body>:
 * no DOMContentLoaded handler, no requestAnimationFrame, no dynamic imports. That keeps the
 * DOM complete before `load`, which is when headless Chrome prints to PDF.
 *
 * The artifact goes into `#root`; the viewer toolbar (toolbar.ts) is a separate shadow host appended
 * to <body> after it, so it is never part of the page `build.py` writes and never printed.
 */

import { type DeckController, initDeck } from './deck.ts'
import type {} from './embed.ts'
import hljsCss from './hljs.css'
import { writeOutline } from './outline.ts'
import { buildPage } from './page.ts'
import { hideText, measureScene, prepareScene, sceneRequested } from './scene.ts'
import { splitPages } from './split.ts'
import deckCss from './styles/deck.css'
import documentCss from './styles/document.css'
import pageCss from './styles/page.css'
import proseCss from './styles/prose.css'
import sharedCss from './styles/shared.css'
import { initToolbar } from './toolbar.ts'
import type { ArtifactConfig, ArtifactData, ArtifactType } from './types.ts'

/** Stylesheets per type, injected after shared.css and before hljs.css and the user's styles.css. */
const TYPE_STYLES: Record<ArtifactType, string[]> = {
  deck: [deckCss],
  document: [proseCss, documentCss],
  page: [proseCss, pageCss],
}

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

  const raw = splitPages(data.markdown)
  if (raw.every((page) => page.body.trim() === '')) {
    showError(root, 'no content: main.md is empty')
    return
  }
  const pages = raw.map((page, i) => buildPage(page, i, raw.length, data, config))

  let deck: DeckController | null = null
  if (config.type === 'deck') {
    // A deck: one page at a time, inside the presenter that scales it to the viewport.
    const presenter = document.createElement('div')
    presenter.className = `artifact artifact-deck deck-presenter theme-${config.theme}`
    const stream = document.createElement('div')
    stream.className = 'deck'
    stream.append(...pages)
    presenter.append(stream)
    root.replaceChildren(presenter)
    deck = initDeck(presenter, config)
    if (sceneRequested()) {
      // The measurement pass of the PowerPoint export (scene.ts): a render for the chrome service, not a viewer.
      prepareScene()
      measureScene(pages)
      hideText(pages)
      return
    }
  } else {
    // A document or page artifact: the pages stack; the type's stylesheet lays them out.
    const wrapper = document.createElement('div')
    wrapper.className = `artifact artifact-${config.type} theme-${config.theme}`
    wrapper.append(...pages)
    root.replaceChildren(wrapper)
    const title = config.title || pages[0].dataset.pageTitle
    if (title) document.title = title
    // `#3` scrolls the third page into view, the way it shows the third slide of a deck: what the server's
    // screenshot of one page relies on.
    const wanted = Number.parseInt(window.location.hash.replace('#', ''), 10)
    if (wanted >= 1 && wanted <= pages.length) pages[wanted - 1].scrollIntoView()
    if (config.type === 'document' && sceneRequested()) {
      // The outline pass of the Word export (outline.ts): a render for the chrome service, not a viewer.
      writeOutline(pages)
      return
    }
  }

  // Viewer chrome belongs to the top-level viewer: inside a frame (the editor's live preview, an embedding page)
  // the surrounding page is the viewer and the toolbar stays out; it is told which page is showing instead.
  if (window.self === window.top) {
    initToolbar({ title: config.title, type: config.type, theme: config.theme, deck })
  } else {
    reportPosition(deck, pages.length)
  }
  // What an embedding page may read and drive as well: the editor's preview takes the deck's controller.
  window.openartifact = { type: config.type, title: config.title, deck }
}

/**
 * Tell the framing page which page is showing, now and after every navigation, as a `postMessage` of
 * `{ type: 'openartifact:page', page, total }` (1-based) to the parent on the same origin. The editor's preview
 * listens so the chat can say what the user is looking at. A document or page artifact reports page 1 once.
 */
function reportPosition(deck: DeckController | null, total: number): void {
  const post = (page: number) =>
    window.parent.postMessage({ type: 'openartifact:page', page, total }, window.location.origin)
  if (deck) {
    post(deck.position().index + 1)
    deck.onChange((position) => post(position.index + 1))
  } else {
    post(1)
  }
}

main()
