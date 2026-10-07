/** The editor: the chat on the left, the live preview on the right, access controls for the owner in the header. */

import { ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { type Artifact, api, findArtifact, type Me } from '../api.ts'
import { Chat } from '../chat/Chat.tsx'
import { Header } from '../components/Header.tsx'
import { Badge, Button, LinkButton, Spinner } from '../components/ui.tsx'
import { Preview } from '../Preview.tsx'
import { navigate } from '../router.ts'

type AccessChoice = 'private' | 'org' | 'org-editable' | 'public' | 'public-editable'

function accessOf(a: Artifact): AccessChoice {
  if (a.visibility === 'public') return a.org_editable ? 'public-editable' : 'public'
  if (a.visibility === 'org') return a.org_editable ? 'org-editable' : 'org'
  return 'private'
}

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
  const personal =
    artifact.visibility !== 'org' && artifact.org_editable === false && !artifact.forked_from && domain === null
  const choices: Array<[AccessChoice, string]> =
    artifact.visibility === 'private' || personal
      ? [
          ['private', 'Private: only me'],
          ['public', 'Public: anyone with the link'],
        ]
      : [
          ['org', `Visible to ${domain ?? 'the organisation'}`],
          ['org-editable', `Editable by ${domain ?? 'the organisation'}`],
          ['public', 'Public, read-only for others'],
          ['public-editable', `Public, editable by ${domain ?? 'the organisation'}`],
        ]
  const change = async (choice: AccessChoice) => {
    setBusy(true)
    try {
      onChange(
        await api.setAccess(artifact.id, {
          public: choice.startsWith('public'),
          org_editable: choice.endsWith('editable'),
        }),
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <select
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
    </select>
  )
}

export function Editor({ id }: { id: string }) {
  const [me, setMe] = useState<Me | null>(null)
  const [artifact, setArtifact] = useState<Artifact | null | undefined>(undefined)
  const [version, setVersion] = useState(1)
  const reload = useCallback(() => setVersion((v) => v + 1), [])

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
          <Chat artifactId={id} onChanged={reload} />
        </aside>
        <main className="min-h-0 flex-1">
          <Preview artifactId={id} version={version} />
        </main>
      </div>
    </div>
  )
}
