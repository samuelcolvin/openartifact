/**
 * Scene mode, the measurement pass behind the PowerPoint export (`backend/pptx.py`).
 *
 * The export is a hybrid: each slide is a screenshot of the page with its text made invisible, and every block of
 * text is laid over it as an editable text box at the position, size, font and colour the browser gave it. Both
 * halves come from the page itself when it is opened with `?scene` in the URL:
 *
 *  - `measureScene` walks every page after it is built and writes a JSON scene into a
 *    `<script type="application/json" id="artifact-scene">` block at the end of <body>, which the chrome service
 *    reads out of a `--dump-dom` of the page. Positions are in CSS pixels of the unscaled slide, relative to the
 *    page's top-left corner, so the presenter's `--page-scale` and the hidden pages' absolute positioning do
 *    not matter: a hidden page keeps its layout, and a rect is divided by the scale the page was measured at.
 *  - `hideText` then wraps every text node in a span whose colour, text shadow and decoration are transparent,
 *    so a screenshot of the same URL shows everything but the glyphs: backgrounds, borders, images, SVG
 *    components, the `::before` bullets and heading prefixes of the markdown themes, which are not text nodes
 *    and keep their colour.
 *
 * A block is the smallest element whose text is laid out as one run of lines: a paragraph, a heading, a list
 * item, a table cell, a code block. Each carries its runs (one per text node, with the parent element's computed
 * font) joined in document order; `<br>` and the newlines of preformatted text are explicit line breaks. Text
 * inside `<svg>` stays in the picture. Everything here is synchronous, like the rest of the runtime.
 */

/** One stretch of text with one style: a text node's content under its parent element's computed font. */
export interface SceneRun {
  text: string
  /** The first usable family of the computed `font-family` list; a CSS generic is passed through for Python to map. */
  font: string
  /** Font size in CSS pixels. */
  size: number
  bold: boolean
  italic: boolean
  underline: boolean
  strike: boolean
  /** `#rrggbb` of the computed colour. */
  color: string
  /** Letter spacing in CSS pixels, 0 when normal. */
  spacing: number
  /** An absolute link around this run, for a hyperlink on the text. */
  href?: string
}

/** One text box of the slide: a block of text and its box, in slide pixels from the page's top-left corner. */
export interface SceneBlock {
  x: number
  y: number
  w: number
  h: number
  align: 'left' | 'center' | 'right' | 'justify'
  /** Line pitch in CSS pixels: the computed `line-height` of the block. */
  lineHeight: number
  /** Runs in order; a `text` of `"\n"` is a line break. */
  runs: SceneRun[]
}

export interface ScenePage {
  index: number
  blocks: SceneBlock[]
}

/** What the `#artifact-scene` block holds. */
export interface Scene {
  /** The slide size in CSS pixels. */
  width: number
  height: number
  pages: ScenePage[]
}

/** The query flag that turns scene mode on: `/artifacts/<id>/?scene#3`. */
export function sceneRequested(): boolean {
  return new URLSearchParams(window.location.search).has('scene')
}

/** Tags whose text is a picture, not text. */
const SKIP_TAGS = new Set(['svg', 'script', 'style', 'template', 'noscript'])

/** Display values of an element that is laid out inline with its siblings' text. */
const INLINE_DISPLAYS = new Set(['inline', 'inline-block', 'inline-flex', 'inline-grid', 'contents'])

/** Family names that name the platform's UI font: no use to PowerPoint, which has its own idea of them. */
const SYSTEM_FAMILIES = new Set(['system-ui', 'ui-sans-serif', 'ui-serif', 'ui-monospace', 'ui-rounded'])

/**
 * A computed colour (`rgb(r, g, b)`, `rgba(r, g, b, a)` or `color(srgb r g b / a)`) as `#rrggbb`; null when fully
 * transparent, black when it is in a colour space this does not read.
 */
export function hexColor(css: string): string | null {
  const hex = (n: number) =>
    Math.max(0, Math.min(255, Math.round(n)))
      .toString(16)
      .padStart(2, '0')
  const rgb = /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/.exec(css)
  if (rgb) {
    if (rgb[4] !== undefined && Number.parseFloat(rgb[4]) === 0) return null
    return `#${hex(Number(rgb[1]))}${hex(Number(rgb[2]))}${hex(Number(rgb[3]))}`
  }
  const srgb = /color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)/.exec(css)
  if (srgb) {
    if (srgb[4] !== undefined && Number.parseFloat(srgb[4]) === 0) return null
    return `#${hex(Number(srgb[1]) * 255)}${hex(Number(srgb[2]) * 255)}${hex(Number(srgb[3]) * 255)}`
  }
  return '#000000'
}

