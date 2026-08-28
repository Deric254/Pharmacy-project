/**
 * Calendar-date utilities anchored to the business's configured IANA
 * timezone (BusinessConfigOut.timezone) -- never to the browser's own
 * clock/timezone.
 *
 * The bug this closes: every "today" default on the Dashboard and
 * Reports pages used to be built from `new Date()`, the browser's own
 * local time. This app is built to be run from anywhere -- a
 * cloud-hosted backend, a device checked in from a different city --
 * not just a single till physically located where the business is.
 * Whenever the viewing device's timezone differs from the business's
 * configured one, "today" as the browser understands it can be a
 * different calendar day than "today" for the business, silently
 * dropping or shifting hours of real sales into or out of a report
 * with no error shown. Every place that means "today, for this
 * business" must resolve it through this module instead, so there is
 * exactly one definition of "today" and it stays correct regardless
 * of where the app -- or the person looking at it -- happens to be.
 */

function partsInTimeZone(date: Date, timeZone: string): { year: number; month: number; day: number } {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const parts = formatter.formatToParts(date)
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  return { year: get('year'), month: get('month'), day: get('day') }
}

function toIsoDate({ year, month, day }: { year: number; month: number; day: number }): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${year}-${pad(month)}-${pad(day)}`
}

function fromIsoDate(isoDate: string): { year: number; month: number; day: number } {
  const [year, month, day] = isoDate.split('-').map(Number)
  return { year, month, day }
}

/** Today's calendar date, in `timezone`, as YYYY-MM-DD. */
export function businessToday(timezone: string): string {
  return toIsoDate(partsInTimeZone(new Date(), timezone))
}

/**
 * The device's own timezone -- used only as a fallback when the
 * business's configured timezone genuinely failed to load (see
 * config/store.ts's own "never block the app" behavior). Never used
 * as the primary source of "today"; that would silently reintroduce
 * the exact bug this module exists to close.
 */
export function fallbackTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/**
 * `isoDate` minus `days` calendar days. Arithmetic runs on the
 * calendar date itself (via a noon-UTC anchor), not on a raw UTC
 * instant, so it stays correct regardless of timezone or DST.
 */
export function subtractDays(isoDate: string, days: number): string {
  const { year, month, day } = fromIsoDate(isoDate)
  const dt = new Date(Date.UTC(year, month - 1, day, 12))
  dt.setUTCDate(dt.getUTCDate() - days)
  return toIsoDate({ year: dt.getUTCFullYear(), month: dt.getUTCMonth() + 1, day: dt.getUTCDate() })
}

/** First day of the calendar month containing `isoDate`. */
export function startOfMonth(isoDate: string): string {
  const { year, month } = fromIsoDate(isoDate)
  return toIsoDate({ year, month, day: 1 })
}
