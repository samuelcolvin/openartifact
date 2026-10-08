/**
 * The editor: the chat on the left, the artifact on the right as the live preview or, with the toggle in the
 * bar above it, as a rich (MDXEditor) or raw (its source mode) editor of `main.md`; access controls for the owner
 * in the header.
 *
 * The bar over the document pane holds the View / Rich / Raw toggle, for a deck in view mode the slide counter and arrows (driven
 * through the controller the preview's page leaves on its window), and Save and Discard. The draft outlives the
 * toggle: switching between view, rich and raw never loses an edit, only Discard does. Save commits the draft and
 * rebuilds the page; a build error is shown and the draft kept. When the chat's agent changes the artifact the
 * source is fetched again unless a draft is in progress, in which case the draft wins.
 */

import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Code,
  ExternalLink,
  Eye,
  PencilLine,
  RefreshCw,
  Terminal,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DeckController, DeckPosition } from '../../../src/deck.ts'
import { type AccessChoice, type Artifact, accessFields, accessOf, api, findArtifact, type Me } from '../api.ts'
import { Chat } from '../chat/Chat.tsx'
import { Header } from '../components/Header.tsx'
import { McpDialog } from '../components/McpDialog.tsx'
import { Badge, Button, LinkButton, Select, Spinner } from '../components/ui.tsx'
import { type EditMode, MarkdownEditor } from '../editor/MarkdownEditor.tsx'
import { Preview } from '../Preview.tsx'
import { navigate } from '../router.ts'

/** The owner's control over who sees the artifact: the two personal choices, or all five when they are in an
 * organisation, since an artifact can move between their own space and the organisation's. */
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

type Mode = 'view' | EditMode

const MODES: Array<{ mode: Mode; label: string; icon: typeof Eye; hint: string }> = [
  { mode: 'view', label: 'View', icon: Eye, hint: 'The page as it is served' },
  { mode: 'rich', label: 'Rich', icon: PencilLine, hint: 'Edit the markdown as formatted text' },
  { mode: 'raw', label: 'Raw', icon: Code, hint: 'Edit the markdown as text' },
]

/** The view / rich / raw switch. */
function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div className="inline-flex h-7 items-center rounded-md border border-line bg-bg p-0.5">
      {MODES.map(({ mode: value, label, icon: Icon, hint }) => (
        <button
          key={value}
          type="button"
          aria-pressed={mode === value}
          title={hint}
          onClick={() => onChange(value)}
          className={`inline-flex h-6 items-center gap-1 rounded px-2 text-xs font-medium transition ${
            mode === value ? 'bg-raised text-fg' : 'text-muted hover:text-fg'
          }`}
        >
          <Icon size={13} /> {label}
        </button>
      ))}
    </div>
  )
}

const ARROW =
  'inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-white/8 hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent'

/** Previous / next and the counter, kept in step with the preview's deck. */
function DeckNav({ deck }: { deck: DeckController }) {
  const [position, setPosition] = useState<DeckPosition>(() => deck.position())
  useEffect(() => {
    setPosition(deck.position())
    // The controller dies with its iframe, so the listener needs no removal.
    deck.onChange(setPosition)
  }, [deck])
  return (
    <div className="inline-flex items-center gap-1 font-mono text-xs text-muted">
      <button
        type="button"
        className={ARROW}
        onClick={() => deck.go(-1)}
        disabled={position.atStart}
        title="Previous (Left; Shift+Left for a whole page)"
      >
        <ChevronLeft size={16} />
      </button>
      <span className="min-w-[4.5rem] text-center tabular-nums">
        {position.index + 1} / {position.total}
      </span>
      <button
        type="button"
        className={ARROW}
        onClick={() => deck.go(1)}
        disabled={position.atEnd}
        title="Next (Right or Space)"
      >
        <ChevronRight size={16} />
      </button>
    </div>
  )
}

/** A line above the pane: a build or parse error, or a note, with a dismiss. */
function Notice({ tone, children, onClose }: { tone: 'danger' | 'muted'; children: string; onClose: () => void }) {
  const colour = tone === 'danger' ? 'border-danger/40 bg-danger/10 text-danger' : 'border-line bg-panel text-muted'
  return (
    <div className={`flex items-start gap-3 border-b px-4 py-2 text-xs ${colour}`}>
      <pre className="min-w-0 flex-1 whitespace-pre-wrap font-mono">{children}</pre>
      <button type="button" onClick={onClose} className="shrink-0 underline opacity-80 hover:opacity-100">
        dismiss
      </button>
    </div>
  )
}

type NoticeState = { tone: 'danger' | 'muted'; text: string } | null

/**
 * The artifact's source and the draft over it. `source` is what the server holds, `draft` what the editor holds
 * (null while clean), and `generation` remounts the editor whenever the text it should start from changes (a
 * discard, or a fetch after the agent edited).
 */