/** The first family of a computed `font-family` list that is not a system keyword, unquoted. */
export function pickFamily(fontFamily: string): string {
  const families = fontFamily.split(',').map((f) => f.trim().replace(/^["']|["']$/g, ''))
  const usable = families.filter((f) => f && !f.startsWith('-') && !SYSTEM_FAMILIES.has(f))
  return usable[0] ?? families[0] ?? 'sans-serif'
}

/** Weight 600 and up is bold in PowerPoint's two-state world. */
function isBold(fontWeight: string): boolean {
  const n = Number.parseInt(fontWeight, 10)
  return Number.isFinite(n) ? n >= 600 : fontWeight === 'bold' || fontWeight === 'bolder'
}

/** Collapse the whitespace of `text` the way `white-space: normal` does; preformatted text keeps it. */
function collapse(text: string, preformatted: boolean): string {
  return preformatted ? text.replace(/\r\n?/g, '\n') : text.replace(/\s+/g, ' ')
}

/** Apply the computed `text-transform` to `text`, so the box says what the slide shows. */
function transform(text: string, textTransform: string): string {
  if (textTransform === 'uppercase') return text.toUpperCase()
  if (textTransform === 'lowercase') return text.toLowerCase()
  if (textTransform === 'capitalize') return text.replace(/\b\p{L}/gu, (c) => c.toUpperCase())
  return text
}

/** The nearest `<a href>` around `node` within the page, as an absolute URL, if any. */
function linkAround(node: Node, page: HTMLElement): string | undefined {
  const anchor = node.parentElement?.closest('a[href]')
  if (!anchor || !page.contains(anchor)) return undefined
  const href = (anchor as HTMLAnchorElement).href
  return /^https?:/.test(href) ? href : undefined
}

/** A text node's run, or null when it is invisible: whitespace only, transparent, or laid out nowhere. */
function runOf(node: Text, page: HTMLElement, preformatted: boolean): SceneRun | null {
  const parent = node.parentElement
  if (!parent) return null
  const style = getComputedStyle(parent)
  const raw = collapse(node.data, preformatted)
  // Whitespace between inline elements is a space the browser shows ("foo <b>bar</b> baz"); in preformatted
  // text it is exactly what it says, and a newline alone is a line break.
  if (preformatted && raw === '\n') return newline()
  const color = hexColor(style.color)
  if (color === null) return null
  const decoration = style.textDecorationLine
  return {
    text: transform(raw, style.textTransform),
    font: pickFamily(style.fontFamily),
    size: Number.parseFloat(style.fontSize),
    bold: isBold(style.fontWeight),
    italic: style.fontStyle === 'italic' || style.fontStyle === 'oblique',
    underline: decoration.includes('underline'),
    strike: decoration.includes('line-through'),
    color,
    spacing: style.letterSpacing === 'normal' ? 0 : Number.parseFloat(style.letterSpacing) || 0,
    href: linkAround(node, page),
  }
}

function newline(): SceneRun {
  return {
    text: '\n',
    font: '',
    size: 0,
    bold: false,
    italic: false,
    underline: false,
    strike: false,
    color: '',
    spacing: 0,
  }
}

/** Whether `el` takes part in layout at all: not `display: none`, `visibility: hidden` or fully transparent. */
function isShown(el: Element, style: CSSStyleDeclaration): boolean {
  if (style.display === 'none' || style.visibility === 'hidden') return false
  if (Number.parseFloat(style.opacity) === 0) return false
  // Build steps: a stepped element exports at its final (print) state, like the PDF.
  const printed = (el as HTMLElement).dataset?.stepPrint
  return printed !== 'pending' && printed !== 'done'
}

/** Whether `el` is laid out as a block of its own (anything not inline, from PowerPoint's point of view). */
function isBlockLevel(style: CSSStyleDeclaration): boolean {
  return !INLINE_DISPLAYS.has(style.display)
}

/** Whether every element under `el` is inline: `el` then lays its text out as one block. */
function inlineOnly(el: Element): boolean {
  for (const child of el.children) {
    if (SKIP_TAGS.has(child.tagName.toLowerCase())) continue
    const style = getComputedStyle(child)
    if (style.display === 'none') continue
    if (isBlockLevel(style) || !inlineOnly(child)) return false
  }
  return true
}

/** The runs of `el`'s text, in document order; `<br>` and preformatted newlines become `"\n"` runs. */
function collectRuns(el: Element, page: HTMLElement, preformatted: boolean, runs: SceneRun[]): void {
  for (const node of el.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      const run = runOf(node as Text, page, preformatted)
      if (run) pushRun(runs, run)
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node as Element
      const tag = child.tagName.toLowerCase()
      if (SKIP_TAGS.has(tag)) continue
      if (tag === 'br') {
        runs.push(newline())
        continue
      }
      const style = getComputedStyle(child)
      if (!isShown(child, style)) continue
      collectRuns(child, page, preformatted || style.whiteSpace.startsWith('pre'), runs)
    }
  }
}

/**
 * Append `run`: the newlines of preformatted text become runs of their own so Python sees line breaks, and a
 * space that follows a space (two collapsed text nodes meeting) is dropped, as the browser drops it.
 */
function pushRun(runs: SceneRun[], run: SceneRun): void {
  if (run.text === '\n') {
    runs.push(run)
    return
  }
  const previous = runs[runs.length - 1]
  if (previous && run.text.startsWith(' ') && (previous.text.endsWith(' ') || previous.text === '\n')) {
    run = { ...run, text: run.text.slice(1) }
    if (!run.text) return
  }
  if (!run.text.includes('\n')) {
    runs.push(run)
    return
  }
  const lines = run.text.split('\n')
  lines.forEach((line, i) => {
    if (i > 0) runs.push(newline())
    if (line) runs.push({ ...run, text: line })
  })
}

/** The glyph boxes of the text under an element: their union and the height of the tallest one on the top line. */
interface TextBounds {
  rect: DOMRect
  firstHeight: number
}

/** The union of the glyph boxes of every text node under `el`, in viewport coordinates; null without any. */
function textBounds(el: Element): TextBounds | null {
  const rects: DOMRect[] = []
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const range = document.createRange()
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if ((node as Text).data.trim() === '') continue
    const parent = node.parentElement
    if (!parent || parent.closest('svg,script,style')) continue
    range.selectNodeContents(node)
    for (const rect of range.getClientRects()) if (rect.width > 0 && rect.height > 0) rects.push(rect)
  }
  if (rects.length === 0) return null
  const left = Math.min(...rects.map((r) => r.left))
  const top = Math.min(...rects.map((r) => r.top))
  const right = Math.max(...rects.map((r) => r.right))
  const bottom = Math.max(...rects.map((r) => r.bottom))
  // The top line is every box that starts within the tallest box's reach of the top.
  const first = rects.filter((r) => r.top < top + r.height / 2)
  return {
    rect: new DOMRect(left, top, right - left, bottom - top),
    firstHeight: Math.max(...first.map((r) => r.height)),
  }
}

/** Build the block for `el`, whose text is one run of lines, in slide pixels; null when it shows no text. */
function blockOf(el: Element, page: HTMLElement, origin: DOMRect, scale: number): SceneBlock | null {
  const style = getComputedStyle(el)
  const runs: SceneRun[] = []
  collectRuns(el, page, style.whiteSpace.startsWith('pre'), runs)
  trimRuns(runs)
  if (runs.length === 0) return null
  const bounds = textBounds(el)
  if (!bounds) return null
  const lines = bounds.rect
  const sizes = runs.filter((run) => run.size > 0).map((run) => run.size)
  const lineHeight = (Number.parseFloat(style.lineHeight) || Math.max(...sizes) * 1.2) * scale
  // The box spans the element's content width, so PowerPoint wraps where the browser did and centred or
  // right-aligned text keeps its anchor. Left-aligned text starts where its first glyph is, past an inline
  // `::before` prefix or a text indent. Vertically it covers whole line boxes: the glyph boxes measured, plus
  // the half-leading above and below them, so the first line sits where the browser put it.
  const box = el.getBoundingClientRect()
  const contentLeft = box.left + Number.parseFloat(style.paddingLeft) * scale
  const contentRight = box.right - Number.parseFloat(style.paddingRight) * scale
  const textAlign = style.textAlign
  const align = textAlign === 'center' || textAlign === 'right' || textAlign === 'justify' ? textAlign : 'left'
  const left = align === 'left' ? Math.min(lines.left, contentRight) : contentLeft
  const width = Math.max(contentRight - left, lines.width)
  const leading = Math.max(0, lineHeight - bounds.firstHeight)
  return {
    x: round((left - origin.left) / scale),
    y: round((lines.top - leading / 2 - origin.top) / scale),
    w: round(width / scale),
    h: round((lines.height + leading) / scale),
    align,
    lineHeight: round(lineHeight / scale),
    runs,
  }
}

/** Drop the whitespace and line breaks a block starts and ends with, which the browser does not show either. */
function trimRuns(runs: SceneRun[]): void {
  while (runs.length) {
    const first = runs[0]
    const text = first.text === '\n' ? '' : first.text.replace(/^\s+/, '')
    if (text) {
      runs[0] = { ...first, text }
      break
    }
    runs.shift()
  }
  while (runs.length) {
    const last = runs[runs.length - 1]
    const text = last.text === '\n' ? '' : last.text.replace(/\s+$/, '')
    if (text) {
      runs[runs.length - 1] = { ...last, text }
      break
    }
    runs.pop()
  }
}

function round(n: number): number {
  return Math.round(n * 100) / 100
}

/** Walk `el`, emitting one block per element whose text is laid out together, outermost such element first. */
function collectBlocks(el: Element, page: HTMLElement, origin: DOMRect, scale: number, blocks: SceneBlock[]): void {
  const tag = el.tagName.toLowerCase()
  // A page component's navigation controls are viewer chrome: they stay in the picture.
  if (SKIP_TAGS.has(tag) || el.hasAttribute('data-nav')) return
  const style = getComputedStyle(el)
  if (!isShown(el, style)) return
  if (inlineOnly(el)) {
    const block = blockOf(el, page, origin, scale)
    if (block) blocks.push(block)
    return
  }
  // Mixed content, such as a list item holding text and a nested list: block-level children are walked, and each
  // run of inline siblings between them (text nodes and inline elements) is wrapped in a span so it is measured
  // and exported as one block. An inline wrapper changes nothing about the layout.
  let group: Node[] = []
  const flush = () => {
    if (group.some((node) => node.nodeType === Node.ELEMENT_NODE || (node as Text).data.trim() !== '')) {
      const span = document.createElement('span')
      group[0].parentNode?.insertBefore(span, group[0])
      span.append(...group)
      const block = blockOf(span, page, origin, scale)
      if (block) blocks.push(block)
    }
    group = []
  }
  for (const node of Array.from(el.childNodes)) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node as Element
      const display = getComputedStyle(child).display
      if (display === 'none') continue
      if (isBlockLevel(getComputedStyle(child)) || SKIP_TAGS.has(child.tagName.toLowerCase())) {
        flush()
        collectBlocks(child, page, origin, scale, blocks)
      } else {
        group.push(node)
      }
    } else if (node.nodeType === Node.TEXT_NODE) {
      group.push(node)
    }
  }
  flush()
}

