/**
 * A curated city list, not the full ~400-zone IANA database -- nobody
 * picking a timezone for their pharmacy wants to scroll a list that
 * long, and most of those zones are uninhabited islands or historical
 * aliases. Each entry's `timezone` value is a real IANA name; the
 * `city` label is just what the person sees and searches by.
 *
 * Grouped by region for a readable dropdown, sorted with this app's
 * primary market (East/West/Southern Africa) first.
 */
export interface TimezoneOption {
  city: string
  timezone: string
}

export interface TimezoneGroup {
  region: string
  options: TimezoneOption[]
}

export const TIMEZONE_GROUPS: TimezoneGroup[] = [
  {
    region: 'East Africa',
    options: [
      { city: 'Nairobi', timezone: 'Africa/Nairobi' },
      { city: 'Kampala', timezone: 'Africa/Kampala' },
      { city: 'Dar es Salaam', timezone: 'Africa/Dar_es_Salaam' },
      { city: 'Kigali', timezone: 'Africa/Kigali' },
      { city: 'Addis Ababa', timezone: 'Africa/Addis_Ababa' },
      { city: 'Mogadishu', timezone: 'Africa/Mogadishu' },
      { city: 'Juba', timezone: 'Africa/Juba' },
      { city: 'Khartoum', timezone: 'Africa/Khartoum' },
    ],
  },
  {
    region: 'West Africa',
    options: [
      { city: 'Lagos', timezone: 'Africa/Lagos' },
      { city: 'Accra', timezone: 'Africa/Accra' },
      { city: 'Abidjan', timezone: 'Africa/Abidjan' },
      { city: 'Dakar', timezone: 'Africa/Dakar' },
      { city: 'Bamako', timezone: 'Africa/Bamako' },
      { city: 'Niamey', timezone: 'Africa/Niamey' },
    ],
  },
  {
    region: 'Southern Africa',
    options: [
      { city: 'Johannesburg', timezone: 'Africa/Johannesburg' },
      { city: 'Lusaka', timezone: 'Africa/Lusaka' },
      { city: 'Harare', timezone: 'Africa/Harare' },
      { city: 'Gaborone', timezone: 'Africa/Gaborone' },
      { city: 'Maputo', timezone: 'Africa/Maputo' },
      { city: 'Windhoek', timezone: 'Africa/Windhoek' },
    ],
  },
  {
    region: 'North Africa',
    options: [
      { city: 'Cairo', timezone: 'Africa/Cairo' },
      { city: 'Casablanca', timezone: 'Africa/Casablanca' },
      { city: 'Tunis', timezone: 'Africa/Tunis' },
      { city: 'Algiers', timezone: 'Africa/Algiers' },
      { city: 'Tripoli', timezone: 'Africa/Tripoli' },
    ],
  },
  {
    region: 'Europe',
    options: [
      { city: 'London', timezone: 'Europe/London' },
      { city: 'Paris', timezone: 'Europe/Paris' },
      { city: 'Berlin', timezone: 'Europe/Berlin' },
      { city: 'Madrid', timezone: 'Europe/Madrid' },
      { city: 'Rome', timezone: 'Europe/Rome' },
      { city: 'Amsterdam', timezone: 'Europe/Amsterdam' },
      { city: 'Moscow', timezone: 'Europe/Moscow' },
    ],
  },
  {
    region: 'Middle East',
    options: [
      { city: 'Dubai', timezone: 'Asia/Dubai' },
      { city: 'Riyadh', timezone: 'Asia/Riyadh' },
      { city: 'Doha', timezone: 'Asia/Qatar' },
      { city: 'Istanbul', timezone: 'Europe/Istanbul' },
      { city: 'Jerusalem', timezone: 'Asia/Jerusalem' },
    ],
  },
  {
    region: 'Asia',
    options: [
      { city: 'Mumbai / New Delhi', timezone: 'Asia/Kolkata' },
      { city: 'Karachi', timezone: 'Asia/Karachi' },
      { city: 'Dhaka', timezone: 'Asia/Dhaka' },
      { city: 'Bangkok', timezone: 'Asia/Bangkok' },
      { city: 'Singapore', timezone: 'Asia/Singapore' },
      { city: 'Hong Kong', timezone: 'Asia/Hong_Kong' },
      { city: 'Shanghai / Beijing', timezone: 'Asia/Shanghai' },
      { city: 'Tokyo', timezone: 'Asia/Tokyo' },
      { city: 'Seoul', timezone: 'Asia/Seoul' },
      { city: 'Jakarta', timezone: 'Asia/Jakarta' },
      { city: 'Manila', timezone: 'Asia/Manila' },
    ],
  },
  {
    region: 'Americas',
    options: [
      { city: 'New York', timezone: 'America/New_York' },
      { city: 'Chicago', timezone: 'America/Chicago' },
      { city: 'Denver', timezone: 'America/Denver' },
      { city: 'Los Angeles', timezone: 'America/Los_Angeles' },
      { city: 'Toronto', timezone: 'America/Toronto' },
      { city: 'Mexico City', timezone: 'America/Mexico_City' },
      { city: 'São Paulo', timezone: 'America/Sao_Paulo' },
      { city: 'Buenos Aires', timezone: 'America/Argentina/Buenos_Aires' },
    ],
  },
  {
    region: 'Oceania',
    options: [
      { city: 'Sydney', timezone: 'Australia/Sydney' },
      { city: 'Perth', timezone: 'Australia/Perth' },
      { city: 'Auckland', timezone: 'Pacific/Auckland' },
    ],
  },
  {
    region: 'Other',
    options: [{ city: 'UTC (no local offset)', timezone: 'UTC' }],
  },
]

/** Flat lookup, built once, for turning a stored IANA name back into its city label. */
const CITY_BY_TIMEZONE: Record<string, string> = Object.fromEntries(
  TIMEZONE_GROUPS.flatMap((group) => group.options.map((opt) => [opt.timezone, opt.city])),
)

/**
 * Label for a stored IANA timezone value that isn't in the curated
 * list (a pre-existing value from before this picker existed, or one
 * set by some other path). Falls back to the raw IANA name itself
 * rather than hiding it -- the person should always be able to see
 * what's actually saved, even if it's not one of the common choices.
 */
export function timezoneLabel(iana: string): string {
  return CITY_BY_TIMEZONE[iana] ?? iana
}

/** The browser's own detected IANA timezone, for defaulting a fresh setup sensibly. */
export function detectBrowserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return 'UTC'
  }
}
