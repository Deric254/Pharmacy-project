import { useEffect, useState, type FormEvent } from 'react'
import { backupsApi } from '../api/backups'
import { ApiError } from '../api/client'
import { Modal } from '../components/Modal'
import type { BackupLogOut, RestoreResult } from '../types/api'

export function BackupsPage() {
  const [backups, setBackups] = useState<BackupLogOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [restoreTarget, setRestoreTarget] = useState<BackupLogOut | null>(null)
  const [showConnect, setShowConnect] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    backupsApi
      .list()
      .then((list) => {
        if (cancelled) return
        setBackups(list)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load backup history.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  async function handleRunBackup() {
    setRunning(true)
    setError(null)
    try {
      await backupsApi.run()
      setReloadKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Backup failed to run.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="p-6">
      <header className="mb-2 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Backups</h1>
        <div className="flex gap-2">
          <button
            onClick={() => void handleRunBackup()}
            disabled={running}
            className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-50"
          >
            {running ? 'Backing up…' : 'Back up now'}
          </button>
          <button
            onClick={() => setShowConnect(true)}
            className="border border-rule px-3 py-1.5 text-sm text-ink-soft hover:border-brass"
          >
            Also back up to Google Drive…
          </button>
        </div>
      </header>
      <p className="mb-6 text-sm text-ink-soft">
        Every sale, every batch, every coin recorded in this system lives in one database. A
        backup is the only thing standing between a hard-drive failure and losing all of it.
        "Back up now" saves a copy on this computer immediately — no setup, no internet needed.
        Connecting Google Drive is entirely optional, for anyone who also wants an off-site copy.
      </p>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="ledger-panel divide-y divide-rule">
        {backups.map((b) => (
          <div key={b.id} className="flex items-center justify-between px-3 py-3 text-sm">
            <div>
              <p className="flex items-center gap-2 font-medium">
                {new Date(b.created_at).toLocaleString()}
                <StatusBadge status={b.status} />
              </p>
              <p className="text-xs text-ink-soft">
                {b.provider === 'LOCAL_FILE' ? 'On this computer' : 'Google Drive'}
                {b.size_bytes !== null && ` · ${formatBytes(b.size_bytes)}`}
                {b.restored_at && ` · restored ${new Date(b.restored_at).toLocaleString()}`}
              </p>
              {b.error_message && (
                <p className="mt-1 text-xs text-stamp-red">{b.error_message}</p>
              )}
            </div>
            {b.status === 'SUCCESS' && (
              <button
                onClick={() => setRestoreTarget(b)}
                className="text-xs text-stamp-red underline decoration-dotted"
              >
                Restore
              </button>
            )}
          </div>
        ))}
        {backups.length === 0 && !loading && (
          <p className="px-3 py-4 text-sm text-ink-soft">
            No backups yet. Run one now — it only takes a moment.
          </p>
        )}
      </div>

      {restoreTarget && (
        <ConfirmRestoreModal
          backup={restoreTarget}
          onClose={() => setRestoreTarget(null)}
          onRestored={() => {
            setRestoreTarget(null)
            setReloadKey((k) => k + 1)
          }}
        />
      )}

      {showConnect && (
        <ConnectGoogleDriveModal
          onClose={() => setShowConnect(false)}
          onConnected={() => setShowConnect(false)}
        />
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: BackupLogOut['status'] }) {
  if (status === 'SUCCESS') {
    return <span className="text-xs uppercase tracking-wide text-stamp-green">succeeded</span>
  }
  return <span className="text-xs uppercase tracking-wide text-stamp-red">failed</span>
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function ConfirmRestoreModal({
  backup,
  onClose,
  onRestored,
}: {
  backup: BackupLogOut
  onClose: () => void
  onRestored: () => void
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<RestoreResult | null>(null)

  async function handleRestore() {
    setBusy(true)
    setError(null)
    try {
      const outcome = await backupsApi.restore(backup.id, true)
      setResult(outcome)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Restore failed.')
      setBusy(false)
    }
  }

  if (result) {
    return (
      <Modal title="Restore complete" onClose={onRestored}>
        <p className="text-sm text-ink-soft">
          Restored <span className="figure text-ink">{result.tables_restored}</span> table(s),{' '}
          <span className="figure text-ink">{result.total_rows_restored}</span> row(s) total.
        </p>
        <p className="mt-2 text-sm">
          {result.manifest_matched ? (
            <span className="text-stamp-green">
              Backup contents matched their recorded manifest exactly.
            </span>
          ) : (
            <span className="text-stamp-red">
              Warning: the restored data didn't match its recorded manifest. Review carefully.
            </span>
          )}
        </p>
        <button
          onClick={onRestored}
          className="mt-4 w-full border border-ink bg-ink py-2 text-sm text-paper"
        >
          Done
        </button>
      </Modal>
    )
  }

  return (
    <Modal title="Restore from this backup?" onClose={onClose}>
      <div className="border border-stamp-red-soft bg-stamp-red-soft/30 p-3 text-sm text-stamp-red">
        <p className="font-medium">This overwrites the live database right now.</p>
        <p className="mt-1">
          Everything recorded since {new Date(backup.created_at).toLocaleString()} — every sale,
          refund, adjustment, and stock take — will be gone. This cannot be undone. Only do this
          if you're certain the current data is wrong and this backup is correct.
        </p>
      </div>

      <label className="mt-4 flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
          className="mt-0.5"
        />
        <span>I understand this replaces all current data and cannot be undone.</span>
      </label>

      {error && <p className="mt-3 text-sm text-stamp-red">{error}</p>}

      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          onClick={() => void handleRestore()}
          disabled={!acknowledged || busy}
          className="border border-stamp-red bg-stamp-red px-4 py-2 text-sm text-paper disabled:opacity-40"
        >
          {busy ? 'Restoring…' : 'Restore now'}
        </button>
      </div>
    </Modal>
  )
}

function ConnectGoogleDriveModal({
  onClose,
  onConnected,
}: {
  onClose: () => void
  onConnected: () => void
}) {
  const [refreshToken, setRefreshToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await backupsApi.connectGoogleDrive(refreshToken)
      onConnected()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not connect Google Drive.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Connect Google Drive" onClose={onClose}>
      <p className="mb-3 text-sm text-ink-soft">
        Backups upload to a Google Drive folder this account controls. This needs a Google OAuth
        refresh token with Drive access, obtained once and pasted in below — the same
        bring-your-own-credential pattern as the AI Assistant's provider keys. Whoever set up
        this deployment's Google OAuth client ID/secret should have this, or can generate one via{' '}
        <a
          href="https://developers.google.com/oauthplayground/"
          target="_blank"
          rel="noreferrer"
          className="underline"
        >
          Google's OAuth Playground
        </a>{' '}
        with the Drive API scope selected.
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Refresh token
          </span>
          <input
            type="password"
            value={refreshToken}
            onChange={(e) => setRefreshToken(e.target.value)}
            required
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>

        {error && <p className="text-sm text-stamp-red">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="border border-rule px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
          >
            {submitting ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
