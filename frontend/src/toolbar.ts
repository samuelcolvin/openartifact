/**
 * The viewer toolbar: product chrome the runtime adds to every artifact page, never part of the HTML
 * `build.py` writes. One host div is appended to <body> (outside `#root`, which main.ts replaces) and
 * everything lives in its shadow root, so the artifact's styles.css cannot restyle it and toolbar.css
 * cannot leak out.
 *
 * It holds the artifact title, for a deck the previous / next buttons, a counter and a full-screen
 * toggle, a Download menu for the `.pdf`, `.md` and `.zip` exports served beside the page, and the
 * brand. Fork and Edit will join the right-hand group later.
 *
 * Auto-hide: the bar shows while the pointer is near the top of the window or over the bar, while
 * focus is inside it or the menu is open, and slides away a second after the pointer leaves that
 * area; a pointer over the bulk of the page never shows it. It is pinned where there is no hover
 * (touch) and hidden in print by toolbar.css. Building the DOM is synchronous like the rest of the runtime; the timers
 * only toggle visibility afterwards.
 */

import type { DeckController, DeckPosition } from './deck.ts'
import toolbarCss from './styles/toolbar.css'
import type { ArtifactType, DeckTheme } from './types.ts'

export interface ToolbarOptions {
  title?: string
  type: ArtifactType
  theme: DeckTheme
  /** The deck's controller, or null for a document or page (no navigation group). */
  deck: DeckController | null
}

/** Pointer this close to the top edge (px) shows the bar. */
const NEAR_TOP = 96
/** How long the bar lingers after the pointer leaves that area (ms). */
const LINGER = 1000
/** How long the bar is shown at load, so visitors learn it is there (ms). */
const INTRO_SHOW = 2500

const ICONS = {
  prev: '<svg viewBox="0 0 16 16"><path d="M10 3 5 8l5 5"/></svg>',
  next: '<svg viewBox="0 0 16 16"><path d="m6 3 5 5-5 5"/></svg>',
  enterFullscreen: '<svg viewBox="0 0 16 16"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/></svg>',
  exitFullscreen: '<svg viewBox="0 0 16 16"><path d="M6 2v4H2M14 6h-4V2M10 14v-4h4M2 10h4v4"/></svg>',
  chevron: '<svg viewBox="0 0 12 12"><path d="m3 4.5 3 3 3-3"/></svg>',
}

const EXPORTS: Array<{ ext: string; label: string; hint?: string }> = [
  { ext: '.pdf', label: 'PDF', hint: 'printed on request, takes a moment' },
  { ext: '.md', label: 'Markdown', hint: 'the source, behind a summary' },
  { ext: '.zip', label: 'Source (zip)', hint: 'every file of the artifact' },
]

/**
 * The path the exports hang off: the page is `/artifacts/<id>/`, the exports `/artifacts/<id>.pdf` and
 * friends. Null when the page is not served that way (nothing to link to).
 */
function exportBase(pathname: string): string | null {
  const match = /^(.+?)\/(index\.html)?$/.exec(pathname)
  return match?.[1] ?? null
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  ...children: Array<Node | string>
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value)
  node.append(...children)
  return node
}

/** A button whose content is one of the inline SVG icons. */
function iconButton(action: string, icon: string, label: string): HTMLButtonElement {
  const button = el('button', {
    type: 'button',
    class: 'icon',
    'data-action': action,
    title: label,
    'aria-label': label,
  })
  button.innerHTML = icon
  return button
}

function toggleFullscreen(): void {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {})
  } else {
    document.documentElement.requestFullscreen().catch(() => {})
  }
}

/** Build the toolbar into a shadow host appended to <body>, and return the host. */
export function initToolbar(options: ToolbarOptions): HTMLElement {
  const host = el('div', { id: 'openartifact-toolbar', 'data-type': options.type })
  // A deck sits on the always-dark deck backdrop; a document or page follows its theme.
  const light = options.type !== 'deck' && (options.theme === 'light' || options.theme === 'markdown-light')
  host.dataset.scheme = light ? 'light' : 'dark'
  const shadow = host.attachShadow({ mode: 'open' })
  shadow.append(el('style', {}, toolbarCss))

  const start = el('div', { class: 'group start' })
  if (options.title) start.append(el('span', { class: 'title', title: options.title }, options.title))

  const center = el('div', { class: 'group center' })
  if (options.deck) buildNavigation(center, options.deck)

  const end = el('div', { class: 'group end' })
  const base = exportBase(window.location.pathname)
  const menu = base === null ? null : buildDownloadMenu(end, base)
  end.append(el('a', { class: 'brand', href: '/' }, 'OpenArtifact'))

  shadow.append(el('div', { class: 'bar', role: 'toolbar', 'aria-label': 'OpenArtifact viewer' }, start, center, end))

  // A mouse click must not move focus into the bar: the deck's keyboard handling listens on window and would
  // otherwise stop reaching the page, and :focus-within would pin the bar open. Tab still focuses the buttons.
  shadow.addEventListener('mousedown', (e) => {
    if ((e.target as Element).closest('button')) e.preventDefault()
  })
  // A focused button activates on Space / Enter by itself; the deck must not also advance.
  shadow.addEventListener('keydown', (e) => {
    const key = (e as KeyboardEvent).key
    if (key === ' ' || key === 'Enter') e.stopPropagation()
  })
  // The deck's wheel listener on window flips pages; a flick over the bar or the open menu should not.
  host.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true })

  installAutoHide(host, () => menu?.isOpen() ?? false)
  document.body.append(host)
  return host
}

