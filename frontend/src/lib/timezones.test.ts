import { describe, expect, it } from 'vitest'
import { TIMEZONE_GROUPS, timezoneLabel } from './timezones'

describe('TIMEZONE_GROUPS', () => {
  it('has no duplicate IANA timezone values across the whole curated list', () => {
    const allTimezones = TIMEZONE_GROUPS.flatMap((group) =>
      group.options.map((opt) => opt.timezone),
    )
    expect(new Set(allTimezones).size).toBe(allTimezones.length)
  })

  it("includes this app's actual default timezone", () => {
    const allTimezones = TIMEZONE_GROUPS.flatMap((group) => group.options.map((o) => o.timezone))
    expect(allTimezones).toContain('Africa/Nairobi')
  })
})

describe('timezoneLabel', () => {
  it('returns the curated city label for a known IANA name', () => {
    expect(timezoneLabel('Africa/Nairobi')).toBe('Nairobi')
  })

  it('falls back to the raw IANA name for a value outside the curated list', () => {
    expect(timezoneLabel('Pacific/Fiji')).toBe('Pacific/Fiji')
  })
})
