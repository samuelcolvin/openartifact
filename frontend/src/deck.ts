/**
 * Presenter behaviour, a vanilla port of the React `Deck` component this project used to ship:
 *
 *  - the current page lives in the URL hash (`#3` is the third page, 1-indexed)
 *  - keyboard (arrows, space, page up/down), wheel and any `[data-nav]` element in a page component navigate
 *  - "next" and "previous" step through a page's build steps (`data-step`, see steps.ts)
 *    before moving between pages; a page entered backwards opens on its last step
 *  - shift + left/right jump a whole page, skipping build steps
 *  - the page stream is scaled to fit the viewport via `--page-scale`
 *  - `document.title` follows the active page's `PAGE_TITLE`
 *  - `#N` links work as they do anywhere, so a page component can link to a page
 *
 * Everything here runs synchronously at load, before first paint, so headless Chrome's
 * print-to-PDF sees the counters and every page.
 */

import { initSteps } from './steps.ts'
import type { ArtifactConfig } from './types.ts'

/** Transition style between pages: 'fade' for crossfade, 'slide' for directional slide. */
const TRANSITION: 'fade' | 'slide' = 'fade'

const STATE_CLASSES = ['page--active', 'page--enter-fwd', 'page--enter-back', 'page--exit-fwd', 'page--exit-back']

/** Read the page index from the hash, clamped to the deck; null when the hash is not a number. */
function indexFromHash(total: number): number | null {
  const n = Number.parseInt(window.location.hash.replace('#', ''), 10)
  if (!Number.isFinite(n) || n < 1) return null
  return Math.min(n - 1, total - 1)
}

export function initDeck(presenter: HTMLElement, config: ArtifactConfig): void {
  const pages = Array.from(presenter.querySelectorAll<HTMLElement>('.page'))
  const total = pages.length
  if (total === 0) return
  const steps = pages.map(initSteps)

  let current = indexFromHash(total) ?? 0
  // Navigation direction and the previously active page, used for directional transitions.
  let dir: -1 | 1 = 1
  let prev: number | null = null
  if (!window.location.hash) window.history.replaceState(null, '', `#${current + 1}`)

  pages.forEach((page, i) => {
    page.dataset.pageIndex = String(i)
  })

  /** Mark the active page, apply transition classes and update the document title. */
  const apply = () => {
    pages.forEach((page, i) => {
      page.classList.remove(...STATE_CLASSES)
      if (i === current) {
        page.classList.add('page--active')
        // The page's PAGE_TITLE (its title directive, else its first h1), else the deck title.
        const title = page.dataset.pageTitle || config.title
        if (title) document.title = title
        if (prev !== null && TRANSITION === 'slide') {
          page.classList.add(dir === 1 ? 'page--enter-fwd' : 'page--enter-back')
        }
      } else if (i === prev && TRANSITION === 'slide') {
        page.classList.add(dir === 1 ? 'page--exit-fwd' : 'page--exit-back')
      }
    })
    // Clear prev so it does not re-trigger an animation on the next apply.
    prev = null
  }

  /**
   * Jump to a page index, recording direction for transitions and syncing the hash. The
   * page opens on its first step, or on its last when `direction` is backwards and
   * `lastStep` is set (so stepping back through a deck retraces every build).
   */
  const setCurrent = (next: number, direction: -1 | 1, lastStep = false) => {
    dir = direction
    if (next !== current) prev = current
    current = next
    steps[next].set(lastStep ? steps[next].count - 1 : 0)
    window.history.replaceState(null, '', `#${next + 1}`)
    apply()
  }

  /** Advance or retreat one step, spilling over to the neighbouring page at either end. */
  const go = (direction: -1 | 1) => {
    const s = steps[current]
    const nextStep = s.current + direction
    if (nextStep >= 0 && nextStep < s.count) {
      s.set(nextStep)
      return
    }
    const next = Math.max(0, Math.min(total - 1, current + direction))
    if (next !== current) setCurrent(next, direction, direction === -1)
  }

  /** Scale factor to fit one page in the viewport with a little padding. */
  const updateScale = () => {
    const page = pages[0]
    const padding = 48
    const vw = window.innerWidth - padding
    const vh = window.innerHeight - padding
    const scale = Math.min(vw / page.offsetWidth, vh / page.offsetHeight)
    presenter.style.setProperty('--page-scale', String(scale))
  }

  /** Jump a whole page in `direction`, ignoring build steps; the target opens on its first step. */
  const jump = (direction: -1 | 1) => {
    const next = Math.max(0, Math.min(total - 1, current + direction))
    if (next !== current) setCurrent(next, direction)
  }

  // Keyboard navigation. Shift + arrow skips build steps and moves one page.
  window.addEventListener('keydown', (e) => {
    if (e.shiftKey && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
      e.preventDefault()
      jump(e.key === 'ArrowRight' ? 1 : -1)
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ' || e.key === 'PageDown') {
      e.preventDefault()
      go(1)
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'PageUp') {
      e.preventDefault()
      go(-1)
    }
  })

  // Wheel navigation with a cooldown to tame trackpad inertia.
  let cooldown = false
  window.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault()
      if (cooldown) return
      if (Math.abs(e.deltaY) < 30) return
      go(e.deltaY > 0 ? 1 : -1)
      cooldown = true
      setTimeout(() => {
        cooldown = false
      }, 400)
    },
    { passive: false },
  )

  // Browser back/forward or a hand-edited hash.
  window.addEventListener('hashchange', () => {
    const n = indexFromHash(total)
    if (n !== null) {
      current = n
      steps[n].set(0)
      apply()
    }
  })

  // Click delegation for a page component's own controls: `data-nav="prev" | "next" | "first" | "last"`.
  presenter.addEventListener('click', (e) => {
    const control = (e.target as HTMLElement).closest<HTMLElement>('[data-nav]')
    if (!control) return
    e.preventDefault()
    switch (control.dataset.nav) {
      case 'prev':
        go(-1)
        break
      case 'next':
        go(1)
        break
      case 'first':
        setCurrent(0, -1)
        break
      case 'last':
        setCurrent(total - 1, 1)
        break
    }
  })

  window.addEventListener('resize', updateScale)
  apply()
  updateScale()
}
