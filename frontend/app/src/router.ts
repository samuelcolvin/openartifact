/**
 * Two routes are not worth a router library: `/` is the artifact list, `/edit/<id>` the editor. The server
 * serves the same shell for both; `navigate` pushes a history entry and the app re-renders on `popstate`.
 */

import { useEffect, useState } from 'react'

export type Route = { page: 'list' } | { page: 'edit'; id: string } | { page: 'missing' }

export function parseRoute(pathname: string): Route {
  if (pathname === '/') return { page: 'list' }
  const match = /^\/edit\/([0-9a-f-]{36})\/?$/.exec(pathname)
  if (match) return { page: 'edit', id: match[1] }
  return { page: 'missing' }
}

const listeners = new Set<() => void>()

/** Go to a path within the app without a full page load. */
export function navigate(path: string): void {
  window.history.pushState(null, '', path)
  for (const listener of listeners) listener()
}

/** The current route, updated on `navigate` and the browser's back / forward. */
export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname))
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.pathname))
    listeners.add(update)
    window.addEventListener('popstate', update)
    return () => {
      listeners.delete(update)
      window.removeEventListener('popstate', update)
    }
  }, [])
  return route
}
