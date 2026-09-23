import { describe, expect, it } from 'vitest'
import { formatMoney } from './currency'

describe('formatMoney', () => {
  it('formats a whole number in USD', () => {
    expect(formatMoney(10, 'USD')).toBe('$10.00')
  })

  it('formats a fractional amount in USD', () => {
    expect(formatMoney(9.5, 'USD')).toBe('$9.50')
  })

  it('formats zero correctly', () => {
    expect(formatMoney(0, 'USD')).toBe('$0.00')
  })

  it('formats a real-world Kenyan Shilling amount', () => {
    const result = formatMoney(1553.68, 'KES')
    expect(result).toContain('1,553.68')
  })

  it('formats a negative amount (a refund) without losing the sign', () => {
    const result = formatMoney(-25.5, 'USD')
    expect(result).toMatch(/-|\(.*\)/) 
    expect(result).toContain('25.50')
  })

  it('formats a very large amount without switching to scientific notation', () => {
    const result = formatMoney(1_000_000_000, 'USD')
    expect(result).not.toMatch(/e\+/i)
    expect(result).toContain('1,000,000,000')
  })

  it('rounds to exactly 2 decimal places for a currency with sub-cent floating point drift', () => {
    const result = formatMoney(10.005, 'USD')
    expect(result).toMatch(/^\$10\.0[01]$/)
  })

  it('falls back to a plain "CODE amount" string for an unrecognized currency code', () => {
    expect(formatMoney(10, 'NOTACURRENCY')).toBe('NOTACURRENCY 10.00')
  })

  it('falls back gracefully for an empty currency string', () => {
    expect(() => formatMoney(10, '')).not.toThrow()
  })

  it('falls back gracefully for a lowercase currency code Intl rejects', () => {
    const result = formatMoney(10, 'notreal')
    expect(result).toContain('10.00')
  })

  it('handles NaN without throwing (defensive -- must never crash a receipt)', () => {
    expect(() => formatMoney(NaN, 'USD')).not.toThrow()
  })

  it('handles Infinity without throwing', () => {
    expect(() => formatMoney(Infinity, 'USD')).not.toThrow()
  })
})