function useSource(artifact: Artifact, mode: Mode, version: number, onSaved: () => void) {
  const [source, setSource] = useState<string | null>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const [generation, setGeneration] = useState(0)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<NoticeState>(null)
  const dirty = draft !== null && draft !== source
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty

  const fetchSource = useCallback(async () => {
    try {
      const { content } = await api.source(artifact.id)
      setSource(content)
      setDraft(null)
      setGeneration((g) => g + 1)
    } catch (err) {
      setNotice({ tone: 'danger', text: err instanceof Error ? err.message : String(err) })
    }
  }, [artifact.id])

  // Fetched the first time an edit mode opens.
  useEffect(() => {
    if (mode !== 'view' && source === null) void fetchSource()
  }, [mode, source, fetchSource])

  // Fetched again when the preview reloads because the artifact may have changed (the agent edited it), unless a
  // draft is in progress or the reload is our own save, which the editor already shows.
  const seenVersion = useRef(version)
  const justSaved = useRef(false)
  useEffect(() => {
    if (seenVersion.current === version) return
    seenVersion.current = version
    if (justSaved.current) {
      justSaved.current = false
      return
    }
    if (source !== null && !dirtyRef.current) void fetchSource()
  }, [version, source, fetchSource])

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const save = async () => {
    if (draft === null || saving) return
    setSaving(true)
    try {
      // MDXEditor drops the final newline; a text file ends with one.
      const content = draft.endsWith('\n') ? draft : `${draft}\n`
      const { build_error } = await api.saveSource(artifact.id, content)
      setSource(content)
      setDraft(null)
      setNotice(build_error ? { tone: 'danger', text: build_error } : null)
      justSaved.current = true
      onSaved()
    } catch (err) {
      setNotice({ tone: 'danger', text: err instanceof Error ? err.message : String(err) })
    } finally {
      setSaving(false)
    }
  }
  const discard = () => {
    if (!dirty || !window.confirm('Discard your unsaved changes?')) return
    setDraft(null)
    setGeneration((g) => g + 1)
  }
  return { source, draft, dirty, generation, saving, notice, setNotice, setDraft, save, discard }
}

/** The two panes, for an artifact the viewer may change; the document pane carries its own bar. */
function Workbench({
  artifact,
  version,
  reload,
  page,
}: {
  artifact: Artifact
  /** Bumped to reload the preview; `reload` bumps it and refetches the artifact. */
  version: number
  reload: () => void
  /** The page the preview shows, 1-based, as the runtime inside it reports. */
  page: number | null
}) {
  const [mode, setMode] = useState<Mode>('view')
  const [deck, setDeck] = useState<DeckController | null>(null)
  const state = useSource(artifact, mode, version, reload)
  const editing = mode !== 'view'
  const status = state.dirty ? 'Unsaved changes to main.md' : editing ? 'Editing main.md' : null
  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <aside className="flex h-[45vh] w-full shrink-0 flex-col border-b border-line bg-panel md:h-auto md:w-[420px] md:border-r md:border-b-0">
        <Chat artifactId={artifact.id} page={page} onChanged={reload} />
      </aside>
      <main className="flex min-h-0 flex-1 flex-col">
        <div className="flex h-10 shrink-0 items-center gap-3 border-b border-line bg-panel px-3">
          <ModeToggle mode={mode} onChange={setMode} />
          <Button onClick={reload} className="h-7 px-2" title="Reload the preview" disabled={editing}>
            <RefreshCw size={14} />
          </Button>
          <span className="flex-1" />
          {!editing && deck ? <DeckNav deck={deck} /> : null}
          <span className="flex-1" />
          <span className="truncate text-xs text-muted">{status}</span>
          {state.dirty ? (
            <>
              <Button onClick={state.discard} className="h-7 px-2 text-xs" disabled={state.saving}>
                Discard
              </Button>
              <Button
                variant="primary"
                onClick={() => void state.save()}
                className="h-7 px-2.5 text-xs"
                disabled={state.saving}
              >
                {state.saving ? 'Saving' : 'Save'}
              </Button>
            </>
          ) : null}
        </div>
        {state.notice ? (
          <Notice tone={state.notice.tone} onClose={() => state.setNotice(null)}>
            {state.notice.text}
          </Notice>
        ) : null}
        <div className="min-h-0 flex-1">
          {!editing ? (
            <Preview artifactId={artifact.id} version={version} page={page} onDeck={setDeck} />
          ) : state.source === null ? (
            <div className="p-4">
              <Spinner label="Loading main.md" />
            </div>
          ) : (
            <MarkdownEditor
              key={state.generation}
              artifactId={artifact.id}
              markdown={state.draft ?? state.source}
              mode={mode}
              onChange={state.setDraft}
              onParseError={(error) =>
                state.setNotice({ tone: 'danger', text: `Rich mode cannot show this markdown: ${error}` })
              }
            />
          )}
        </div>
      </main>
    </div>
  )
}

export function Editor({ id }: { id: string }) {
  const [me, setMe] = useState<Me | null>(null)
  const [artifact, setArtifact] = useState<Artifact | null | undefined>(undefined)
  const [version, setVersion] = useState(1)
  const [promptOpen, setPromptOpen] = useState(false)
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
        <Button
          onClick={() => setPromptOpen(true)}
          className="h-7"
          title="A prompt that puts your coding agent to work on this artifact over MCP"
        >
          <Terminal size={14} /> Prompt
        </Button>
        <LinkButton href={artifact.url} target="_blank" rel="noopener" className="h-7">
          <ExternalLink size={14} /> Open page
        </LinkButton>
      </Header>
      <McpDialog open={promptOpen} onClose={() => setPromptOpen(false)} artifact={artifact} />
      <Workbench artifact={artifact} version={version} reload={reload} page={page} />
    </div>
  )
}
