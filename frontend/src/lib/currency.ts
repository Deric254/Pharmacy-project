import { useConfigStore } from '../config/store'

/**
 * The actual formatting logic, pulled out from the hook so it's
 * directly unit-testable without any React rendering machinery --
 * the hook below is just this function plus a subscription to
 * wherever the currency code currently lives.
 */
export function formatMoney(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
  } catch {
    // Intl throws on a currency code it doesn't recognize -- fall
    // back to a plain number rather than crash the page over a
    // typo'd currency code in business settings.
    return `${currency} ${value.toFixed(2)}`
  }
}

export function useCurrencyFormatter(): (value: number) => string {
  const currency = useConfigStore((s) => s.config?.currency ?? 'USD')
  return (value: number) => formatMoney(value, currency)
}
