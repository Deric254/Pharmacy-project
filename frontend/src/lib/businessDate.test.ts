import { afterEach, describe, expect, it, vi } from 'vitest'
import { businessToday, startOfMonth, subtractDays } from './businessDate'

describe('businessToday', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns the calendar date in the business timezone, not the system timezone', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-26T23:30:00Z'))

    expect(businessToday('Africa/Nairobi')).toBe('2026-08-27')
    expect(businessToday('UTC')).toBe('2026-08-26')
  })

  it('agrees with the system date away from any boundary', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-26T12:00:00Z'))

    expect(businessToday('Africa/Nairobi')).toBe('2026-08-26')
  })
})

describe('subtractDays', () => {
  it('subtracts within a month', () => {
    expect(subtractDays('2026-08-27', 6)).toBe('2026-08-21')
  })

  it('crosses a month boundary', () => {
    expect(subtractDays('2026-08-02', 5)).toBe('2026-07-28')
  })

  it('crosses a year boundary', () => {
    expect(subtractDays('2026-01-02', 5)).toBe('2025-12-28')
  })

  it('is correct across a DST transition in a DST-observing zone', () => {
    expect(subtractDays('2026-03-10', 7)).toBe('2026-03-03')
  })
})

describe('startOfMonth', () => {
  it('returns the first of the month', () => {
    expect(startOfMonth('2026-08-27')).toBe('2026-08-01')
  })

  it('is idempotent on the first already', () => {
    expect(startOfMonth('2026-08-01')).toBe('2026-08-01')
  })
})
