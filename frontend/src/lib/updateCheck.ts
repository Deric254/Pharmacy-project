import { useEffect, useState } from 'react'
import { fetchWithTimeout } from '../api/client'
import { runAfterStartup } from './startupSettled'

const REPO = 'Deric254/Pharmacy-project'

const MANUAL_CHECK_TIMEOUT_MS = 10_000
const AUTO_CHECK_TIMEOUT_MS = 4_000

const AUTO_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000
const AUTO_CHECK_CACHE_KEY = 'pharmacy-erp:update-check-cache'

export interface UpdateInfo {
  currentVersion: string
  latestVersion: string
  downloadUrl: string | null
  releaseUrl: string
}

interface GithubRelease {
  tag_name: string
  html_url: string
  assets: { name: string; browser_download_url: string }[]
}

interface UpdateCheckCacheEntry {
  checkedAt: number
  info: UpdateInfo | null
}

function normalizeVersion(v: string): string {
  return v.replace(/^v/i, '')
}

function isNewer(latest: string, current: string): boolean {
  const a = normalizeVersion(latest).split('.').map(Number)
  const b = normalizeVersion(current).split('.').map(Number)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0)
    if (diff !== 0) return diff > 0
  }
  return false
}

function readAutoCheckCache(): UpdateCheckCacheEntry | null {
  try {
    const raw = window.localStorage.getItem(AUTO_CHECK_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<UpdateCheckCacheEntry>
    if (typeof parsed.checkedAt !== 'number') return null
    return { checkedAt: parsed.checkedAt, info: parsed.info ?? null }
  } catch {
    return null
  }
}

function writeAutoCheckCache(info: UpdateInfo | null): void {
  try {
    const entry: UpdateCheckCacheEntry = { checkedAt: Date.now(), info }
    window.localStorage.setItem(AUTO_CHECK_CACHE_KEY, JSON.stringify(entry))
  } catch {
  }
}

interface UpdateCheckResult {
  info: UpdateInfo | null
  checking: boolean
  checkNow: () => Promise<void>
}

async function fetchLatestReleaseInfo(timeoutMs: number): Promise<UpdateInfo | null> {
  const [healthRes, releaseRes] = await Promise.all([
    fetchWithTimeout('/health', {}, timeoutMs),
    fetchWithTimeout(`https://api.github.com/repos/${REPO}/releases/latest`, {}, timeoutMs),
  ])
  if (!healthRes.ok || !releaseRes.ok) return null
  const health = (await healthRes.json()) as { version: string }
  const release = (await releaseRes.json()) as GithubRelease

  if (!isNewer(release.tag_name, health.version)) return null

  const installerAsset = release.assets.find(
    (a) => a.name.startsWith('Pharmacy-ERP-Setup-') && a.name.endsWith('.exe'),
  )
  return {
    currentVersion: health.version,
    latestVersion: normalizeVersion(release.tag_name),
    downloadUrl: installerAsset?.browser_download_url ?? null,
    releaseUrl: release.html_url,
  }
}

export function useUpdateCheck(): UpdateCheckResult {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)

  async function checkNow() {
    setChecking(true)
    try {
      const result = await fetchLatestReleaseInfo(MANUAL_CHECK_TIMEOUT_MS)
      setInfo(result)
      writeAutoCheckCache(result)
    } catch {} finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    const cached = readAutoCheckCache()
    if (cached && Date.now() - cached.checkedAt < AUTO_CHECK_INTERVAL_MS) {
      setInfo(cached.info)
      return
    }

    const cancelScheduled = runAfterStartup(() => {
      fetchLatestReleaseInfo(AUTO_CHECK_TIMEOUT_MS)
        .then((result) => {
          writeAutoCheckCache(result)
          if (!cancelled) setInfo(result)
        })
        .catch(() => {})
    })
    return () => {
      cancelled = true
      cancelScheduled()
    }
  }, [])

  return { info, checking, checkNow }
}

interface ReleaseOption {
  version: string
  downloadUrl: string
  releaseUrl: string
  isCurrent: boolean
}

export function useReleaseHistory(): {
  releases: ReleaseOption[] | null
  loading: boolean
  error: boolean
  load: () => Promise<void>
} {
  const [releases, setReleases] = useState<ReleaseOption[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)

  async function load() {
    setLoading(true)
    setError(false)
    try {
      const [healthRes, releasesRes] = await Promise.all([
        fetchWithTimeout('/health', {}, MANUAL_CHECK_TIMEOUT_MS),
        fetchWithTimeout(`https://api.github.com/repos/${REPO}/releases`, {}, MANUAL_CHECK_TIMEOUT_MS),
      ])
      const health = healthRes.ok ? ((await healthRes.json()) as { version: string }) : null
      const currentVersion = health ? normalizeVersion(health.version) : null

      if (!releasesRes.ok) {
        setError(true)
        return
      }
      const allReleases = (await releasesRes.json()) as GithubRelease[]

      const options = allReleases
        .map((release) => {
          const installerAsset = release.assets.find(
            (a) => a.name.startsWith('Pharmacy-ERP-Setup-') && a.name.endsWith('.exe'),
          )
          if (!installerAsset) return null
          const version = normalizeVersion(release.tag_name)
          return {
            version,
            downloadUrl: installerAsset.browser_download_url,
            releaseUrl: release.html_url,
            isCurrent: version === currentVersion,
          }
        })
        .filter((option): option is ReleaseOption => option !== null)

      setReleases(options)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }

  return { releases, loading, error, load }
}
