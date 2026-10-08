/**
 * The editing conversation for one artifact, over the Vercel AI SDK protocol to `/api/artifacts/<id>/chat`.
 *
 * The server owns the history: the browser sends only the new message each turn (`prepareSendMessagesRequest`),
 * the server runs the agent with the stored history, and `GET .../chat` restores the conversation on load. The
 * preview beside the chat is told to reload whenever a `build` call completes and when a turn ends.
 */

import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport, isToolUIPart, type UIMessage } from 'ai'
import { ArrowUp, Square, Trash2 } from 'lucide-react'
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import { api, type ModelChoice } from '../api.ts'
import { Button, Spinner } from '../components/ui.tsx'
import { Message } from './Message.tsx'

interface ChatProps {
  artifactId: string
  /** The page the preview shows, 1-based, sent with each message so the agent knows what "this slide" means. */
  page: number | null
  /** Called when the artifact may have changed: a `build` finished, or a turn ended. */
  onChanged: () => void
}

interface Loaded {
  messages: UIMessage[]
  model: string | null
  models: ModelChoice[]
  defaultModel: string | null
}

export function Chat(props: ChatProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    Promise.all([api.chat(props.artifactId), api.configure()])
      .then(([chat, configure]) =>
        setLoaded({
          messages: chat.messages as UIMessage[],
          model: chat.model,
          models: configure.models,
          defaultModel: configure.default,
        }),
      )
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [props.artifactId])

  if (error) return <p className="p-4 text-sm text-danger">{error}</p>
  if (!loaded) {
    return (
      <div className="p-4">
        <Spinner label="Loading conversation" />
      </div>
    )
  }
  return <Conversation {...props} loaded={loaded} />
}

function Conversation({ artifactId, page, onChanged, loaded }: ChatProps & { loaded: Loaded }) {
  const configured = loaded.models.some((m) => m.id === loaded.model) ? loaded.model : loaded.defaultModel
  const [model, setModel] = useState<string | null>(configured)
  // Refs, so the transport (created once) reads the current choice and page when a message is sent.
  const modelRef = useRef(model)
  modelRef.current = model
  const pageRef = useRef(page)
  pageRef.current = page
  const [draft, setDraft] = useState('')
  const bottom = useRef<HTMLDivElement>(null)

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `/api/artifacts/${artifactId}/chat`,
        credentials: 'same-origin',
        body: () => ({ model: modelRef.current, page: pageRef.current }),
        // Only the new message: the server holds the history and appends what it is sent.
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId, body }) => ({
          body: { id, trigger, messageId, messages: messages.slice(-1), ...body },
        }),
      }),
    [artifactId],
  )
  const { messages, sendMessage, status, stop, error, setMessages } = useChat({
    id: artifactId,
    transport,
    messages: loaded.messages,
    onFinish: () => onChanged(),
  })

  // Reload the preview as soon as a build has finished, before the model's closing words arrive.
  const builds = useRef(0)
  useEffect(() => {
    const done = messages
      .flatMap((m) => m.parts)
      .filter((p) => isToolUIPart(p) && p.type === 'tool-build' && p.state === 'output-available').length
    if (done > builds.current) onChanged()
    builds.current = done
  }, [messages, onChanged])

  // Keep the latest message in view as it streams: the effect runs whenever `messages` changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `messages` is the trigger, not a value used inside
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  const busy = status === 'submitted' || status === 'streaming'
  const send = () => {
    const text = draft.trim()
    if (!text || busy || !model) return
    setDraft('')
    void sendMessage({ text })
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }
  const clear = async () => {
    if (busy || messages.length === 0) return
    if (!window.confirm('Forget this conversation? The artifact itself is unchanged.')) return
    await api.clearChat(artifactId)
    setMessages([])
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="mt-8 text-center text-sm text-muted">
            <p className="font-medium text-fg">Ask for a change</p>
            <p className="mt-1">
              "Add a page about pricing", "make the headings blue", "turn the bullet list into a table".
            </p>
            <p className="mt-1">The agent edits the files, rebuilds, and the preview refreshes.</p>
          </div>
        ) : null}
        {messages.map((message) => (
          <Message key={message.id} message={message} artifactId={artifactId} />
        ))}
        {status === 'submitted' ? <Spinner label="Thinking" /> : null}
        {error ? (
          <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            {error.message}
          </p>
        ) : null}
        <div ref={bottom} />
      </div>
      <div className="border-t border-line p-3">
        <div className="rounded-xl border border-line bg-bg focus-within:border-accent">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            rows={3}
            placeholder={
              model
                ? 'Describe the change (Enter to send, Shift+Enter for a new line)'
                : 'No model is configured on this server'
            }
            disabled={!model}
            className="block w-full resize-none bg-transparent px-3 pt-3 text-sm outline-none placeholder:text-faint"
          />
          <div className="flex items-center gap-2 px-2 pb-2">
            <select
              value={model ?? ''}
              onChange={(e) => setModel(e.target.value || null)}
              disabled={busy || loaded.models.length === 0}
              className="h-7 max-w-[55%] rounded-md border border-line bg-panel px-1.5 text-xs text-muted outline-none"
              aria-label="Model"
            >
              {loaded.models.length === 0 ? <option value="">No models configured</option> : null}
              {loaded.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={clear}
              disabled={busy || messages.length === 0}
              title="Forget this conversation"
              className="ml-auto rounded-md p-1.5 text-muted hover:bg-white/8 hover:text-fg disabled:opacity-30"
            >
              <Trash2 size={14} />
            </button>
            {busy ? (
              <Button variant="ghost" onClick={() => stop()} className="h-7 px-2" title="Stop">
                <Square size={12} /> Stop
              </Button>
            ) : (
              <Button
                variant="primary"
                onClick={send}
                disabled={!draft.trim() || !model}
                className="h-7 w-7 px-0"
                title="Send"
              >
                <ArrowUp size={14} />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
