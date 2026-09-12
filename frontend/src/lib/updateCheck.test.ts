import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useReleaseHistory, useUpdateCheck } from './updateCheck'

const HEALTH_URL = '/health'
const RELEASES_LATEST_URL = 'https://api.github.com/repos/Deric254/Pharmacy-project/releases/latest'
const RELEASES_URL = 'https://api.github.com/repos/Deric254/Pharmacy-project/releases'

function jsonResponse(body: unknown, ok = true) {
  return { ok, json: async () => body } as Response
}

function installerAsset(name: string) {
  return [{ name, browser_download_url: `https://example.com/${name}` }]
}

describe('useUpdateCheck', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('correctly compares double-digit version segments, not string order', async () => {
    // The exact bug class naive string comparison gets wrong:
    // "1.9.0" > "1.10.0" as strings, but 1.10.0 is the newer release.
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '1.9.0' }))
      if (url === RELEASES_LATEST_URL) {
        return Promise.resolve(
          jsonResponse({
            tag_name: 'v1.10.0',
            html_url: 'https://github.com/releases/v1.10.0',
            assets: installerAsset('Pharmacy-ERP-Setup-1.10.0.exe'),
          }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(result.current.info).not.toBeNull())
    expect(result.current.info?.currentVersion).toBe('1.9.0')
    expect(result.current.info?.latestVersion).toBe('1.10.0')
  })

  it('reports no update when already on the latest version', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '2.0.0' }))
      if (url === RELEASES_LATEST_URL) {
        return Promise.resolve(
          jsonResponse({
            tag_name: 'v2.0.0',
            html_url: 'https://github.com/releases/v2.0.0',
            assets: installerAsset('Pharmacy-ERP-Setup-2.0.0.exe'),
          }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useUpdateCheck())

    // Give the effect a tick to run and settle -- info should stay
    // null the whole time, never briefly show a false update.
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current.info).toBeNull()
  })

  it('only ever points at the real installer asset, never the bare backend exe', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '1.0.0' }))
      if (url === RELEASES_LATEST_URL) {
        return Promise.resolve(
          jsonResponse({
            tag_name: 'v1.1.0',
            html_url: 'https://github.com/releases/v1.1.0',
            assets: [
              { name: 'pharmacy-backend.exe', browser_download_url: 'https://example.com/raw' },
              {
                name: 'Pharmacy-ERP-Setup-1.1.0.exe',
                browser_download_url: 'https://example.com/installer',
              },
            ],
          }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(result.current.info).not.toBeNull())
    expect(result.current.info?.downloadUrl).toBe('https://example.com/installer')
  })
})

describe('useReleaseHistory', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists every release with an installer, flagging the currently installed one', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '1.5.0' }))
      if (url === RELEASES_URL) {
        return Promise.resolve(
          jsonResponse([
            {
              tag_name: 'v1.6.0',
              html_url: 'https://github.com/releases/v1.6.0',
              assets: installerAsset('Pharmacy-ERP-Setup-1.6.0.exe'),
            },
            {
              tag_name: 'v1.5.0',
              html_url: 'https://github.com/releases/v1.5.0',
              assets: installerAsset('Pharmacy-ERP-Setup-1.5.0.exe'),
            },
            {
              tag_name: 'v1.4.0',
              html_url: 'https://github.com/releases/v1.4.0',
              assets: installerAsset('Pharmacy-ERP-Setup-1.4.0.exe'),
            },
          ]),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useReleaseHistory())
    await result.current.load()

    await waitFor(() => expect(result.current.releases).not.toBeNull())
    expect(result.current.releases).toHaveLength(3)
    const current = result.current.releases?.find((r) => r.version === '1.5.0')
    expect(current?.isCurrent).toBe(true)
    expect(result.current.releases?.find((r) => r.version === '1.6.0')?.isCurrent).toBe(false)
    expect(result.current.releases?.find((r) => r.version === '1.4.0')?.isCurrent).toBe(false)
  })

  it('excludes releases with no installer asset attached, rather than offering a dead link', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '1.0.0' }))
      if (url === RELEASES_URL) {
        return Promise.resolve(
          jsonResponse([
            {
              tag_name: 'v1.1.0',
              html_url: 'https://github.com/releases/v1.1.0',
              assets: [], // e.g. a release still building, or source-only
            },
            {
              tag_name: 'v1.0.0',
              html_url: 'https://github.com/releases/v1.0.0',
              assets: installerAsset('Pharmacy-ERP-Setup-1.0.0.exe'),
            },
          ]),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useReleaseHistory())
    await result.current.load()

    await waitFor(() => expect(result.current.releases).not.toBeNull())
    expect(result.current.releases).toHaveLength(1)
    expect(result.current.releases?.[0]?.version).toBe('1.0.0')
  })

  it('surfaces an error rather than a silent empty list when the request fails', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '1.0.0' }))
      if (url === RELEASES_URL) return Promise.resolve(jsonResponse(null, false))
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useReleaseHistory())
    await result.current.load()

    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current.releases).toBeNull()
  })
})
