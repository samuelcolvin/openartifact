/**
 * Outline mode, the measurement pass behind the Word export (`backend/word.py`): the counterpart of scene.ts for a
 * document artifact.
 *
 * A document opened with `?scene` in the URL writes a semantic outline of its pages into a
 * `<script type="application/json" id="artifact-scene">` block (the same block the chrome service reads for a
 * deck's scene; what it holds is told apart by `kind`). Where a deck's scene is geometry, a document's outline is
 * structure: headings, paragraphs, lists, code blocks, quotes, tables, images and rules, each with its text as
 * runs that keep bold, italic, code, underline, strike-through and links. Nothing is measured: Word lays the
 * text out again on its own pages, and a page of the artifact becomes a page break.
 *
 * Only the rendered markdown, `.page-body`, is walked: a page component's header or footer is the page's chrome,
 * which Word has its own idea of. A component's HTML inside the body is walked like any other: its containers
 * are looked through, its headings, paragraphs and lists are what they are. Text inside `<svg>` stays out, as
 * does anything hidden.
 */

/** A stretch of text in one style; a `text` of `"\n"` alone is a line break. */
export interface OutlineRun {
  text: string
  bold: boolean
  italic: boolean
  code: boolean
  underline: boolean
  strike: boolean
  href?: string
}

export interface OutlineItem {
  runs: OutlineRun[]
  /** What the item holds besides its own text: a nested list, a paragraph of a loose list, a code block. */
  blocks: OutlineBlock[]
}

export interface OutlineCell {
  runs: OutlineRun[]
  header: boolean
}

export type OutlineBlock =
  | { type: 'heading'; level: number; runs: OutlineRun[] }
  | { type: 'paragraph'; runs: OutlineRun[] }
  | { type: 'list'; ordered: boolean; items: OutlineItem[] }
  | { type: 'code'; text: string }
  | { type: 'quote'; blocks: OutlineBlock[] }
  | { type: 'table'; rows: OutlineCell[][] }
  | { type: 'image'; src: string; alt: string }
  | { type: 'rule' }

export interface OutlinePage {
  index: number
  blocks: OutlineBlock[]
}

/** What the `#artifact-scene` block holds for a document. */
export interface Outline {
  kind: 'document'
  pages: OutlinePage[]
}

/** Tags whose content is not text to export. */
const SKIP_TAGS = new Set(['svg', 'script', 'style', 'template', 'noscript', 'button'])

/** Display values of an element that is laid out inline with its siblings' text. */
const INLINE_DISPLAYS = new Set(['inline', 'inline-block', 'inline-flex', 'inline-grid', 'contents'])

/** Collapse whitespace the way `white-space: normal` does. */
function collapse(text: string): string {
  return text.replace(/\s+/g, ' ')
}

function isHidden(el: Element): boolean {
  const style = getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden') return true
  const printed = (el as HTMLElement).dataset?.stepPrint
  return printed === 'pending' || printed === 'done'
}

function isInline(el: Element): boolean {
  return INLINE_DISPLAYS.has(getComputedStyle(el).display)
}

function skipped(el: Element): boolean {
  return SKIP_TAGS.has(el.tagName.toLowerCase()) || el.hasAttribute('data-nav') || isHidden(el)
}

function weightOf(el: Element): number {
  const weight = getComputedStyle(el).fontWeight
  const n = Number.parseInt(weight, 10)
  return Number.isFinite(n) ? n : weight === 'bold' ? 700 : 400
}

function italicOf(el: Element): boolean {
  const style = getComputedStyle(el).fontStyle
  return style === 'italic' || style === 'oblique'
}

/**
 * The style of a text node within the block `root`: the tags markdown uses between the two, and a weight or slant
 * the computed font has beyond the block's own (a heading is bold as a heading, not as a run of bold text).
 */
