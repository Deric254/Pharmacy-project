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
    // The automatic check now caches its result in localStorage so a
    // fresh check isn't repeated on every mount -- each test needs to
    // start from "no prior check" to exercise a real fetch.
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('correctly compares double-digit version segments, not string order', async () => {
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

  it('does not hit the network on mount when a check ran within the last 24h', async () => {
    const cachedInfo = {
      currentVersion: '1.0.0',
      latestVersion: '1.1.0',
      downloadUrl: 'https://example.com/installer',
      releaseUrl: 'https://github.com/releases/v1.1.0',
    }
    window.localStorage.setItem(
      'pharmacy-erp:update-check-cache',
      JSON.stringify({ checkedAt: Date.now() - 1000, info: cachedInfo }),
    )

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(result.current.info).toEqual(cachedInfo))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('checks the network again once the cached result is more than 24h old', async () => {
    window.localStorage.setItem(
      'pharmacy-erp:update-check-cache',
      JSON.stringify({
        checkedAt: Date.now() - 25 * 60 * 60 * 1000,
        info: null,
      }),
    )
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '2.0.0' }))
      if (url === RELEASES_LATEST_URL) {
        return Promise.resolve(
          jsonResponse({
            tag_name: 'v2.1.0',
            html_url: 'https://github.com/releases/v2.1.0',
            assets: installerAsset('Pharmacy-ERP-Setup-2.1.0.exe'),
          }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(result.current.info).not.toBeNull())
    expect(result.current.info?.latestVersion).toBe('2.1.0')
    expect(fetchMock).toHaveBeenCalled()
  })

  it('caches a fresh automatic check so the next mount can skip the network', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '3.0.0' }))
      if (url === RELEASES_LATEST_URL) {
        return Promise.resolve(jsonResponse({ tag_name: 'v3.0.0', html_url: '', assets: [] }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderHook(() => useUpdateCheck())

    await waitFor(() => {
      const raw = window.localStorage.getItem('pharmacy-erp:update-check-cache')
      expect(raw).not.toBeNull()
    })
    const cached = JSON.parse(window.localStorage.getItem('pharmacy-erp:update-check-cache')!)
    expect(cached.info).toBeNull() // already on the latest version
    expect(typeof cached.checkedAt).toBe('number')
  })

  it('checkNow always hits the network even when a fresh cache entry exists', async () => {
    window.localStorage.setItem(
      'pharmacy-erp:update-check-cache',
      JSON.stringify({ checkedAt: Date.now(), info: null }),
    )
    fetchMock.mockImplementation((url: string) => {
      if (url === HEALTH_URL) return Promise.resolve(jsonResponse({ version: '1.0.0' }))
      if (url === RELEASES_LATEST_URL) {
        return Promise.resolve(
          jsonResponse({
            tag_name: 'v1.2.0',
            html_url: 'https://github.com/releases/v1.2.0',
            assets: installerAsset('Pharmacy-ERP-Setup-1.2.0.exe'),
          }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useUpdateCheck())
    // Let the (cache-hit, no-fetch) mount effect settle first.
    await waitFor(() => expect(result.current.info).toBeNull())
    fetchMock.mockClear()

    await result.current.checkNow()

    expect(fetchMock).toHaveBeenCalled()
    await waitFor(() => expect(result.current.info?.latestVersion).toBe('1.2.0'))
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
              assets: [], 
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
