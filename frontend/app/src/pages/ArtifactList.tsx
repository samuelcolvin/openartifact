/** The home page: three ways to start an artifact, the viewer's own, and the ones their organisation shares. */

import { Eye, FileText, Pencil, Presentation, ScrollText } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import { type Artifact, type ArtifactType, api, type ArtifactList as Listing, type Me } from '../api.ts'
import { Header } from '../components/Header.tsx'
import { Badge, Button, LinkButton, Spinner } from '../components/ui.tsx'
import { navigate } from '../router.ts'

const TYPES: Array<{ value: ArtifactType; label: string; hint: string; icon: ReactNode }> = [
  { value: 'deck', label: 'Deck', hint: 'Slides, one 16:9 page at a time', icon: <Presentation size={18} /> },
  { value: 'document', label: 'Document', hint: 'A4 sheets, one per page', icon: <FileText size={18} /> },
  { value: 'page', label: 'Page', hint: 'One continuous web page', icon: <ScrollText size={18} /> },
]

function since(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

/** One artifact, a full-width row: title and details on the left, the badge and the actions on the right. */
function Card({ artifact, domain }: { artifact: Artifact; domain: string | null }) {
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-panel px-4 py-3">
      <div className="min-w-0 flex-1">
        <h3 className="truncate font-semibold">{artifact.title}</h3>
        <p className="text-xs text-muted">
          {artifact.type} · updated {since(artifact.updated_at)}
          {artifact.owner_email && !artifact.can_manage ? ` · ${artifact.owner_email}` : ''}
        </p>
      </div>
      <Badge
        visibility={artifact.visibility}
        orgEditable={artifact.org_editable}
        domain={artifact.visibility === 'org' ? domain : null}
      />
      <div className="flex gap-2">
        {artifact.can_edit ? (
          <Button
            variant="primary"
            onClick={() => navigate(`/edit/${artifact.id}`)}
            title="Open the editor: chat with the agent and watch the page update"
          >
            <Pencil size={14} /> Edit
          </Button>
        ) : null}
        <LinkButton href={artifact.url} target="_blank" rel="noopener" title="Open the page in a new tab">
          <Eye size={14} /> View
        </LinkButton>
      </div>
    </li>
  )
}

/** The three ways to start: one click creates an artifact of that type with a placeholder title, a theme that
 * suits it and a page or two to replace, and opens the editor; the chat's first turn names it. */
function Starters() {
  const [busy, setBusy] = useState<ArtifactType | null>(null)
  const [error, setError] = useState<string | null>(null)
  const start = async (type: ArtifactType) => {
    setBusy(type)
    setError(null)
    try {
      const created = await api.create({ type })
      navigate(`/edit/${created.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }
  return (
    <section className="mb-8">
      <h2 className="mb-3 font-semibold">New</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        {TYPES.map((option) => (
          <button
            key={option.value}
            type="button"
            disabled={busy !== null}
            onClick={() => void start(option.value)}
            className="grid gap-1.5 rounded-xl border border-line bg-panel p-4 text-left hover:border-faint disabled:opacity-60"
          >
            <span className="flex items-center gap-2 font-semibold">
              <span className="text-accent">{option.icon}</span>
              {option.label}
              {busy === option.value ? <Spinner /> : null}
            </span>
            <span className="text-xs text-muted">{option.hint}</span>
          </button>
        ))}
      </div>
      {error ? <p className="mt-2 text-sm text-danger">{error}</p> : null}
    </section>
  )
}

export function ArtifactList() {
  const [me, setMe] = useState<Me | null>(null)
  const [listing, setListing] = useState<Listing | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.me(), api.artifacts()])
      .then(([me, listing]) => {
        setMe(me)
        setListing(listing)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  const domain = me?.organization?.domain ?? null
  return (
    <div className="flex h-full flex-col">
      <Header me={me}>
        <h1 className="text-sm text-muted">Your artifacts</h1>
      </Header>
      <main className="mx-auto w-full max-w-5xl flex-1 overflow-y-auto px-4 py-6">
        {error ? <p className="text-danger">{error}</p> : null}
        {!listing ? (
          <Spinner />
        ) : (
          <>
            {me ? <Starters /> : null}
            <section className="mb-8">
              <h2 className="mb-3 font-semibold">Mine</h2>
              {listing.mine.length === 0 ? (
                <p className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">
                  Nothing yet. Start one above, or from an agent over MCP.
                </p>
              ) : (
                <ul className="grid gap-3">
                  {listing.mine.map((a) => (
                    <Card key={a.id} artifact={a} domain={domain} />
                  ))}
                </ul>
              )}
            </section>
            {listing.shared.length > 0 ? (
              <section>
                <h2 className="mb-3 font-semibold">Shared with you{domain ? ` at ${domain}` : ''}</h2>
                <ul className="grid gap-3">
                  {listing.shared.map((a) => (
                    <Card key={a.id} artifact={a} domain={domain} />
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        )}
      </main>
    </div>
  )
}