/** Previous / next, the counter and the full-screen toggle, kept in step with the deck. */
function buildNavigation(group: HTMLElement, deck: DeckController): void {
  const prev = iconButton('prev', ICONS.prev, 'Previous (Left; Shift+Left for a whole page)')
  const next = iconButton('next', ICONS.next, 'Next (Right or Space)')
  const current = el('b', {}, '1')
  const counter = el('span', { class: 'counter' }, current, ` / ${deck.total}`)
  group.append(prev, counter, next)
  prev.addEventListener('click', () => deck.go(-1))
  next.addEventListener('click', () => deck.go(1))

  const render = (p: DeckPosition) => {
    current.textContent = String(p.index + 1)
    prev.disabled = p.atStart
    next.disabled = p.atEnd
  }
  render(deck.position())
  deck.onChange(render)

  if (document.fullscreenEnabled) {
    const fullscreen = iconButton('fullscreen', ICONS.enterFullscreen, 'Full screen (F)')
    group.append(fullscreen)
    fullscreen.addEventListener('click', toggleFullscreen)
    document.addEventListener('fullscreenchange', () => {
      const active = document.fullscreenElement !== null
      fullscreen.innerHTML = active ? ICONS.exitFullscreen : ICONS.enterFullscreen
      const label = active ? 'Exit full screen (F)' : 'Full screen (F)'
      fullscreen.title = label
      fullscreen.setAttribute('aria-label', label)
    })
    // `F` from the page itself (not from a focused control) toggles too.
    document.addEventListener('keydown', (e) => {
      if (e.key === 'f' && !e.metaKey && !e.ctrlKey && !e.altKey && e.target === document.body) toggleFullscreen()
    })
  }
}

interface Menu {
  isOpen(): boolean
}

/** The Download menu: one link per export, with the keyboard handling of a menu. */
function buildDownloadMenu(group: HTMLElement, base: string): Menu {
  const button = el(
    'button',
    {
      type: 'button',
      class: 'menu-button',
      'data-action': 'download',
      'aria-haspopup': 'menu',
      'aria-expanded': 'false',
    },
    'Download',
  )
  button.insertAdjacentHTML('beforeend', ICONS.chevron)
  const items = EXPORTS.map(({ ext, label, hint }) => {
    const link = el('a', { role: 'menuitem', href: base + ext, download: '' }, label)
    if (hint) link.append(el('small', {}, hint))
    return link
  })
  const menu = el('div', { class: 'menu', role: 'menu', hidden: '' }, ...items)
  const wrap = el('div', { class: 'menu-wrap' }, button, menu)
  group.append(wrap)

  const setOpen = (open: boolean) => {
    menu.hidden = !open
    button.setAttribute('aria-expanded', String(open))
  }
  const isOpen = () => !menu.hidden

  button.addEventListener('click', () => setOpen(!isOpen()))
  // Opened from the keyboard, the first item takes focus; a mouse click leaves focus where it was.
  button.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      e.stopPropagation()
      setOpen(true)
      items[0].focus()
    }
  })
  menu.addEventListener('keydown', (e) => {
    const index = items.indexOf(document.activeElement as HTMLAnchorElement)
    const focusItem = (i: number) => items[(i + items.length) % items.length].focus()
    switch (e.key) {
      case 'ArrowDown':
        focusItem(index + 1)
        break
      case 'ArrowUp':
        focusItem(index - 1)
        break
      case 'Home':
        focusItem(0)
        break
      case 'End':
        focusItem(items.length - 1)
        break
      case 'Escape':
        setOpen(false)
        button.focus()
        break
      default:
        return
    }
    e.preventDefault()
    e.stopPropagation()
  })
  menu.addEventListener('click', () => setOpen(false))
  // Escape anywhere closes it; the deck ignores Escape, so no need to stop it.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) setOpen(false)
  })
  // A pointer down anywhere outside the menu and its button closes it (the composed path sees into the shadow).
  document.addEventListener('pointerdown', (e) => {
    if (isOpen() && !e.composedPath().includes(wrap)) setOpen(false)
  })

  return { isOpen }
}

/** Show the bar while the pointer is near the top, and for a moment after it leaves; never over the page itself. */
function installAutoHide(host: HTMLElement, menuOpen: () => boolean): void {
  let nearTop = false
  let lingering = false
  // Automated captures (a future screenshot endpoint) should never see the intro. Print is covered by CSS.
  const automated = navigator.webdriver || /HeadlessChrome/.test(navigator.userAgent)
  let intro = !automated
  let timer: ReturnType<typeof setTimeout> | undefined

  const update = () => {
    const visible = nearTop || lingering || intro || menuOpen()
    if (visible !== host.hasAttribute('data-visible')) host.toggleAttribute('data-visible', visible)
  }
  const after = (ms: number, then: () => void) => {
    clearTimeout(timer)
    timer = setTimeout(then, ms)
  }
  const leave = () => {
    if (!nearTop) return
    nearTop = false
    lingering = true
    after(LINGER, () => {
      lingering = false
      update()
    })
  }

  window.addEventListener('mousemove', (e) => {
    intro = false
    if (e.clientY < NEAR_TOP) {
      nearTop = true
      lingering = false
      clearTimeout(timer)
    } else {
      leave()
    }
    update()
  })
  document.addEventListener('mouseleave', () => {
    leave()
    update()
  })

  // The bar is hidden by default; the intro shows it after the first paint, then the pointer rules take over.
  if (intro) {
    update()
    after(INTRO_SHOW, () => {
      intro = false
      update()
    })
  }
}
