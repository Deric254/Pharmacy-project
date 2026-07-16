export interface BusinessConfigOut {
  business_name: string
  slogan: string
  logo_url: string | null
  theme_name: string
  primary_color: string
  secondary_color: string
  receipt_header_text: string
  receipt_footer_text: string
  currency: string
  tax_rate: number
  tax_id: string | null
  contact_phone: string | null
  contact_email: string | null
  address: string | null
  default_language: string
  timezone: string
  low_stock_threshold_default: number
  expiry_alert_days: number[]
  loyalty_program_enabled: boolean
  loyalty_points_per_currency_unit: number
}

export interface BusinessConfigUpdate {
  business_name?: string
  slogan?: string
  logo_url?: string | null
  theme_name?: string
  primary_color?: string
  secondary_color?: string
  receipt_header_text?: string
  receipt_footer_text?: string
  currency?: string
  tax_rate?: number
  tax_id?: string | null
  contact_phone?: string | null
  contact_email?: string | null
  address?: string | null
  default_language?: string
  timezone?: string
  low_stock_threshold_default?: number
  expiry_alert_days?: number[]
  loyalty_program_enabled?: boolean
  loyalty_points_per_currency_unit?: number
}
