/**
 * One tool call in an assistant turn, folded by default: `run_code` shows the code it ran and the inputs it was
 * given, every tool shows its output or error. The left border says how it went.
 */

import type { DynamicToolUIPart, ToolUIPart } from 'ai'
import { ChevronRight, Code2, Hammer, Wrench } from 'lucide-react'
import { useState } from 'react'

const ICONS = { run_code: Code2, build: Hammer }

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

function summary(part: AnyToolPart): string {
  switch (part.state) {
    case 'input-streaming':
    case 'input-available':
      return 'running'
    case 'output-available':
      return 'done'
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
        <span className={`ml-auto ${running ? 'animate-pulse' : ''}`}>{summary(part)}</span>
      </button>
      {open ? (
        <div className="border-t border-line px-2.5 py-2">
          {name === 'run_code' ? (
            <RunCodeInput input={part.input} />
          ) : (
            <pre className="chat-code">{asText(part.input)}</pre>
          )}
          {part.state === 'output-available' ? (
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
