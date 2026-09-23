import { useEffect, useState } from 'react'
import { fetchWithTimeout } from '../api/client'

const REPO = 'Deric254/Pharmacy-project'

const UPDATE_CHECK_TIMEOUT_MS = 10_000

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

export interface UpdateCheckResult {
  info: UpdateInfo | null
  checking: boolean
  checkNow: () => Promise<void>
}
async function fetchLatestReleaseInfo(): Promise<UpdateInfo | null> {
  const healthRes = await fetchWithTimeout('/health', {}, UPDATE_CHECK_TIMEOUT_MS)
  if (!healthRes.ok) return null
  const health = (await healthRes.json()) as { version: string }

  const releaseRes = await fetchWithTimeout(
    `https://api.github.com/repos/${REPO}/releases/latest`,
    {},
    UPDATE_CHECK_TIMEOUT_MS,
  )
  if (!releaseRes.ok) return null 
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
      const result = await fetchLatestReleaseInfo()
      setInfo(result)
    } catch {} finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    fetchLatestReleaseInfo()
      .then((result) => {
        if (!cancelled) setInfo(result)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  return { info, checking, checkNow }
}

export interface ReleaseOption {
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
      const healthRes = await fetchWithTimeout('/health', {}, UPDATE_CHECK_TIMEOUT_MS)
      const health = healthRes.ok ? ((await healthRes.json()) as { version: string }) : null
      const currentVersion = health ? normalizeVersion(health.version) : null

      const releasesRes = await fetchWithTimeout(
        `https://api.github.com/repos/${REPO}/releases`,
        {},
        UPDATE_CHECK_TIMEOUT_MS,
      )
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
