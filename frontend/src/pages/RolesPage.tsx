import { useEffect, useState, type FormEvent } from 'react'
import { rolesApi } from '../api/roles'
import { ApiError } from '../api/client'
import type { PermissionOut, RoleDetailOut } from '../types/api'

export function RolesPage() {
  const [roles, setRoles] = useState<RoleDetailOut[]>([])
  const [permissions, setPermissions] = useState<PermissionOut[]>([])
  const [selected, setSelected] = useState<RoleDetailOut | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([rolesApi.listRoles(), rolesApi.listPermissions()])
      .then(([roleList, permList]) => {
        if (cancelled) return
        setRoles(roleList)
        setPermissions(permList)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Could not load roles.')
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  function refresh() {
    setSelected(null)
    setCreating(false)
    setReloadKey((k) => k + 1)
  }

  return (
    <div className="p-6">
      <header className="mb-2 flex items-center justify-between">
        <h1 className="font-display text-2xl text-ink">Roles &amp; Permissions</h1>
        <button
          onClick={() => setCreating(true)}
          className="border border-ink bg-ink px-3 py-1.5 text-sm text-paper"
        >
          New role
        </button>
      </header>
      <p className="mb-6 text-sm text-ink-soft">
        Nothing here is fixed in code. Rename a role, change what it can do, or define a
        brand-new one -- it takes effect immediately for everyone who has it.
      </p>

      {error && (
        <p role="alert" className="mb-4 border border-stamp-red-soft bg-stamp-red-soft/40 px-3 py-2 text-sm text-stamp-red">
          {error}
        </p>
      )}

      <div className="ledger-panel divide-y divide-rule">
        {roles.map((role) => (
          <button
            key={role.id}
            onClick={() => setSelected(role)}
            className="flex w-full items-center justify-between px-3 py-3 text-left text-sm hover:bg-panel"
          >
            <div>
              <p className="font-medium">
                {role.name}
                {role.is_system && (
                  <span className="ml-2 text-xs uppercase tracking-wide text-ink-soft">
                    built-in
                  </span>
                )}
              </p>
              <p className="text-xs text-ink-soft">
                {role.description || 'No description'} · {role.permissions.length} permission(s)
              </p>
            </div>
            <span className="figure text-xs text-ink-soft">{role.user_count} user(s)</span>
          </button>
        ))}
      </div>

      {(creating || selected) && (
        <RoleEditor
          role={selected}
          permissions={permissions}
          onClose={() => {
            setCreating(false)
            setSelected(null)
          }}
          onChanged={refresh}
        />
      )}
    </div>
  )
}

function groupByDomain(permissions: PermissionOut[]): [string, PermissionOut[]][] {
  const groups = new Map<string, PermissionOut[]>()
  for (const p of permissions) {
    const domain = p.code.split('.')[0]
    const list = groups.get(domain) ?? []
    list.push(p)
    groups.set(domain, list)
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
}

function RoleEditor({
  role,
  permissions,
  onClose,
  onChanged,
}: {
  role: RoleDetailOut | null
  permissions: PermissionOut[]
  onClose: () => void
  onChanged: () => void
}) {
  const [name, setName] = useState(role?.name ?? '')
  const [description, setDescription] = useState(role?.description ?? '')
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(
    new Set(role?.permissions ?? []),
  )
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)

  function toggle(code: string) {
    setSelectedCodes((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const payload = {
        name,
        description,
        permission_codes: [...selectedCodes],
      }
      if (role) {
        await rolesApi.update(role.id, payload)
      } else {
        await rolesApi.create(payload)
      }
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save role.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!role) return
    setDeleting(true)
    setError(null)
    try {
      await rolesApi.delete(role.id)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete role.')
      setDeleting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto border border-rule bg-paper p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-lg text-ink">{role ? 'Edit role' : 'New role'}</h2>
          <button onClick={onClose} aria-label="Close" className="text-ink-soft">
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">Name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Description
            </span>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-1 w-full border border-rule bg-paper px-3 py-2"
            />
          </label>

          <div>
            <span className="block text-xs uppercase tracking-wide text-ink-soft">
              Permissions ({selectedCodes.size} selected)
            </span>
            <div className="mt-2 max-h-64 space-y-3 overflow-y-auto border border-rule p-3">
              {groupByDomain(permissions).map(([domain, perms]) => (
                <div key={domain}>
                  <p className="mb-1 text-xs font-medium uppercase text-brass">{domain}</p>
                  {perms.map((p) => (
                    <label key={p.code} className="flex items-start gap-2 py-0.5 text-sm">
                      <input
                        type="checkbox"
                        checked={selectedCodes.has(p.code)}
                        onChange={() => toggle(p.code)}
                        className="mt-0.5"
                      />
                      <span>
                        <span className="figure">{p.code}</span>{' '}
                        <span className="text-ink-soft">-- {p.description}</span>
                      </span>
                    </label>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {error && <p className="text-sm text-stamp-red">{error}</p>}

          <div className="flex items-center justify-between">
            {role && !role.is_system && (
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={deleting || role.user_count > 0}
                title={role.user_count > 0 ? 'Reassign users off this role first' : undefined}
                className="text-sm text-stamp-red underline decoration-dotted disabled:opacity-40"
              >
                {deleting ? 'Deleting…' : 'Delete role'}
              </button>
            )}
            {role?.is_system && (
              <span className="text-xs text-ink-soft">Built-in role -- can't be deleted</span>
            )}
            <div className="flex gap-2">
              <button type="button" onClick={onClose} className="border border-rule px-4 py-2 text-sm">
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50"
              >
                {saving ? 'Saving…' : 'Save role'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
