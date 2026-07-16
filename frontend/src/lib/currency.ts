import { useConfigStore } from '../config/store'

export function useCurrencyFormatter(): (value: number) => string {
  const currency = useConfigStore((s) => s.config?.currency ?? 'USD')
  return (value: number) => {
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
    } catch {
      // Intl throws on a currency code it doesn't recognize -- fall
      // back to a plain number rather than crash the page over a
      // typo'd currency code in business settings.
      return `${currency} ${value.toFixed(2)}`
    }
  }
}
