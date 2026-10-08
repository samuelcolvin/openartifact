/** The editor: the chat on the left, the live preview on the right, access controls for the owner in the header. */

import { ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { type AccessChoice, type Artifact, accessFields, accessOf, api, findArtifact, type Me } from '../api.ts'
import { Chat } from '../chat/Chat.tsx'
import { Header } from '../components/Header.tsx'
import { Badge, Button, LinkButton, Select, Spinner } from '../components/ui.tsx'
import { Preview } from '../Preview.tsx'
import { navigate } from '../router.ts'

/** The owner's control over who sees the artifact: the two personal choices, or all five when they are in an
 * organisation, since an artifact can move between their own space and the organisation's. */
function AccessSelect({
  artifact,
  domain,
  onChange,
}: {
  artifact: Artifact
  domain: string | null
  onChange: (a: Artifact) => void
}) {
  const [busy, setBusy] = useState(false)
  const choices: Array<[AccessChoice, string]> =
    domain === null
      ? [
          ['private', 'Only me'],
          ['public', 'Anyone with the link'],
        ]
      : [
          ['private', 'Only me'],
          ['org', `Everyone at ${domain}`],
          ['org-editable', `Everyone at ${domain} can edit`],
          ['public', `Anyone with the link, listed for ${domain}`],
          ['public-editable', `Anyone with the link, ${domain} can edit`],
        ]
  const change = async (choice: AccessChoice) => {
    setBusy(true)
    try {
      onChange(await api.setAccess(artifact.id, accessFields(choice)))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Select
      value={accessOf(artifact)}
      onChange={(e) => void change(e.target.value as AccessChoice)}
      disabled={busy}
      className="h-7 rounded-md border border-line bg-panel px-1.5 text-xs text-muted outline-none"
      aria-label="Who can see this artifact"
    >
      {choices.map(([value, label]) => (
        <option key={value} value={value}>
          {label}
        </option>
      ))}
    </Select>
  )
}

export function Editor({ id }: { id: string }) {
  const [me, setMe] = useState<Me | null>(null)
  const [artifact, setArtifact] = useState<Artifact | null | undefined>(undefined)
  const [version, setVersion] = useState(1)
  // After a turn the artifact may have changed beyond its page: the first turn names it.
  const reload = useCallback(() => {
    setVersion((v) => v + 1)
    findArtifact(id).then(
      (found) => found && setArtifact(found),
      () => undefined,
    )
  }, [id])
  // Which page the preview shows, reported by the runtime inside the iframe (see `reportPosition` in main.ts).
  const [page, setPage] = useState<number | null>(null)
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return
      const data: unknown = event.data
      if (data && typeof data === 'object' && (data as { type?: unknown }).type === 'openartifact:page') {
        const reported = (data as { page?: unknown }).page
        if (typeof reported === 'number' && reported >= 1) setPage(reported)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  useEffect(() => {
    api.me().then(setMe, () => setMe(null))
    findArtifact(id).then(setArtifact, () => setArtifact(null))
  }, [id])

  if (artifact === undefined) {
    return (
      <div className="flex h-full flex-col">
        <Header me={me} />
        <div className="p-6">
          <Spinner />
        </div>
      </div>
    )
  }
  if (artifact === null || !artifact.can_edit) {
    return (
      <div className="flex h-full flex-col">
        <Header me={me} />
        <div className="p-6 text-sm text-muted">
          {artifact === null
            ? 'This artifact does not exist or is not shared with you.'
            : 'This artifact is shared with you read-only.'}{' '}
          <button type="button" className="text-accent underline" onClick={() => navigate('/')}>
            Back to your artifacts
          </button>
        </div>
      </div>
    )
  }
  const domain = me?.organization?.domain ?? null
  return (
    <div className="flex h-full flex-col">
      <Header me={me}>
        <Button onClick={() => navigate('/')} className="h-7 px-2" title="Back to your artifacts">
          <ArrowLeft size={14} />
        </Button>
        <h1 className="truncate font-semibold">{artifact.title}</h1>
        <Badge
          visibility={artifact.visibility}
          orgEditable={artifact.org_editable}
          domain={artifact.visibility === 'org' ? domain : null}
        />
        {artifact.can_manage ? <AccessSelect artifact={artifact} domain={domain} onChange={setArtifact} /> : null}
        <span className="flex-1" />
        <Button onClick={reload} className="h-7 px-2" title="Reload the preview">
          <RefreshCw size={14} />
        </Button>
        <LinkButton href={artifact.url} target="_blank" rel="noopener" className="h-7">
          <ExternalLink size={14} /> Open page
        </LinkButton>
      </Header>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="flex h-[45vh] w-full shrink-0 flex-col border-b border-line bg-panel md:h-auto md:w-[420px] md:border-r md:border-b-0">
          <Chat artifactId={id} page={page} onChanged={reload} />
        </aside>
        <main className="min-h-0 flex-1">
          <Preview artifactId={id} version={version} page={page} />
        </main>
      </div>
    </div>
  )
}
