import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, LICENCE_READ_ONLY_EVENT, LICENCE_REQUIRED_EVENT } from './client'

function respond(status: number, body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })),
  )
}

describe('a 402 from the licence gate', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('is a renewal prompt, not a lock, when the copy is only view-only', async () => {
    const detail = 'Your licence has expired, so this is view-only. Renew your licence to add or change anything.'
    respond(402, { detail, licence: { ok: false, kind: 'expired', read_only: true, renew_url: 'x' } })
    const readOnly = vi.fn()
    const locked = vi.fn()
    window.addEventListener(LICENCE_READ_ONLY_EVENT, readOnly)
    window.addEventListener(LICENCE_REQUIRED_EVENT, locked)
    await expect(api.post('/products', {})).rejects.toMatchObject({ status: 402, message: detail })
    window.removeEventListener(LICENCE_READ_ONLY_EVENT, readOnly)
    window.removeEventListener(LICENCE_REQUIRED_EVENT, locked)
    expect(readOnly).toHaveBeenCalledTimes(1)
    expect((readOnly.mock.calls[0][0] as CustomEvent<string>).detail).toBe(detail)
    expect(locked).not.toHaveBeenCalled()
  })

  it('still asks the app to re-check (and lock) when the copy has no valid licence', async () => {
    respond(402, { detail: "This computer isn't licensed yet.", licence: { ok: false, kind: 'unlicensed' } })
    const readOnly = vi.fn()
    const locked = vi.fn()
    window.addEventListener(LICENCE_READ_ONLY_EVENT, readOnly)
    window.addEventListener(LICENCE_REQUIRED_EVENT, locked)
    await expect(api.get('/products')).rejects.toBeInstanceOf(ApiError)
    window.removeEventListener(LICENCE_READ_ONLY_EVENT, readOnly)
    window.removeEventListener(LICENCE_REQUIRED_EVENT, locked)
    expect(locked).toHaveBeenCalledTimes(1)
    expect(readOnly).not.toHaveBeenCalled()
  })
})
