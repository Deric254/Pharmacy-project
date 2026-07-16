import { useState, type FormEvent, type ReactNode } from 'react'
import { useConfigStore } from '../config/store'
import { configApi } from '../api/config'
import { ApiError } from '../api/client'
import { THEMES, applyTheme } from '../theme/themes'

export function SettingsPage() {
  const config = useConfigStore((s) => s.config)
  const refresh = useConfigStore((s) => s.refresh)

  const [businessName, setBusinessName] = useState(config?.business_name ?? '')
  const [slogan, setSlogan] = useState(config?.slogan ?? '')
  const [logoUrl, setLogoUrl] = useState(config?.logo_url ?? '')
  const [currency, setCurrency] = useState(config?.currency ?? 'USD')
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
        theme_name: selectedTheme,
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
          <Field label="Logo URL">
            <input
              value={logoUrl}
              onChange={(e) => setLogoUrl(e.target.value)}
              placeholder="https://... (leave blank to use the default mark)"
              className="w-full border border-rule bg-paper px-3 py-2 outline-none focus-visible:border-brass"
            />
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

        <button
          type="submit"
          disabled={saving}
          className="border border-ink bg-ink px-5 py-2 font-medium text-paper disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save settings'}
        </button>
      </form>
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
