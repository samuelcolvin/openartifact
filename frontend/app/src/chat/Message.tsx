/**
 * One message of the conversation: the user's text in a bubble, the assistant's parts in order, with each run of
 * provider-executed tool calls (web searches) folded into one card.
 */

import type { UIMessage } from 'ai'
import { isToolUIPart } from 'ai'
import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Streamdown } from 'streamdown'
import { type AnyToolPart, isProviderExecuted, ProviderToolGroup, ToolCard } from './ToolCard.tsx'

function Reasoning({ text }: { text: string }) {
  if (!text.trim()) return null
  return (
    <details className="my-1.5 text-xs text-muted">
      <summary className="flex cursor-pointer list-none items-center gap-1 hover:text-fg">
        <ChevronRight size={12} />
        Reasoning
      </summary>
      <div className="mt-1 whitespace-pre-wrap border-l border-line pl-3 text-faint">{text}</div>
    </details>
  )
}

export function Message({ message, artifactId }: { message: UIMessage; artifactId: string }) {
  if (message.role === 'user') {
    const text = message.parts
      .filter((p): p is Extract<typeof p, { type: 'text' }> => p.type === 'text')
      .map((p) => p.text)
      .join('')
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent/15 px-3.5 py-2 text-sm">
          {text}
        </div>
      </div>
    )
  }
  const nodes: ReactNode[] = []
  let group: AnyToolPart[] = []
  const flush = () => {
    if (group.length > 0) nodes.push(<ProviderToolGroup key={group[0].toolCallId} parts={group} />)
    group = []
  }
  message.parts.forEach((part, index) => {
    const key = `${message.id}-${index}`
    if (isToolUIPart(part) && isProviderExecuted(part)) {
      group.push(part)
      return
    }
    // A step marker separates the model responses of one turn and renders nothing: it must not split a group.
    if (part.type === 'step-start') return
    flush()
    if (part.type === 'text') nodes.push(<Streamdown key={key}>{part.text}</Streamdown>)
    else if (part.type === 'reasoning') nodes.push(<Reasoning key={key} text={part.text} />)
    else if (isToolUIPart(part)) nodes.push(<ToolCard key={part.toolCallId} part={part} artifactId={artifactId} />)
  })
  flush()
  return <div className="chat-prose">{nodes}</div>
}
