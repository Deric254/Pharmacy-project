import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './SettingsPage'
import { useConfigStore } from '../config/store'
import { configApi } from '../api/config'
import type { BusinessConfigOut } from '../types/config'

vi.mock('../api/config', () => ({
  configApi: {
    get: vi.fn(),
    update: vi.fn(),
  },
}))

// SettingsPage also renders the "Software updates" section, which
// calls out to GitHub for release info the moment its "Check for
// updates" button is pressed -- not relevant to anything under test
// here, and a real network call has no place in a unit test.
vi.mock('../lib/updateCheck', () => ({
  useUpdateCheck: () => ({ info: null, checking: false, checkNow: vi.fn() }),
}))

const BASE_CONFIG: BusinessConfigOut = {
  business_name: 'Test Pharmacy',
  slogan: '',
  logo_url: null,
  theme_name: 'ledger',
  primary_color: '#000',
  secondary_color: '#000',
  receipt_header_text: '',
  receipt_footer_text: '',
  currency: 'KES',
  tax_rate: 0,
  tax_id: null,
  contact_phone: null,
  contact_email: null,
  address: null,
  default_language: 'en',
  timezone: 'Africa/Nairobi',
  low_stock_threshold_default: 5,
  expiry_alert_days: [30, 60],
  loyalty_program_enabled: false,
  loyalty_points_per_currency_unit: 0,
  local_backup_dir_override: null,
}

function seedConfig(overrides: Partial<BusinessConfigOut> = {}) {
  useConfigStore.setState({
    config: { ...BASE_CONFIG, ...overrides },
    status: 'ready',
  })
}

describe('SettingsPage timezone field', () => {
  beforeEach(() => {
    vi.mocked(configApi.update).mockReset()
  })

  it("pre-selects the business's currently saved timezone", () => {
    seedConfig({ timezone: 'Africa/Lagos' })
    render(<SettingsPage />)

    const select = screen.getByLabelText(/timezone/i) as HTMLSelectElement
    expect(select.value).toBe('Africa/Lagos')
  })

  it('offers cities grouped by region, not raw IANA names', () => {
    seedConfig()
    render(<SettingsPage />)

    expect(screen.getByRole('option', { name: 'Nairobi' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'East Africa' })).toBeInTheDocument()
  })

  it('still shows a saved value outside the curated list, rather than silently mismatching it', () => {
    // A value set before this picker existed, or via some other path.
    seedConfig({ timezone: 'Pacific/Fiji' })
    render(<SettingsPage />)

    const select = screen.getByLabelText(/timezone/i) as HTMLSelectElement
    expect(select.value).toBe('Pacific/Fiji')
  })

  it('saves the newly selected timezone, not the one the page loaded with', async () => {
    seedConfig({ timezone: 'Africa/Nairobi' })
    const updated = { ...BASE_CONFIG, timezone: 'Asia/Dubai' }
    vi.mocked(configApi.update).mockResolvedValue(updated)
    // handleSubmit calls refresh() after a successful update, which
    // itself calls configApi.get() -- needs a real resolved config or
    // applyBranding() downstream throws on an undefined config.
    vi.mocked(configApi.get).mockResolvedValue(updated)
    const user = userEvent.setup()
    render(<SettingsPage />)

    await user.selectOptions(screen.getByLabelText(/timezone/i), 'Asia/Dubai')
    await user.click(screen.getByRole('button', { name: /save settings/i }))

    await waitFor(() => {
      expect(configApi.update).toHaveBeenCalledWith(
        expect.objectContaining({ timezone: 'Asia/Dubai' }),
      )
    })
  })
})
