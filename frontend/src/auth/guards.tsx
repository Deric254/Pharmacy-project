import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from './store'
import { MustChangePasswordPage } from './MustChangePasswordPage'

export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status)
  const user = useAuthStore((s) => s.user)

  if (status === 'loading') {
    return (
      <div className="grid h-screen place-items-center bg-paper text-ink-soft">
        <p className="font-mono text-sm tracking-wide">opening the ledger…</p>
      </div>
    )
  }
  if (status === 'anonymous') {
    return <Navigate to="/login" replace />
  }
  if (user?.must_change_password) {
    return <MustChangePasswordPage />
  }
  return <>{children}</>
}

export function RequirePermission({
  code,
  children,
}: {
  code: string
  children: ReactNode
}) {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  if (!hasPermission(code)) {
    return (
      <div className="p-8">
        <p className="font-display text-lg text-ink">Not authorized</p>
        <p className="mt-1 text-sm text-ink-soft">
          Your role doesn't include the <span className="figure">{code}</span> permission. Ask an
          administrator if you believe this is wrong.
        </p>
      </div>
    )
  }
  return <>{children}</>
}
