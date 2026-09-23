
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

export function businessToday(timezone: string): string {
  return toIsoDate(partsInTimeZone(new Date(), timezone))
}

export function fallbackTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

export function subtractDays(isoDate: string, days: number): string {
  const { year, month, day } = fromIsoDate(isoDate)
  const dt = new Date(Date.UTC(year, month - 1, day, 12))
  dt.setUTCDate(dt.getUTCDate() - days)
  return toIsoDate({ year: dt.getUTCFullYear(), month: dt.getUTCMonth() + 1, day: dt.getUTCDate() })
}

export function startOfMonth(isoDate: string): string {
  const { year, month } = fromIsoDate(isoDate)
  return toIsoDate({ year, month, day: 1 })
}
