import { useEffect, useState, type FormEvent } from 'react'
import { usersApi } from '../api/users'
import { useAuthStore } from '../auth/store'
import { ApiError } from '../api/client'
import { Modal } from '../components/Modal'
import type { RoleOut, UserListItemOut } from '../types/api'

export function UsersPage() {
  const currentUser = useAuthStore((s) => s.user)
  const [users, setUsers] = useState<UserListItemOut[]>([])
  const [roles, setRoles] = useState<RoleOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [confirmDeactivate, setConfirmDeactivate] = useState<UserListItemOut | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([usersApi.list(), usersApi.listRoles()])
      .then(([userList, roleList]) => {
        if (cancelled) return
        setUsers(userList)
        setRoles(roleList)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load users.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  function refresh() {
    setShowCreate(false)
    setConfirmDeactivate(null)
    setReloadKey((k) => k + 1)
  }

  return (
    <div className="p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Staff accounts</h1>
        <button
          onClick={() => setShowCreate(true)}
          disabled={roles.length === 0}
          className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-40"
        >
          New staff account
        </button>
      </header>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="ledger-panel divide-y divide-rule">
        {users.map((u) => (
          <div key={u.id} className="flex items-center justify-between px-3 py-3 text-sm">
            <div>
              <p className="font-medium">
                {u.full_name}
                {!u.is_active && (
                  <span className="ml-2 text-xs uppercase tracking-wide text-stamp-red">
                    deactivated
                  </span>
                )}
              </p>
              <p className="text-xs text-ink-soft">
                @{u.username} · {u.role_name}
              </p>
            </div>
            {u.is_active && u.id !== currentUser?.id && (
              <button
                onClick={() => setConfirmDeactivate(u)}
                className="text-xs text-stamp-red underline decoration-dotted"
              >
                Deactivate
              </button>
            )}
            {u.id === currentUser?.id && (
              <span className="text-xs text-ink-soft">This is you</span>
            )}
          </div>
        ))}
        {users.length === 0 && !loading && (
          <p className="px-3 py-4 text-sm text-ink-soft">No staff accounts yet.</p>
        )}
      </div>

      {showCreate && (
        <CreateUserModal roles={roles} onClose={() => setShowCreate(false)} onCreated={refresh} />
      )}

      {confirmDeactivate && (
        <ConfirmDeactivateModal
          user={confirmDeactivate}
          onClose={() => setConfirmDeactivate(null)}
          onConfirmed={refresh}
        />
      )}
    </div>
  )
}

function CreateUserModal({
  roles,
  onClose,
  onCreated,
}: {
  roles: RoleOut[]
  onClose: () => void
  onCreated: () => void
}) {
  const [fullName, setFullName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [roleId, setRoleId] = useState(roles[0]?.id ?? 0)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await usersApi.create({ full_name: fullName, username, password, role_id: roleId })
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the account.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="New staff account" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Full name</span>
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            required
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={3}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">
            Temporary password
          </span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="block text-xs uppercase tracking-wide text-ink-soft">Role</span>
          <select
            value={roleId}
            onChange={(e) => setRoleId(Number(e.target.value))}
            className="mt-1 w-full border border-rule bg-paper px-3 py-2"
          >
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
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
            {submitting ? 'Creating…' : 'Create account'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function ConfirmDeactivateModal({
  user,
  onClose,
  onConfirmed,
}: {
  user: UserListItemOut
  onClose: () => void
  onConfirmed: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleConfirm() {
    setBusy(true)
    setError(null)
    try {
      await usersApi.deactivate(user.id)
      onConfirmed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not deactivate this account.')
      setBusy(false)
    }
  }

  return (
    <Modal title="Deactivate this account?" onClose={onClose}>
      <p className="text-sm text-ink-soft">
        <span className="font-medium text-ink">{user.full_name}</span> (@{user.username}) will no
        longer be able to log in. This doesn't delete their history — every sale, refund, or
        adjustment they made stays on record exactly as it happened.
      </p>
      {error && <p className="mt-3 text-sm text-stamp-red">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="border border-rule px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          onClick={() => void handleConfirm()}
          disabled={busy}
          className="border border-stamp-red bg-stamp-red px-4 py-2 text-sm text-paper disabled:opacity-50"
        >
          {busy ? 'Deactivating…' : 'Deactivate'}
        </button>
      </div>
    </Modal>
  )
}
