/**
 * One tool call in an assistant turn, folded by default: `run_code` shows the code it ran and the inputs it was
 * given, `web_search` (the model's own search, run by the provider) its query and the pages it found, every other
 * tool its output or error. The left border says how it went. `ProviderToolGroup` folds a run of provider-executed
 * calls (a web search and the scaffolding some providers run it through) into one card.
 */

import type { DynamicToolUIPart, ToolUIPart } from 'ai'
import { ChevronRight, Code2, Globe, Hammer, Wrench } from 'lucide-react'
import { useState } from 'react'

const ICONS = { run_code: Code2, build: Hammer, web_search: Globe }

const ACCENT: Record<AnyToolPart['state'], string> = {
  'input-streaming': 'border-l-accent',
  'input-available': 'border-l-accent',
  'approval-requested': 'border-l-warn',
  'approval-responded': 'border-l-warn',
  'output-available': 'border-l-ok',
  'output-error': 'border-l-danger',
  'output-denied': 'border-l-danger',
}

/** A tool call as the stream shows it: our MCP tools arrive as `tool-<name>`; `dynamic-tool` is handled the same. */
export type AnyToolPart = ToolUIPart | DynamicToolUIPart

function toolName(part: AnyToolPart): string {
  return part.type === 'dynamic-tool' ? part.toolName : part.type.replace(/^tool-/, '')
}

function summary(part: AnyToolPart, name: string): string {
  switch (part.state) {
    case 'input-streaming':
    case 'input-available':
      return name === 'web_search' ? 'searching' : 'running'
    case 'output-available':
      return name === 'web_search' ? 'searched' : 'done'
    case 'output-error':
      return 'failed'
    default:
      return part.state
  }
}

function asText(value: unknown): string {
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2) ?? ''
}

function RunCodeInput({ input }: { input: unknown }) {
  if (!input || typeof input !== 'object') return null
  const { code, inputs } = input as { code?: unknown; inputs?: unknown }
  const names = inputs && typeof inputs === 'object' ? Object.keys(inputs) : []
  return (
    <>
      {typeof code === 'string' ? <pre className="chat-code">{code}</pre> : null}
      {names.length > 0 ? (
        <p className="mt-1.5 text-[11px] text-faint">
          inputs:{' '}
          {names
            .map((name) => `${name} (${asText((inputs as Record<string, unknown>)[name]).length} chars)`)
            .join(', ')}
        </p>
      ) : null}
    </>
  )
}

/** The query a provider web search ran: `query`, or OpenAI's `queries` when there are several. */
function searchQuery(input: unknown): string {
  if (!input || typeof input !== 'object') return ''
  const { query, queries } = input as { query?: unknown; queries?: unknown }
  if (typeof query === 'string' && query) return query
  if (Array.isArray(queries)) return queries.filter((q): q is string => typeof q === 'string').join(' / ')
  return ''
}

/** The pages a provider web search returned, where the provider lists them (Anthropic does; OpenAI reports a status). */
function searchResults(output: unknown): Array<{ url: string; title: string }> {
  if (!Array.isArray(output)) return []
  return output.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const { url, title } = item as { url?: unknown; title?: unknown }
    return typeof url === 'string' ? [{ url, title: typeof title === 'string' && title ? title : url }] : []
  })
}

function WebSearchDetail({ part }: { part: AnyToolPart }) {
  const query = searchQuery(part.input)
  const results = part.state === 'output-available' ? searchResults(part.output) : []
  return (
    <div>
      {query ? <p className="text-fg">{query}</p> : null}
      {results.length > 0 ? (
        <ul className="mt-2 grid gap-1">
          {results.map((result) => (
            <li key={result.url} className="truncate">
              <a href={result.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                {result.title}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/** Whether the provider ran this call itself (a native tool such as web search), rather than our server. */
export function isProviderExecuted(part: AnyToolPart): boolean {
  return 'providerExecuted' in part && part.providerExecuted === true
}

/**
 * A run of consecutive provider-executed calls as one folded card: the searches it holds, with their queries and
 * results when opened. Anthropic runs its web search through `code_execution` calls, which would otherwise show as
 * a card each; those are counted but not listed.
 */
export function ProviderToolGroup({ parts }: { parts: AnyToolPart[] }) {
  const [open, setOpen] = useState(false)
  const searches = parts.filter((part) => toolName(part) === 'web_search')
  const last = parts[parts.length - 1]
  const running = parts.some((part) => part.state === 'input-streaming' || part.state === 'input-available')
  const failed = parts.some((part) => part.state === 'output-error')
  const accent = failed ? ACCENT['output-error'] : running ? ACCENT['input-available'] : ACCENT[last.state]
  const label = searches.length === 1 ? '1 search' : `${searches.length} searches`
  return (
    <div className={`my-1.5 rounded-md border border-line border-l-2 bg-bg/60 text-xs ${accent}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-muted hover:text-fg"
        aria-expanded={open}
      >
        <ChevronRight size={12} className={`transition ${open ? 'rotate-90' : ''}`} />
        <Globe size={13} />
        <span className="font-mono text-fg">web search</span>
        <span className={`ml-auto ${running ? 'animate-pulse' : ''}`}>{running ? 'searching' : label}</span>
      </button>
      {open ? (
        <div className="grid gap-3 border-t border-line px-2.5 py-2">
          {searches.map((part) => (
            <WebSearchDetail key={part.toolCallId} part={part} />
          ))}
          {searches.length === 0 ? <p className="text-faint">{parts.map(toolName).join(', ')}</p> : null}
        </div>
      ) : null}
    </div>
  )
}

export function ToolCard({ part }: { part: AnyToolPart }) {
  const [open, setOpen] = useState(false)
  const name = toolName(part)
  const Icon = (ICONS as Record<string, typeof Wrench>)[name] ?? Wrench
  const running = part.state === 'input-streaming' || part.state === 'input-available'
  return (
    <div className={`my-1.5 rounded-md border border-line border-l-2 bg-bg/60 text-xs ${ACCENT[part.state]}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-muted hover:text-fg"
        aria-expanded={open}
      >
        <ChevronRight size={12} className={`transition ${open ? 'rotate-90' : ''}`} />
        <Icon size={13} />
        <span className="font-mono text-fg">{name}</span>
        <span className={`ml-auto ${running ? 'animate-pulse' : ''}`}>{summary(part, name)}</span>
      </button>
      {open ? (
        <div className="border-t border-line px-2.5 py-2">
          {name === 'run_code' ? (
            <RunCodeInput input={part.input} />
          ) : name === 'web_search' ? (
            <WebSearchDetail part={part} />
          ) : (
            <pre className="chat-code">{asText(part.input)}</pre>
          )}
          {part.state === 'output-available' && name !== 'web_search' ? (
            <>
              <p className="mt-2 mb-1 text-[11px] uppercase tracking-wide text-faint">Output</p>
              <pre className="chat-code">{asText(part.output) || '(no output)'}</pre>
            </>
          ) : null}
          {part.state === 'output-error' ? (
            <>
              <p className="mt-2 mb-1 text-[11px] uppercase tracking-wide text-danger">Error</p>
              <pre className="chat-code text-danger">{part.errorText}</pre>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
