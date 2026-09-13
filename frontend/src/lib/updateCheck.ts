import { useEffect, useState } from 'react'
import { fetchWithTimeout } from '../api/client'

const REPO = 'Deric254/Pharmacy-project'

// Every other fetch in this app goes through client.ts's own
// fetchWithTimeout precisely because a bare fetch() that never
// resolves or rejects is a real, previously-reported failure mode
// (see that file's own comment) -- this update check used to be the
// one remaining exception, calling fetch() directly with no bound at
// all. That mattered here specifically: unlike the backend health
// check (a same-machine loopback call), this reaches out to GitHub's
// public API, which is only reachable at all when the machine has
// internet access -- and a slow, filtered, or half-working connection
// (common on a small business's network) can leave an un-timed fetch
// hanging far longer than any offline failure ever would, which is
// exactly the "sometimes hangs on startup, but only with internet"
// shape this was causing. This check is purely informational (an
// update banner), so a shorter bound than the app's general 30s
// default is fine -- there is nothing here worth a long wait for.
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

/** Simple numeric semver comparison -- good enough for x.y.z tags,
 * which is all this project's release workflow ever produces. */
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
  if (!releaseRes.ok) return null // no releases yet, rate-limited, offline -- fine, just skip
  const release = (await releaseRes.json()) as GithubRelease

  if (!isNewer(release.tag_name, health.version)) return null

  // Specifically the installer (Pharmacy-ERP-Setup-*.exe), not just
  // any .exe -- a release attaches both the installer and the raw
  // backend exe it wraps (the latter exists only so Electron has
  // something to bundle, never meant as a public download), and an
  // in-app update banner should only ever point someone at the one
  // real users are meant to run.
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
    } catch {
      // Update checks are informational, never load-bearing -- any
      // failure (offline, GitHub API down, rate-limited) just means
      // no banner shows, not an error the user needs to see.
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    fetchLatestReleaseInfo()
      .then((result) => {
        if (!cancelled) setInfo(result)
      })
      .catch(() => {
        // Same as above -- silent on failure, this is the automatic
        // once-per-session check, not a user-initiated action that
        // needs feedback either way.
      })
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

/**
 * Every release that has a real installer asset, newest first --
 * unlike useUpdateCheck above (which only ever surfaces "is there
 * something newer"), this is what lets someone deliberately install
 * an OLDER version too. Kept as a separate, on-demand hook rather
 * than folded into useUpdateCheck: fetching the full release list is
 * unnecessary API usage for the common case (just checking whether
 * to upgrade), and this is only ever needed once someone actually
 * opens the "install a specific version" section.
 */
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
