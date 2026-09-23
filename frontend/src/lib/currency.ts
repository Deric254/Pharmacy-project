import { useConfigStore } from '../config/store'

export function formatMoney(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
  } catch {
    return `${currency} ${value.toFixed(2)}`
  }
}

export function useCurrencyFormatter(): (value: number) => string {
  const currency = useConfigStore((s) => s.config?.currency ?? 'USD')
  return (value: number) => formatMoney(value, currency)
}