function styleOf(node: Text, root: Element): Omit<OutlineRun, 'text'> {
  const style: Omit<OutlineRun, 'text'> = { bold: false, italic: false, code: false, underline: false, strike: false }
  const parent = node.parentElement
  if (parent && parent !== root) {
    style.bold = weightOf(parent) > weightOf(root)
    style.italic = italicOf(parent) && !italicOf(root)
  }
  for (let el: Element | null = parent; el && el !== root; el = el.parentElement) {
    switch (el.tagName.toLowerCase()) {
      case 'code':
      case 'kbd':
      case 'samp':
        style.code = true
        break
      case 'u':
      case 'ins':
        style.underline = true
        break
      case 's':
      case 'del':
      case 'strike':
        style.strike = true
        break
      case 'a': {
        const href = (el as HTMLAnchorElement).href
        if (/^https?:/.test(href)) style.href = href
        break
      }
    }
  }
  return style
}

/** Append `run` to `runs`, merging it into the previous run when the style is the same, dropping doubled spaces. */
function pushRun(runs: OutlineRun[], run: OutlineRun): void {
  const previous = runs[runs.length - 1]
  if (previous && run.text.startsWith(' ') && (previous.text.endsWith(' ') || previous.text === '\n')) {
    run = { ...run, text: run.text.slice(1) }
    if (!run.text) return
  }
  if (
    previous &&
    previous.text !== '\n' &&
    run.text !== '\n' &&
    previous.bold === run.bold &&
    previous.italic === run.italic &&
    previous.code === run.code &&
    previous.underline === run.underline &&
    previous.strike === run.strike &&
    previous.href === run.href
  ) {
    previous.text += run.text
    return
  }
  runs.push(run)
}

/**
 * The runs of the inline content under `el`, styled relative to the block `root` (`el` itself at the top); images
 * met on the way are collected into `images`.
 */
function inlineRuns(el: Element, runs: OutlineRun[], images: OutlineBlock[], root: Element = el): void {
  for (const node of el.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = collapse((node as Text).data)
      if (text === '') continue
      pushRun(runs, { text, ...styleOf(node as Text, root) })
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node as Element
      const tag = child.tagName.toLowerCase()
      if (tag === 'br') {
        runs.push({ text: '\n', bold: false, italic: false, code: false, underline: false, strike: false })
      } else if (tag === 'img') {
        images.push(imageBlock(child as HTMLImageElement))
      } else if (!skipped(child)) {
        inlineRuns(child, runs, images, root)
      }
    }
  }
}

