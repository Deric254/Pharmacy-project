import { useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react'
import { useConfigStore } from '../config/store'
import { configApi } from '../api/config'
import { ApiError } from '../api/client'
import { THEMES, applyTheme } from '../theme/themes'
import { useUpdateCheck } from '../lib/updateCheck'
import { TIMEZONE_GROUPS, timezoneLabel } from '../lib/timezones'

export function SettingsPage() {
  const config = useConfigStore((s) => s.config)
  const refresh = useConfigStore((s) => s.refresh)

  const [businessName, setBusinessName] = useState(config?.business_name ?? '')
  const [slogan, setSlogan] = useState(config?.slogan ?? '')
  const [logoUrl, setLogoUrl] = useState(config?.logo_url ?? '')
  const [localBackupDirOverride, setLocalBackupDirOverride] = useState(
    config?.local_backup_dir_override ?? '',
  )
  const [logoError, setLogoError] = useState<string | null>(null)
  const [currency, setCurrency] = useState(config?.currency ?? 'USD')
  const [timezone, setTimezone] = useState(config?.timezone ?? 'Africa/Nairobi')
  const [selectedTheme, setSelectedTheme] = useState(config?.theme_name ?? 'ledger')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  if (!config) return null
  const savedConfig = config

  function previewTheme(themeName: string) {
    setSelectedTheme(themeName)
    applyTheme(themeName) // live preview, before saving
  }

  const MAX_LOGO_FILE_BYTES = 2 * 1024 * 1024 // 2MB raw -- comfortably under the
  // backend's stored-string limit even after base64's ~33% size inflation

  function handleLogoFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow choosing the exact same file again later
    if (!file) return

    setLogoError(null)

    if (!file.type.startsWith('image/')) {
      setLogoError("That file doesn't look like an image. Choose a PNG or JPG.")
      return
    }
    if (file.size > MAX_LOGO_FILE_BYTES) {
      setLogoError('That image is too large (max 2MB). Try a smaller or more compressed file.')
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setLogoUrl(reader.result)
      }
    }
    reader.onerror = () => {
      setLogoError('Could not read that file. Try a different image.')
    }
    reader.readAsDataURL(file)
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await configApi.update({
        business_name: businessName,
        slogan,
        logo_url: logoUrl || null,
        currency,
        timezone,
        theme_name: selectedTheme,
        local_backup_dir_override: localBackupDirOverride.trim() || null,
      })
      await refresh()
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save settings.')
      // Revert the live preview back to what's actually saved.
      applyTheme(savedConfig.theme_name)
      setSelectedTheme(savedConfig.theme_name)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="mb-1 font-display text-2xl text-ink">Business settings</h1>
      <p className="mb-6 text-sm text-ink-soft">
        Everything here is specific to this business -- name, logo, and look. Nothing about the
        system itself is tied to any one pharmacy.
      </p>

      <form onSubmit={handleSubmit} className="space-y-6">
        <section className="ledger-panel space-y-4 p-4">
          <h2 className="text-xs uppercase tracking-wide text-ink-soft">Identity</h2>
          <Field label="Business name">
            <input
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
              required
              className="w-full border border-rule bg-paper px-3 py-2 outline-none focus-visible:border-brass"
            />
          </Field>
          <Field label="Slogan">
            <input
              value={slogan}
              onChange={(e) => setSlogan(e.target.value)}
              placeholder="Shown under the name on the login screen"
              className="w-full border border-rule bg-paper px-3 py-2 outline-none focus-visible:border-brass"
            />
          </Field>
          <Field label="Logo">
            <div className="flex items-center gap-4">
              <div className="flex h-16 w-16 shrink-0 items-center justify-center border border-rule bg-paper">
                {logoUrl ? (
                  <img src={logoUrl} alt="" className="h-full w-full object-contain" />
                ) : (
                  <span className="text-2xl text-ink-soft" aria-hidden="true">
                    ℞
                  </span>
                )}
              </div>
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <label className="inline-block cursor-pointer border border-rule bg-paper px-3 py-1.5 text-sm hover:bg-panel">
                    Choose image…
                    <input
                      type="file"
                      accept="image/*"
                      onChange={handleLogoFileChange}
                      className="sr-only"
                    />
                  </label>
                  {logoUrl && (
                    <button
                      type="button"
                      onClick={() => setLogoUrl('')}
                      className="text-sm text-stamp-red underline decoration-dotted underline-offset-2"
                    >
                      Remove
                    </button>
                  )}
                </div>
                {logoError && (
                  <p role="alert" className="text-xs text-stamp-red">
                    {logoError}
                  </p>
                )}
                <p className="text-xs text-ink-soft">
                  PNG or JPG, square works best. Stored directly in the app -- no separate
                  file or web link needed.
                </p>
              </div>
            </div>
          </Field>
          <Field label="Currency code">
            <input
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
              maxLength={3}
              minLength={3}
              required
              placeholder="USD, KES, NGN..."
              className="w-32 border border-rule bg-paper px-3 py-2 uppercase outline-none focus-visible:border-brass"
            />
          </Field>
          <Field label="Timezone">
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              required
              className="w-full border border-rule bg-paper px-3 py-2 outline-none focus-visible:border-brass"
            >
              {/* If the currently saved value isn't one of the curated
                  cities below (an older install, or set some other
                  way), it still needs its own matching option here --
                  otherwise the <select> would silently fall back to
                  showing the first option as "selected" without that
                  actually being true, and saving the form would quietly
                  overwrite the real value with whatever that first
                  option happens to be. */}
              {!TIMEZONE_GROUPS.some((group) =>
                group.options.some((opt) => opt.timezone === timezone),
              ) && <option value={timezone}>{timezoneLabel(timezone)}</option>}
              {TIMEZONE_GROUPS.map((group) => (
                <optgroup key={group.region} label={group.region}>
                  {group.options.map((opt) => (
                    <option key={opt.timezone} value={opt.timezone}>
                      {opt.city}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <p className="mt-1 text-xs text-ink-soft">
              Used to decide what counts as "today" for expiry checks, reports, and the audit
              log -- always your own local day, not the server's.
            </p>
          </Field>
        </section>

        <section className="ledger-panel p-4">
          <h2 className="mb-3 text-xs uppercase tracking-wide text-ink-soft">Theme</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.values(THEMES).map((theme) => (
              <button
                key={theme.name}
                type="button"
                onClick={() => previewTheme(theme.name)}
                className={`border p-3 text-left ${
                  selectedTheme === theme.name ? 'border-brass ring-1 ring-brass' : 'border-rule'
                }`}
                style={{ background: theme.vars['--color-paper'], color: theme.vars['--color-ink'] }}
              >
                <div className="mb-2 flex gap-1">
                  {[theme.vars['--color-brass'], theme.vars['--color-stamp-green'], theme.vars['--color-stamp-red']].map(
                    (color, i) => (
                      <span
                        key={i}
                        className="h-4 w-4 rounded-full border"
                        style={{ background: color, borderColor: theme.vars['--color-rule'] }}
                      />
                    ),
                  )}
                </div>
                <p className="text-sm font-medium">{theme.label}</p>
                <p className="mt-0.5 text-xs opacity-70">{theme.description}</p>
              </button>
            ))}
          </div>
        </section>

        {error && (
          <p role="alert" className="border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
            {error}
          </p>
        )}
        {saved && (
          <p className="border border-stamp-green-soft bg-stamp-green-soft/40 px-3 py-2 text-sm text-stamp-green">
            Saved.
          </p>
        )}

        <section className="ledger-panel p-4">
          <h2 className="mb-1 text-xs uppercase tracking-wide text-ink-soft">
            Local backup location
          </h2>
          <p className="mb-3 text-xs text-ink-soft">
            Leave blank to use the default (next to this computer's own data) -- which will not
            survive this computer being lost, stolen, or damaged. Point this at a USB drive,
            external disk, or network folder instead so a real disaster doesn't take your backups
            with it.
          </p>
          <input
            value={localBackupDirOverride}
            onChange={(e) => setLocalBackupDirOverride(e.target.value)}
            placeholder="e.g. D:\PharmacyBackups or \\server\share\backups"
            className="w-full border border-rule bg-paper px-3 py-2 outline-none focus-visible:border-brass"
          />
        </section>

        <button
          type="submit"
          disabled={saving}
          className="border border-ink bg-ink px-5 py-2 font-medium text-paper disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save settings'}
        </button>
      </form>

      <section className="ledger-panel mt-6 space-y-3 p-4">
        <h2 className="text-xs uppercase tracking-wide text-ink-soft">Software updates</h2>
        <UpdateSection />
      </section>
    </div>
  )
}

function UpdateSection() {
  const { info, checking, checkNow } = useUpdateCheck()

  return (
    <div className="flex items-center justify-between text-sm">
      <div>
        {info ? (
          <p>
            A newer version is available:{' '}
            <span className="figure">
              {info.currentVersion} → {info.latestVersion}
            </span>
          </p>
        ) : (
          <p className="text-ink-soft">You're on the latest version, or none is known yet.</p>
        )}
      </div>
      <div className="flex gap-2">
        {info && info.downloadUrl && window.electronAPI?.downloadUpdateInstaller ? (
          // Routed through Electron's own download manager rather than
          // a plain link -- this is what lets main.js's will-download
          // handler offer to install it automatically once the
          // download finishes, instead of leaving the person to find
          // the installer in their Downloads folder and run it
          // themselves. Falls through to the plain link below whenever
          // this isn't available (a plain browser during development,
          // or no direct installer asset on the release), which is
          // exactly the same fallback pattern already used for silent
          // receipt printing.
          <button
            onClick={() => void window.electronAPI?.downloadUpdateInstaller(info.downloadUrl!)}
            className="border border-ink bg-ink px-3 py-1.5 text-paper"
          >
            Download &amp; install update
          </button>
        ) : (
          info && (
            <a
              href={info.downloadUrl ?? info.releaseUrl}
              target="_blank"
              rel="noreferrer"
              className="border border-ink bg-ink px-3 py-1.5 text-paper"
            >
              {info.downloadUrl ? 'Download update' : 'View release'}
            </a>
          )
        )}
        <button
          onClick={() => void checkNow()}
          disabled={checking}
          className="border border-rule px-3 py-1.5 text-ink-soft hover:border-brass disabled:opacity-50"
        >
          {checking ? 'Checking…' : 'Check for updates'}
        </button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs uppercase tracking-wide text-ink-soft">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}
