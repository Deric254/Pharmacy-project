import { useEffect, useState } from 'react'

const REPO = 'Deric254/Pharmacy-project'

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

export function useUpdateCheck(): UpdateInfo | null {
  const [info, setInfo] = useState<UpdateInfo | null>(null)

  useEffect(() => {
    let cancelled = false

    async function check() {
      try {
        const healthRes = await fetch('/health')
        if (!healthRes.ok) return
        const health = (await healthRes.json()) as { version: string }

        const releaseRes = await fetch(
          `https://api.github.com/repos/${REPO}/releases/latest`,
        )
        if (!releaseRes.ok) return // no releases yet, rate-limited, offline -- fine, just skip
        const release = (await releaseRes.json()) as GithubRelease

        if (cancelled) return
        if (!isNewer(release.tag_name, health.version)) return

        // Specifically the installer (Pharmacy-ERP-Setup-*.exe), not
        // just any .exe -- a release attaches both the installer and
        // the raw backend exe it wraps (the latter exists only so
        // Electron has something to bundle, never meant as a public
        // download), and an in-app update banner should only ever
        // point someone at the one real users are meant to run.
        const installerAsset = release.assets.find(
          (a) => a.name.startsWith('Pharmacy-ERP-Setup-') && a.name.endsWith('.exe'),
        )
        setInfo({
          currentVersion: health.version,
          latestVersion: normalizeVersion(release.tag_name),
          downloadUrl: installerAsset?.browser_download_url ?? null,
          releaseUrl: release.html_url,
        })
      } catch {
        // Update checks are informational, never load-bearing -- any
        // failure (offline, GitHub API down, rate-limited) just means
        // no banner shows, not an error the user needs to see.
      }
    }

    void check()
    return () => {
      cancelled = true
    }
  }, [])

  return info
}
