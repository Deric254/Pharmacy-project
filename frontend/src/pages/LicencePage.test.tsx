import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LicencePage } from './LicencePage'
import { LicenceRenewalNotice } from '../components/LicenceRenewalNotice'
import { licenceApi, type LicenceState } from '../api/licence'
import { ApiError } from '../api/client'

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
