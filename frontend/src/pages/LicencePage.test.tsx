import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LicencePage } from './LicencePage'
import { LicenceRenewalNotice } from '../components/LicenceRenewalNotice'
import { licenceApi, type LicenceState } from '../api/licence'
import { ApiError, LICENCE_READ_ONLY_EVENT } from '../api/client'
import { PAY } from '../lib/payment'

vi.mock('../api/licence', () => ({
  licenceApi: {
    state: vi.fn(),
    activate: vi.fn(),
    claimOwner: vi.fn(),
  },
}))

const LOCKED: LicenceState = {
  ok: false,
  kind: 'unlicensed',
  message: "This computer isn't licensed yet.",
  install_id: 'ABCD-EFGH-JKLM',
}

const LICENSED: LicenceState = {
  ok: true,
  kind: 'licensed',
  message: '',
  install_id: 'ABCD-EFGH-JKLM',
  expires: '2026-12-01',
  days_left: 30,
  user: 'Lucy',
  features: [],
}

describe('LicencePage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('shows the message and the activation code', () => {
    render(<LicencePage state={LOCKED} onActivated={vi.fn()} />)
    expect(screen.getByText("This computer isn't licensed yet.")).toBeInTheDocument()
    expect(screen.getByText('ABCD-EFGH-JKLM')).toBeInTheDocument()
  })

  it('sends the pasted licence and reports success', async () => {
    vi.mocked(licenceApi.activate).mockResolvedValue(LICENSED)
    const onActivated = vi.fn()
    render(<LicencePage state={LOCKED} onActivated={onActivated} />)
    await userEvent.type(screen.getByLabelText('Licence'), 'PHE1.abc.def')
    await userEvent.click(screen.getByRole('button', { name: 'Activate' }))
    await waitFor(() => expect(onActivated).toHaveBeenCalledWith(LICENSED))
    expect(licenceApi.activate).toHaveBeenCalledWith('PHE1.abc.def')
  })

  it('shows the server message verbatim when the licence is refused', async () => {
    vi.mocked(licenceApi.activate).mockRejectedValue(
      new ApiError(400, 'This licence was made for a different computer.', null),
    )
    const onActivated = vi.fn()
    render(<LicencePage state={LOCKED} onActivated={onActivated} />)
    await userEvent.type(screen.getByLabelText('Licence'), 'PHE1.abc.def')
    await userEvent.click(screen.getByRole('button', { name: 'Activate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('different computer')
    expect(onActivated).not.toHaveBeenCalled()
  })

  it('does not offer activation on a build with no owner key', () => {
    render(
      <LicencePage
        state={{ ok: false, kind: 'unconfigured', message: 'This build has no owner key.' }}
        onActivated={vi.fn()}
      />,
    )
    expect(screen.queryByLabelText('Licence')).not.toBeInTheDocument()
  })
})

describe('LicenceRenewalNotice', () => {
  it('stays hidden while the licence has plenty of time left', () => {
    render(<LicenceRenewalNotice state={LICENSED} onRenewed={vi.fn()} />)
    expect(screen.queryByText(/Licence expires/)).not.toBeInTheDocument()
  })

  it('warns when 14 days or fewer remain', () => {
    render(<LicenceRenewalNotice state={{ ...LICENSED, days_left: 3 }} onRenewed={vi.fn()} />)
    expect(screen.getByText('Licence expires in 3 days.')).toBeInTheDocument()
  })

  it('never shows for the owner copy', () => {
    render(
      <LicenceRenewalNotice
        state={{ ok: true, kind: 'owner', message: '', days_left: 1 }}
        onRenewed={vi.fn()}
      />,
    )
    expect(screen.queryByText(/Licence expires/)).not.toBeInTheDocument()
  })
})

const EXPIRED: LicenceState = {
  ok: false,
  kind: 'expired',
  read_only: true,
  message: 'Your licence has expired.',
  install_id: 'ABCD-EFGH-JKLM',
  renew_url: 'https://dericbi.vercel.app/renew.html?app=pharmacy-erp&code=ABCD-EFGH-JKLM',
  expires: '2026-10-01',
  days_left: -30,
}

describe('LicenceRenewalNotice licence modes', () => {
  it('warns during the grace period', () => {
    render(
      <LicenceRenewalNotice
        state={{ ...LICENSED, days_left: -2, grace: true, grace_days_left: 3 }}
        onRenewed={vi.fn()}
      />,
    )
    expect(
      screen.getByText('Licence expired. Renew within 3 days to keep full access.'),
    ).toBeInTheDocument()
  })

  it('says view-only once the grace period is over', () => {
    render(<LicenceRenewalNotice state={EXPIRED} onRenewed={vi.fn()} />)
    expect(screen.getByText(/View-only: you can read and export your data/)).toBeInTheDocument()
  })

  it('never warns a lifetime licence', () => {
    render(
      <LicenceRenewalNotice state={{ ...LICENSED, lifetime: true, days_left: 3 }} onRenewed={vi.fn()} />,
    )
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows the server message when a change is refused, instead of a lock screen', async () => {
    render(<LicenceRenewalNotice state={LICENSED} onRenewed={vi.fn()} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    act(() => {
      window.dispatchEvent(
        new CustomEvent(LICENCE_READ_ONLY_EVENT, { detail: 'Your licence has expired, so this is view-only.' }),
      )
      window.dispatchEvent(
        new CustomEvent(LICENCE_READ_ONLY_EVENT, { detail: 'Your licence has expired, so this is view-only.' }),
      )
    })
    expect(await screen.findAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('so this is view-only')
  })

  it('opens the pay window from the renew dialog and shows the amount and reference', async () => {
    render(<LicenceRenewalNotice state={EXPIRED} onRenewed={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Renew' }))
    await userEvent.click(screen.getByRole('button', { name: /\$100 \/ month/ }))
    expect(screen.getByRole('heading', { name: 'Pay with PayPal' })).toBeInTheDocument()
    expect(screen.getAllByText('$100 USD').length).toBeGreaterThan(0)
    expect(screen.getAllByText(PAY.email).length).toBeGreaterThan(0)
    expect(screen.getByText('LIC-PHA-ABCDEFGHJKLM-M')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open PayPal' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Email my proof' })).toBeInTheDocument()
  })

  it('remembers that the proof was sent and warns before a second payment', async () => {
    localStorage.clear()
    vi.spyOn(window, 'open').mockReturnValue(null)
    render(<LicenceRenewalNotice state={EXPIRED} onRenewed={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Renew' }))
    await userEvent.click(screen.getByRole('button', { name: /\$1000 for life/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Email my proof' }))
    expect(window.open).toHaveBeenCalledWith(
      expect.stringMatching(/^mailto:dericmarangu@gmail\.com\?/),
      '_blank',
      'noopener',
    )
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: /\$1000 for life/ }))
    expect(screen.getByText(/Please do not pay again for the same thing/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'I need to pay again' }))
    expect(screen.getByRole('button', { name: 'Email my proof' })).toBeInTheDocument()
  })

  it('offers the pay buttons on the activation screen too', () => {
    render(<LicencePage state={LOCKED} onActivated={vi.fn()} />)
    expect(screen.getByRole('button', { name: /\$100 \/ month/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /\$1000 for life/ })).toBeEnabled()
  })
})
