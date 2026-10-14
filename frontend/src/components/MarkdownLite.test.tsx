import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MarkdownLite } from './MarkdownLite'

describe('MarkdownLite', () => {
  it('renders bold text and both kinds of list', () => {
    render(<MarkdownLite text={'Total **KES 500**\n- one\n- two\n1. first\n2. second'} />)

    expect(screen.getByText('KES 500').tagName).toBe('STRONG')
    expect(screen.getAllByRole('list')).toHaveLength(2)
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
  })

  it('renders a table with a header, body rows and right-aligned figures', () => {
    const text = [
      '| Product | Qty | Revenue |',
      '|---|---|---|',
      '| Amoxicillin | 12 | KES 1,200.50 |',
      '| Paracetamol | 40 | KES 800.00 |',
    ].join('\n')
    render(<MarkdownLite text={text} />)

    const table = screen.getByRole('table')
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((c) => c.textContent),
    ).toEqual(['Product', 'Qty', 'Revenue'])
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('Amoxicillin').closest('td')).toHaveClass('text-left')
    expect(screen.getByText('12').closest('td')).toHaveClass('text-right')
    expect(screen.getByText('KES 800.00').closest('td')).toHaveClass('text-right')
  })

  it('keeps bold working inside table cells and ends a table at the next paragraph', () => {
    render(<MarkdownLite text={'| A | B |\n|---|---|\n| **x** | y |\nAfter the table'} />)

    expect(screen.getByText('x').tagName).toBe('STRONG')
    expect(screen.getByText('After the table').closest('table')).toBeNull()
  })

  it('shows pipes that do not form a table exactly as written', () => {
    render(<MarkdownLite text={'| just | one | line |'} />)

    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('| just | one | line |')).toBeInTheDocument()
  })

  it('never interprets HTML in an answer', () => {
    const { container } = render(<MarkdownLite text={'<img src=x onerror=alert(1)> hi'} />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)> hi')
  })
})