/**
 * Prepare a deck for its scene: every build step at its final state, as in print, and the presenter at scale 1, so
 * that a screenshot window of exactly the slide's size shows exactly the slide. A stylesheet rule marked important
 * outranks the inline `--page-scale` the presenter writes whenever the window is resized, which headless Chrome
 * does once after load.
 */
export function prepareScene(): void {
  const style = document.createElement('style')
  style.textContent =
    '.deck-presenter{--page-scale:1!important}' +
    '[data-step-state]{opacity:1!important;visibility:visible!important;transition:none!important}' +
    "[data-step-print='pending'],[data-step-print='done']{opacity:0!important;visibility:hidden!important}"
  document.head.append(style)
}

/** Measure every page of a deck and write the scene into the page as `#artifact-scene`. */
export function measureScene(pages: HTMLElement[]): Scene {
  const first = pages[0].getBoundingClientRect()
  // `offsetWidth` is the layout size before transforms; the bounding rect is after the presenter's scale.
  const scale = first.width / pages[0].offsetWidth || 1
  const scene: Scene = {
    width: pages[0].offsetWidth,
    height: pages[0].offsetHeight,
    pages: pages.map((page, i) => {
      // The presenter hides every page but the active one; a hidden page keeps its layout, but the walk would
      // take it for invisible. Show it inline while it is measured, without the presenter's fade.
      const inline = page.getAttribute('style')
      page.style.cssText = 'visibility:visible!important;opacity:1!important;transition:none!important'
      const origin = page.getBoundingClientRect()
      const blocks: SceneBlock[] = []
      collectBlocks(page, page, origin, scale, blocks)
      if (inline === null) page.removeAttribute('style')
      else page.setAttribute('style', inline)
      return { index: i + 1, blocks }
    }),
  }
  const script = document.createElement('script')
  script.type = 'application/json'
  script.id = 'artifact-scene'
  script.textContent = JSON.stringify(scene).replace(/</g, '\\u003c')
  document.body.append(script)
  return scene
}

/**
 * Make every text node of `pages` invisible without moving anything: each is wrapped in a span whose text is
 * transparent and casts no shadow or decoration. Pseudo-element content, SVG and images keep their colours.
 */
export function hideText(pages: HTMLElement[]): void {
  const style = document.createElement('style')
  style.textContent =
    '.oa-scene-hidden{color:transparent!important;-webkit-text-fill-color:transparent!important;' +
    'text-shadow:none!important;text-decoration-color:transparent!important}' +
    '.page a,.page u,.page s,.page del,.page ins{text-decoration-color:transparent!important}'
  document.head.append(style)
  for (const page of pages) {
    const walker = document.createTreeWalker(page, NodeFilter.SHOW_TEXT)
    const nodes: Text[] = []
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement
      if (!parent || parent.closest('svg,script,style')) continue
      if ((node as Text).data.trim() !== '') nodes.push(node as Text)
    }
    for (const node of nodes) {
      const span = document.createElement('span')
      span.className = 'oa-scene-hidden'
      node.parentNode?.insertBefore(span, node)
      span.append(node)
    }
  }
}
