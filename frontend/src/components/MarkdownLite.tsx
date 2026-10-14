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

// Models often drop the outer pipes ("a | b | c", "---|---|---"), so they are
// optional here.
function splitRow(line: string): string[] {
  const inner = line.replace(/^\|/, '').replace(/\|$/, '')
  return inner.split('|').map((cell) => cell.trim())
}

const NUMERIC_CELL = /^-?[A-Za-z]{0,3}\s?-?[\d,]+(\.\d+)?%?$/

export function MarkdownLite({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let listBuffer: string[] = []
  let listType: 'ul' | 'ol' | null = null
  let tableBuffer: string[] = []

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

  function flushTable() {
    if (tableBuffer.length === 0) return
    const rows = tableBuffer.map(splitRow)
    const isTable = rows.length >= 2 && rows[1].every((cell) => /^:?-{2,}:?$/.test(cell))
    if (isTable) {
      const [header, , ...body] = rows
      const numeric = header.map(
        (_, col) => body.length > 0 && body.every((row) => NUMERIC_CELL.test(row[col] ?? '')),
      )
      const align = (col: number) => (numeric[col] ? 'text-right figure' : 'text-left')
      blocks.push(
        <div key={blocks.length} className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                {header.map((cell, col) => (
                  <th
                    key={col}
                    className={`border border-rule bg-brass-soft/30 px-2 py-1 font-medium text-ink ${align(col)}`}
                  >
                    {renderInline(cell, `th-${blocks.length}-${col}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>
                  {header.map((_, col) => (
                    <td key={col} className={`border border-rule px-2 py-1 ${align(col)}`}>
                      {renderInline(row[col] ?? '', `td-${blocks.length}-${r}-${col}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
    } else {
      for (const line of tableBuffer) {
        blocks.push(
          <p key={blocks.length} className="mb-1 last:mb-0">
            {renderInline(line, `p-${blocks.length}`)}
          </p>,
        )
      }
    }
    tableBuffer = []
  }

  for (const rawLine of lines) {
    const line = rawLine.trim()
    const bulletMatch = /^[-*]\s+(.*)/.exec(line)
    const numberedMatch = /^\d+\.\s+(.*)/.exec(line)
    const isTableRow =
      line.length > 1 &&
      line.includes('|') &&
      (line.startsWith('|') || !(bulletMatch || numberedMatch))
    if (!isTableRow) flushTable()
    const headerMatch = /^#{1,6}\s+(.*)/.exec(line)

    if (isTableRow) {
      flushList()
      tableBuffer.push(line)
    } else if (bulletMatch) {
      if (listType !== 'ul') flushList()
      listType = 'ul'
      listBuffer.push(bulletMatch[1])
    } else if (numberedMatch) {
      if (listType !== 'ol') flushList()
      listType = 'ol'
      listBuffer.push(numberedMatch[1])
    } else if (headerMatch) {
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
  flushTable()

  return <div className="space-y-1">{blocks}</div>
}
