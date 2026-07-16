import type { ApiErrorBody } from '../types/api'

/**
 * Access token lives ONLY in this module-level variable -- never in
 * localStorage/sessionStorage, which are readable by any injected
 * script (XSS). It's lost on a hard refresh by design; the refresh
 * token (httpOnly cookie, invisible to JS) is what lets `bootstrap()`
 * silently re-establish a session on page load instead.
 */
let accessToken: string | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

export class ApiError extends Error {
  status: number
  body: ApiErrorBody | null

  constructor(status: number, message: string, body: ApiErrorBody | null) {
    super(message)
    this.status = status
    this.body = body
  }
}

function extractMessage(body: ApiErrorBody | null, fallback: string): string {
  if (!body?.detail) return fallback
  if (typeof body.detail === 'string') return body.detail
  return body.detail.map((e) => e.msg).join('; ')
}

// Multiple requests can 401 at the same moment (e.g. a page that fires
// five requests on mount right as the access token expires). Without
// this, each one would race to refresh independently, and the server's
// one-time-use refresh-token rotation means only the first would
// succeed -- the rest would get a used-up token and force a real
// logout. Coalescing to a single in-flight refresh fixes that.
let refreshInFlight: Promise<boolean> | null = null

async function doRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch('/api/v1/auth/refresh', {
          method: 'POST',
          credentials: 'include',
        })
        if (!res.ok) return false
        const body = (await res.json()) as { access_token: string }
        setAccessToken(body.access_token)
        return true
      } catch {
        return false
      } finally {
        refreshInFlight = null
      }
    })()
  }
  return refreshInFlight
}

interface RequestOptions {
  method?: string
  body?: unknown
  query?: Record<string, string | number | boolean | undefined>
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`/api/v1${path}`, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value))
    }
  }
  return url.pathname + url.search
}

async function rawRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  const res = await fetch(buildUrl(path, options.query), {
    method: options.method ?? 'GET',
    headers,
    credentials: 'include',
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })

  if (res.status === 204) return undefined as T

  let parsedBody: ApiErrorBody | null = null
  const text = await res.text()
  if (text) {
    try {
      parsedBody = JSON.parse(text)
    } catch {
      parsedBody = null
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, extractMessage(parsedBody, res.statusText), parsedBody)
  }
  return parsedBody as T
}

/** Core request function: on a 401, tries exactly one silent refresh
 * and retries the original request once. Two failures in a row means
 * the session is genuinely over -- surface the error, don't loop. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  try {
    return await rawRequest<T>(path, options)
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && path !== '/auth/refresh') {
      const refreshed = await doRefresh()
      if (refreshed) {
        return await rawRequest<T>(path, options)
      }
    }
    throw err
  }
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) =>
    apiRequest<T>(path, { method: 'GET', query }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => apiRequest<T>(path, { method: 'DELETE' }),
}
