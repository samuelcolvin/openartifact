/** One message of the conversation: the user's text in a bubble, the assistant's parts in order. */

import type { UIMessage } from 'ai'
import { isToolUIPart } from 'ai'
import { ChevronRight } from 'lucide-react'
import { Streamdown } from 'streamdown'
import { ToolCard } from './ToolCard.tsx'

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

export function Message({ message }: { message: UIMessage }) {
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
  return (
    <div className="chat-prose">
      {message.parts.map((part, index) => {
        const key = `${message.id}-${index}`
        if (part.type === 'text') return <Streamdown key={key}>{part.text}</Streamdown>
        if (part.type === 'reasoning') return <Reasoning key={key} text={part.text} />
        if (isToolUIPart(part)) return <ToolCard key={part.toolCallId} part={part} />
        return null
      })}
    </div>
  )
}
