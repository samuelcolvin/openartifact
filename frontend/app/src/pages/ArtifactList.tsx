/** The home page: the viewer's artifacts and the ones their organisation shares, and the New artifact dialog. */

import { ExternalLink, FileText, Pencil, Plus, Presentation, ScrollText } from 'lucide-react'
import { type FormEvent, type ReactNode, useEffect, useState } from 'react'
import {
  type Artifact,
  type ArtifactType,
  api,
  type ArtifactList as Listing,
  type Me,
  type NewArtifact,
  type Theme,
} from '../api.ts'
import { Header } from '../components/Header.tsx'
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  Field,
  INPUT,
  LinkButton,
  RadioCards,
  Select,
  Spinner,
} from '../components/ui.tsx'
import { navigate } from '../router.ts'

const TYPES: Array<{ value: ArtifactType; label: string; hint: string; icon: ReactNode }> = [
  { value: 'deck', label: 'Deck', hint: 'Slides, one 16:9 page at a time', icon: <Presentation size={18} /> },
  { value: 'document', label: 'Document', hint: 'A4 sheets, one per page', icon: <FileText size={18} /> },
  { value: 'page', label: 'Page', hint: 'One continuous web page', icon: <ScrollText size={18} /> },
]

/** A tiny rendering of a theme's palette: its page colour with a heading in its text colour. */
function Swatch({ dark, markdown }: { dark: boolean; markdown: boolean }) {
  return (
    <span
      aria-hidden
      className="flex h-7 w-10 items-center justify-center rounded border text-[11px] font-semibold leading-none"
      style={
        dark
          ? { background: '#1c2026', color: '#f2f2f2', borderColor: 'rgba(255, 255, 255, 0.22)' }
          : { background: '#ffffff', color: '#1a1d21', borderColor: 'rgba(255, 255, 255, 0.09)' }
      }
    >
      {markdown ? <span className="font-mono"># Aa</span> : 'Aa'}
    </span>
  )
}

const THEMES: Array<{ value: Theme; label: string; icon: ReactNode }> = [
  { value: 'light', label: 'Light', icon: <Swatch dark={false} markdown={false} /> },
  { value: 'dark', label: 'Dark', icon: <Swatch dark markdown={false} /> },
  { value: 'markdown-light', label: 'Markdown light', icon: <Swatch dark={false} markdown /> },
  { value: 'markdown-dark', label: 'Markdown dark', icon: <Swatch dark markdown /> },
]

function since(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

function Card({ artifact, domain }: { artifact: Artifact; domain: string | null }) {
  return (
    <li className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
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
      </div>
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
        <LinkButton href={artifact.url} target="_blank" rel="noopener">
          <ExternalLink size={14} /> Open
        </LinkButton>
      </div>
    </li>
  )
}

function NewArtifactDialog({ me, open, onClose }: { me: Me; open: boolean; onClose: () => void }) {
  const hasOrg = me.organization !== null
  const [form, setForm] = useState<NewArtifact>({
    title: '',
    type: 'deck',
    theme: 'light',
    placement: hasOrg ? 'org' : 'personal',
    public: false,
    org_editable: false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const created = await api.create(form)
      navigate(`/edit/${created.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="New artifact">
      <form onSubmit={submit} className="grid gap-4">
        <Field id="new-title" label="Title">
          <input
            id="new-title"
            className={INPUT}
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="Quarterly review"
            autoFocus
            required
          />
        </Field>
        <RadioCards
          label="Type"
          name="new-type"
          columns={3}
          value={form.type}
          onChange={(type) => setForm({ ...form, type })}
          options={TYPES}
        />
        <RadioCards
          label="Theme"
          name="new-theme"
          columns={4}
          value={form.theme}
          onChange={(theme) => setForm({ ...form, theme })}
          options={THEMES}
        />
        <fieldset className="grid gap-2 rounded-lg border border-line p-3">
          <legend className="px-1 text-xs text-muted">Who can see it</legend>
          {hasOrg ? (
            <Select
              value={form.placement}
              onChange={(e) =>
                setForm({ ...form, placement: e.target.value as 'personal' | 'org', org_editable: false })
              }
            >
              <option value="org">Everyone at {me.organization?.domain}</option>
              <option value="personal">Only me</option>
            </Select>
          ) : (
            <p className="text-sm text-muted">
              Only you, unless public. Sign in with a Google Workspace account to share with an organisation.
            </p>
          )}
          {form.placement === 'org' ? (
            <Checkbox
              label="Everyone in the organisation can edit it"
              checked={form.org_editable}
              onChange={(v) => setForm({ ...form, org_editable: v })}
            />
          ) : null}
          <Checkbox
            label="Public: anyone with the link can see it"
            checked={form.public}
            onChange={(v) => setForm({ ...form, public: v })}
          />
        </fieldset>
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy || !form.title.trim()}>
            {busy ? 'Creating' : 'Create'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

export function ArtifactList() {
  const [me, setMe] = useState<Me | null>(null)
  const [listing, setListing] = useState<Listing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

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
            <section className="mb-8">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="font-semibold">Mine</h2>
                {me ? (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    <Plus size={14} /> New artifact
                  </Button>
                ) : null}
              </div>
              {listing.mine.length === 0 ? (
                <p className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">
                  Nothing yet. Create an artifact here, or from an agent over MCP.
                </p>
              ) : (
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {listing.mine.map((a) => (
                    <Card key={a.id} artifact={a} domain={domain} />
                  ))}
                </ul>
              )}
            </section>
            {listing.shared.length > 0 ? (
              <section>
                <h2 className="mb-3 font-semibold">Shared with you{domain ? ` at ${domain}` : ''}</h2>
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {listing.shared.map((a) => (
                    <Card key={a.id} artifact={a} domain={domain} />
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        )}
      </main>
      {me ? <NewArtifactDialog me={me} open={creating} onClose={() => setCreating(false)} /> : null}
    </div>
  )
}
