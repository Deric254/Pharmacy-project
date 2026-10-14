import { useConfigStore } from '../config/store'
import { fallbackTimezone } from './businessDate'

// The backend stores timestamps as UTC but serialises them WITHOUT a zone
// suffix ("2026-10-14T17:13:25"). `new Date()` reads such a string as the
// browser's LOCAL time, so a sale rung up at 20:13 Nairobi time showed as
// 17:13. Treat a zone-less string as UTC, as the receipt already does.
const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i

export function parseServerDate(value: string): Date {
  return new Date(HAS_ZONE.test(value) ? value : `${value}Z`)
}

function businessTimezone(): string {
  return useConfigStore.getState().config?.timezone || fallbackTimezone()
}

function format(value: string, options: Intl.DateTimeFormatOptions): string {
  const date = parseServerDate(value)
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone: businessTimezone(), ...options }).format(
      date,
    )
  } catch {
    return date.toLocaleString()
  }
}

/** e.g. "10/14/2026, 8:13:25 PM" in the business timezone. */
export function formatServerDateTime(value: string): string {
  return format(value, { dateStyle: 'short', timeStyle: 'medium' })
}

/** e.g. "10/14/2026" in the business timezone. */
export function formatServerDate(value: string): string {
  return format(value, { dateStyle: 'short' })
}