/** Trim the whitespace and line breaks a run list starts and ends with. */
function trimRuns(runs: OutlineRun[]): OutlineRun[] {
  while (runs.length) {
    const text = runs[0].text === '\n' ? '' : runs[0].text.replace(/^\s+/, '')
    if (text) {
      runs[0] = { ...runs[0], text }
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
  return runs
}

function imageBlock(img: HTMLImageElement): OutlineBlock {
  return { type: 'image', src: img.getAttribute('src') ?? '', alt: img.alt }
}

/** The runs of `el`'s inline content as a paragraph-like block of `type`, followed by any images it held. */
function textBlock(el: Element, make: (runs: OutlineRun[]) => OutlineBlock, out: OutlineBlock[]): void {
  const runs: OutlineRun[] = []
  const images: OutlineBlock[] = []
  inlineRuns(el, runs, images)
  if (trimRuns(runs).length) out.push(make(runs))
  out.push(...images)
}

/** A list item: its own inline content as runs, its block-level children (a nested list, a paragraph) as blocks. */
function listItem(li: Element): OutlineItem {
  const runs: OutlineRun[] = []
  const images: OutlineBlock[] = []
  const blocks: OutlineBlock[] = []
  // A loose list wraps the item's text in a paragraph; take the first paragraph as the item's own text.
  let own = true
  for (const node of Array.from(li.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = collapse((node as Text).data)
      if (text !== '') pushRun(runs, { text, ...styleOf(node as Text, li) })
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node as Element
      if (skipped(child)) continue
      const tag = child.tagName.toLowerCase()
      if (tag === 'br') {
        runs.push({ text: '\n', bold: false, italic: false, code: false, underline: false, strike: false })
      } else if (tag === 'img') {
        images.push(imageBlock(child as HTMLImageElement))
      } else if (isInline(child)) {
        inlineRuns(child, runs, images, li)
      } else if (tag === 'p' && own && runs.length === 0) {
        inlineRuns(child, runs, images, child)
        own = false
      } else {
        collectBlocks(child, blocks)
      }
    }
  }
  return { runs: trimRuns(runs), blocks: [...images, ...blocks] }
}

/** A table's rows: each cell's inline content as runs, header cells marked. */
function tableBlock(table: Element): OutlineBlock {
  const rows: OutlineCell[][] = []
  for (const tr of table.querySelectorAll('tr')) {
    const cells: OutlineCell[] = []
    for (const cell of tr.children) {
      const tag = cell.tagName.toLowerCase()
      if (tag !== 'td' && tag !== 'th') continue
      const runs: OutlineRun[] = []
      inlineRuns(cell, runs, [])
      cells.push({ runs: trimRuns(runs), header: tag === 'th' })
    }
    if (cells.length) rows.push(cells)
  }
  return { type: 'table', rows }
}

/**
 * Walk `el`, appending the blocks it holds to `out`. Known block tags become blocks; any other container is looked
 * through, with each run of inline siblings between its block children made a paragraph.
 */
function collectBlocks(el: Element, out: OutlineBlock[]): void {
  if (skipped(el)) return
  const tag = el.tagName.toLowerCase()
  switch (tag) {
    case 'h1':
    case 'h2':
    case 'h3':
    case 'h4':
    case 'h5':
    case 'h6':
      textBlock(el, (runs) => ({ type: 'heading', level: Number(tag[1]), runs }), out)
      return
    case 'p':
      textBlock(el, (runs) => ({ type: 'paragraph', runs }), out)
      return
    case 'ul':
    case 'ol': {
      const items = Array.from(el.children)
        .filter((child) => child.tagName.toLowerCase() === 'li' && !skipped(child))
        .map(listItem)
      if (items.length) out.push({ type: 'list', ordered: tag === 'ol', items })
      return
    }
    case 'pre':
      out.push({ type: 'code', text: (el.textContent ?? '').replace(/\n$/, '') })
      return
    case 'blockquote': {
      const blocks: OutlineBlock[] = []
      container(el, blocks)
      out.push({ type: 'quote', blocks })
      return
    }
    case 'table':
      out.push(tableBlock(el))
      return
    case 'hr':
      out.push({ type: 'rule' })
      return
    case 'img':
      out.push(imageBlock(el as HTMLImageElement))
      return
    default:
      container(el, out)
  }
}

/** Look through a container: block children are walked, runs of inline siblings between them become paragraphs. */
function container(el: Element, out: OutlineBlock[]): void {
  let runs: OutlineRun[] = []
  let images: OutlineBlock[] = []
  const flush = () => {
    if (trimRuns(runs).length) out.push({ type: 'paragraph', runs })
    out.push(...images)
    runs = []
    images = []
  }
  for (const node of Array.from(el.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = collapse((node as Text).data)
      if (text !== '') pushRun(runs, { text, ...styleOf(node as Text, el) })
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node as Element
      if (skipped(child)) continue
      const tag = child.tagName.toLowerCase()
      if (tag === 'br') {
        runs.push({ text: '\n', bold: false, italic: false, code: false, underline: false, strike: false })
      } else if (tag === 'img') {
        images.push(imageBlock(child as HTMLImageElement))
      } else if (isInline(child)) {
        inlineRuns(child, runs, images, el)
      } else {
        flush()
        collectBlocks(child, out)
      }
    }
  }
  flush()
}

/** Outline every page of a document and write it into the page as `#artifact-scene`. */
export function writeOutline(pages: HTMLElement[]): Outline {
  const outline: Outline = {
    kind: 'document',
    pages: pages.map((page, i) => {
      const body = page.querySelector('.page-body') ?? page
      const blocks: OutlineBlock[] = []
      container(body, blocks)
      return { index: i + 1, blocks }
    }),
  }
  const script = document.createElement('script')
  script.type = 'application/json'
  script.id = 'artifact-scene'
  script.textContent = JSON.stringify(outline).replace(/</g, '\\u003c')
  document.body.append(script)
  return outline
}
