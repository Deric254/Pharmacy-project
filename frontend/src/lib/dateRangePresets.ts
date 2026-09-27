import { businessToday, startOfMonth, subtractDays } from './businessDate'

export type DateRangePreset = 'today' | 'week' | 'month' | 'custom'

export function presetRange(
  preset: DateRangePreset,
  timezone: string,
): { start: string; end: string } {
  const end = businessToday(timezone)
  if (preset === 'week') {
    return { start: subtractDays(end, 6), end }
  }
  if (preset === 'month') {
    return { start: startOfMonth(end), end }
  }
  return { start: end, end }
}
