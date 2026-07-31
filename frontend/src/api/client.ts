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
  if (Array.isArray(body.detail)) return body.detail.map((e) => e.msg).join('; ')
  return body.detail.message
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

async function rawUpload<T>(
  path: string,
  file: File,
  extraFields?: Record<string, string>,
): Promise<T> {
  const headers: Record<string, string> = {}
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`
  // Deliberately no Content-Type here -- the browser sets multipart/
  // form-data with the correct boundary itself; setting it manually
  // breaks the upload.

  const formData = new FormData()
  formData.append('file', file)
  for (const [key, value] of Object.entries(extraFields ?? {})) {
    formData.append(key, value)
  }

  const res = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: formData,
  })

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

export async function uploadFile<T>(
  path: string,
  file: File,
  extraFields?: Record<string, string>,
): Promise<T> {
  try {
    return await rawUpload<T>(path, file, extraFields)
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      const refreshed = await doRefresh()
      if (refreshed) {
        return await rawUpload<T>(path, file, extraFields)
      }
    }
    throw err
  }
}

/**
 * Excel/PDF exports return a binary file with a real filename in
 * Content-Disposition, not JSON -- the generic `api` client (which
 * always parses JSON) doesn't fit here. This does its own fetch,
 * reuses the same in-memory access token, and triggers a normal
 * browser download via a throwaway object URL. Shared by every
 * export-capable list (Reports, Products, Customers, Audit Trail),
 * not reports-specific despite where it was originally written.
 */
async function fetchAndDownload(path: string, fallbackFilename: string): Promise<void> {
  const headers: Record<string, string> = {}
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(path, { headers, credentials: 'include' })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) message = body.detail
    } catch {
      // response wasn't JSON -- keep the statusText fallback
    }
    throw new ApiError(res.status, message, null)
  }

  const disposition = res.headers.get('Content-Disposition') ?? ''
  const filenameMatch = /filename="?([^"]+)"?/.exec(disposition)
  const filename = filenameMatch?.[1] ?? fallbackFilename

  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}

export async function downloadExport(
  path: string,
  query: Record<string, string | number>,
  format: 'excel' | 'pdf',
): Promise<void> {
  const url = new URL(`/api/v1${path}`, window.location.origin)
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, String(value))
  }
  url.searchParams.set('export', format)
  await fetchAndDownload(url.pathname + url.search, `export.${format === 'excel' ? 'xlsx' : 'pdf'}`)
}

export async function downloadFile(path: string, fallbackFilename: string): Promise<void> {
  await fetchAndDownload(`/api/v1${path}`, fallbackFilename)
}

export async function fetchBlob(path: string): Promise<Blob> {
  const headers: Record<string, string> = {}
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`/api/v1${path}`, { headers, credentials: 'include' })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) message = body.detail
    } catch {
      // response wasn't JSON -- keep the statusText fallback
    }
    throw new ApiError(res.status, message, null)
  }
  return res.blob()
}
