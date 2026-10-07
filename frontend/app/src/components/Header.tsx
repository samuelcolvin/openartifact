import type { ReactNode } from 'react'
import type { Me } from '../api.ts'
import { Avatar, Wordmark } from './ui.tsx'

/** The bar at the top of every page: the wordmark, whatever the page adds in the middle, and the account. */
export function Header({ me, children }: { me: Me | null; children?: ReactNode }) {
  const name = me?.viewer.name || me?.viewer.email || 'Account'
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-4">
      <a href="/" className="text-fg hover:opacity-80">
        <Wordmark />
      </a>
      <div className="flex min-w-0 flex-1 items-center gap-3">{children}</div>
      {me ? (
        <details className="relative">
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md px-1.5 py-1 hover:bg-white/8">
            <Avatar name={name} picture={me.viewer.picture} />
          </summary>
          <div className="absolute top-full right-0 z-20 mt-2 w-64 rounded-lg border border-line bg-raised p-1.5 shadow-2xl">
            <div className="px-2.5 py-2 text-sm">
              <div className="font-semibold">{name}</div>
              {me.viewer.email ? <div className="text-xs text-muted">{me.viewer.email}</div> : null}
              {me.organization ? <div className="text-xs text-muted">{me.organization.domain}</div> : null}
            </div>
            <div className="my-1 h-px bg-line" />
            <form method="post" action="/logout">
              <input type="hidden" name="next" value="/" />
              <button type="submit" className="w-full rounded-md px-2.5 py-2 text-left text-sm hover:bg-white/8">
                Sign out
              </button>
            </form>
          </div>
        </details>
      ) : null}
    </header>
  )
}
