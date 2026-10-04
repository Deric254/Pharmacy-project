import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const GRACE_MS = 15_000

function fontLinks(): string[] {
  return Array.from(document.head.querySelectorAll('link[rel="stylesheet"]')).map(
    (l) => (l as HTMLLinkElement).href,
  )
}

// themes.ts keeps its "startup settled" state at module level, so every test
// gets a fresh copy of it.
async function freshThemes() {
  vi.resetModules()
  return import('./themes')
}

describe('theme fonts', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    document.head.innerHTML = ''
    Object.defineProperty(document, 'readyState', { configurable: true, get: () => 'complete' })
  })

  afterEach(() => {
    vi.useRealTimers()
    Reflect.deleteProperty(document, 'readyState')
  })

  it('applies the theme at once but does not fetch fonts during startup', async () => {
    const { applyTheme } = await freshThemes()
    applyTheme('ledger')

    expect(document.documentElement.dataset.theme).toBe('ledger')
    expect(fontLinks()).toHaveLength(0)

    vi.advanceTimersByTime(GRACE_MS - 1)
    expect(fontLinks()).toHaveLength(0)
  })

  it('fetches the fonts once startup has settled', async () => {
    const { applyTheme } = await freshThemes()
    applyTheme('ledger')

    vi.advanceTimersByTime(GRACE_MS)
    const links = fontLinks()
    expect(links.length).toBeGreaterThan(0)
    expect(links.every((href) => href.startsWith('https://fonts.googleapis.com/'))).toBe(true)
  })

  it('loads fonts for a theme changed during startup in the same single release', async () => {
    const { applyTheme } = await freshThemes()
    applyTheme('ledger')
    applyTheme('clinical')
    expect(fontLinks()).toHaveLength(0)

    vi.advanceTimersByTime(GRACE_MS)
    expect(fontLinks().length).toBeGreaterThanOrEqual(2)
  })

  it('loads fonts immediately for a theme changed after startup has settled', async () => {
    const { applyTheme, THEMES } = await freshThemes()
    applyTheme('ledger')
    vi.advanceTimersByTime(GRACE_MS)
    const before = fontLinks().length

    const other = Object.keys(THEMES).find((n) => n !== 'ledger')!
    applyTheme(other)
    expect(fontLinks().length).toBeGreaterThan(before)
  })

  it('never adds the same font twice', async () => {
    const { applyTheme } = await freshThemes()
    applyTheme('ledger')
    applyTheme('ledger')
    vi.advanceTimersByTime(GRACE_MS)
    applyTheme('ledger')

    const links = fontLinks()
    expect(new Set(links).size).toBe(links.length)
  })
})
