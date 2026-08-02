/**
 * Renders a small, safe subset of markdown as real React elements --
 * bold text, bullet lists, numbered lists, and paragraphs. Built
 * specifically for AI assistant responses, which reliably use only
 * these few constructs. Never uses dangerouslySetInnerHTML, so
 * there's no injection risk regardless of what a provider returns.
 */
import type { ReactNode } from 'react'

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
    }
    return part ? <span key={`${keyPrefix}-${i}`}>{part}</span> : null
  })
}

export function MarkdownLite({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let listBuffer: string[] = []
  let listType: 'ul' | 'ol' | null = null

  function flushList() {
    if (listBuffer.length === 0) return
    const items = listBuffer.map((item, i) => (
      <li key={i}>{renderInline(item, `li-${blocks.length}-${i}`)}</li>
    ))
    blocks.push(
      listType === 'ol' ? (
        <ol key={blocks.length} className="ml-4 list-decimal space-y-0.5">
          {items}
        </ol>
      ) : (
        <ul key={blocks.length} className="ml-4 list-disc space-y-0.5">
          {items}
        </ul>
      ),
    )
    listBuffer = []
    listType = null
  }

  for (const rawLine of lines) {
    const line = rawLine.trim()
    const bulletMatch = /^[-*]\s+(.*)/.exec(line)
    const numberedMatch = /^\d+\.\s+(.*)/.exec(line)
    const headerMatch = /^#{1,6}\s+(.*)/.exec(line)

    if (bulletMatch) {
      if (listType !== 'ul') flushList()
      listType = 'ul'
      listBuffer.push(bulletMatch[1])
    } else if (numberedMatch) {
      if (listType !== 'ol') flushList()
      listType = 'ol'
      listBuffer.push(numberedMatch[1])
    } else if (headerMatch) {
      // A defensive backstop, not the primary fix -- the assistant is
      // instructed not to use headers at all, since this renderer
      // only ever turns them into bold text, never a real heading
      // size. This just guarantees a stray "#" never shows up as a
      // literal character on screen if one slips through anyway.
      flushList()
      blocks.push(
        <p key={blocks.length} className="mb-1 font-semibold last:mb-0">
          {renderInline(headerMatch[1], `h-${blocks.length}`)}
        </p>,
      )
    } else {
      flushList()
      if (line) {
        blocks.push(
          <p key={blocks.length} className="mb-1 last:mb-0">
            {renderInline(line, `p-${blocks.length}`)}
          </p>,
        )
      }
    }
  }
  flushList()

  return <div className="space-y-1">{blocks}</div>
}
