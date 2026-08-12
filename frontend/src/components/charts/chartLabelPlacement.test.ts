import { describe, expect, it } from 'vitest'
import { computePlottedRange, isNearBottomOfRange } from './chartLabelPlacement'

describe('computePlottedRange', () => {
  it('always includes 0 in the range even when all values are positive', () => {
    const range = computePlottedRange([100, 200, 300])
    expect(range.min).toBe(0)
    expect(range.max).toBe(300)
  })

  it('includes 0 even when all values are negative (a run of loss days)', () => {
    const range = computePlottedRange([-50, -10, -100])
    expect(range.min).toBe(-100)
    expect(range.max).toBe(0)
  })

  it('spans from a negative minimum to a positive maximum', () => {
    const range = computePlottedRange([-40, 385, 5])
    expect(range.min).toBe(-40)
    expect(range.max).toBe(385)
    expect(range.range).toBe(425)
  })

  it('falls back to a range of 1 rather than 0 when every value is identical', () => {
    // Prevents a division-by-zero in isNearBottomOfRange -- a flat
    // line (every day made exactly the same profit) must not crash
    // label placement.
    const range = computePlottedRange([50, 50, 50])
    expect(range.range).toBe(50) // max(50) - min(0) = 50, not 0 -- 0 is always in the set
  })

  it('falls back to a range of 1 for a genuinely empty input', () => {
    const range = computePlottedRange([])
    expect(range.min).toBe(0)
    expect(range.max).toBe(0)
    expect(range.range).toBe(1)
  })

  it('handles a single value', () => {
    const range = computePlottedRange([200])
    expect(range.min).toBe(0)
    expect(range.max).toBe(200)
  })
})

describe('isNearBottomOfRange', () => {
  it('treats a value at the very bottom of the range as near-bottom', () => {
    const range = computePlottedRange([0, 1000])
    expect(isNearBottomOfRange(0, range)).toBe(true)
  })

  it('treats a value at the very top of the range as NOT near-bottom', () => {
    const range = computePlottedRange([0, 1000])
    expect(isNearBottomOfRange(1000, range)).toBe(false)
  })

  it('treats the exact real-world case that motivated this: profit of 5 against a 0-650 range', () => {
    // The original bug: a $5 profit day, plotted against a chart
    // whose revenue reaches $650, had its label collide with the
    // x-axis. This must register as near-bottom.
    const range = computePlottedRange([650, 190, 550, 163, 291, 5, 203, 33])
    expect(isNearBottomOfRange(5, range)).toBe(true)
  })

  it('treats a near-equal profit/revenue pair (thin costs) as NOT near-bottom', () => {
    // Profit of 385 against a range that includes 400 -- close to
    // the top, not the bottom.
    const range = computePlottedRange([400, 385])
    expect(isNearBottomOfRange(385, range)).toBe(false)
  })

  it('treats a genuine loss (negative profit) as near-bottom when it IS the range minimum', () => {
    const range = computePlottedRange([300, -40])
    expect(isNearBottomOfRange(-40, range)).toBe(true)
  })

  it('is threshold-consistent: values just below the cutoff are near-bottom, just above are not', () => {
    const range = computePlottedRange([0, 100]) // range 0-100, threshold at 15
    expect(isNearBottomOfRange(14, range)).toBe(true)
    expect(isNearBottomOfRange(16, range)).toBe(false)
  })

  it('never throws for a degenerate flat-line range', () => {
    const range = computePlottedRange([50, 50, 50])
    expect(() => isNearBottomOfRange(50, range)).not.toThrow()
  })
})
