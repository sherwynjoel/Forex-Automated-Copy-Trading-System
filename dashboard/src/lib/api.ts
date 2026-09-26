/** Every non-2xx throws one of these; `response` carries what the server said. */
export type ApiError = Error & { response?: { status: number; body?: Record<string, unknown> } }

/**
 * Get CSRF token from cookie
 */
function getCsrfToken(): string | null {
  const cookies = document.cookie.split('; ')
  for (const cookie of cookies) {
    const [name, value] = cookie.split('=')
    if (name === 'csrf') {
      return decodeURIComponent(value)
    }
  }
  return null
}

/**
 * Make an API request with CSRF protection and automatic redirect on 401.
 * A 401 sends the browser to /login, except: /api/login propagates its own
 * 401 as an inline error; a half session's 401 (server detail "MPIN
 * required") goes to /mpin instead of /login; and the MPIN routes
 * (/api/mpin/* and /api/me/mpin) own their 401s ("Invalid MPIN" etc.) as
 * inline errors, never a redirect. Callers that pass
 * `opts.redirectOn401: false` handle every 401 themselves.
 */
export async function api<T>(
  path: string,
  init?: RequestInit,
  opts?: { redirectOn401?: boolean },
): Promise<T> {
  const headers = { ...init?.headers } as Record<string, string>

  // Add CSRF token for mutations
  const method = (init?.method || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD') {
    const csrfToken = getCsrfToken()
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken
    }
  }

  // Add default Content-Type for requests with body
  if (init?.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers,
  })

  // A 401 means "go sign in" -- except when it means "finish signing in":
  // a half session (email+password done, MPIN owed) is told exactly that by
  // the server, and belongs on /mpin. The MPIN routes (/api/mpin/* and
  // /api/me/mpin, the full-session change-MPIN route) own their own 401s
  // ("Invalid MPIN" etc.) as inline errors, like /api/login's.
  if (response.status === 401 && path !== '/api/login' && path !== '/api/me/mpin'
      && !path.startsWith('/api/mpin/') && opts?.redirectOn401 !== false) {
    let detail: string | undefined
    try {
      const body = (await response.clone().json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // not JSON
    }
    window.location.href = detail === 'MPIN required' ? '/mpin' : '/login'
    throw new Error('Unauthorized')
  }

  // Throw on non-2xx, surfacing the server's `detail` when the body has one
  // (status prefix kept so callers that pattern-match on the status code —
  // e.g. Join.tsx's `.includes('410')` — keep working).
  if (!response.ok) {
    let detail: string | undefined
    let parsed: Record<string, unknown> | undefined
    try {
      parsed = (await response.json()) as Record<string, unknown>
      if (typeof parsed.detail === 'string') detail = parsed.detail
    } catch {
      // Body wasn't JSON (or was empty) — fall back to the bare status.
    }
    const error = new Error(detail ? `${response.status}: ${detail}` : `${response.status}`) as ApiError
    error.response = { status: response.status, body: parsed }
    throw error
  }

  // Parse JSON if there's content
  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    return response.json() as Promise<T>
  }

  return undefined as T
}

/**
 * Make an org-scoped API request: GET/POST/etc. against
 * /api/orgs/{orgId}/{tail}.
 */
export function orgApi<T>(orgId: number, tail: string, init?: RequestInit): Promise<T> {
  return api<T>(`/api/orgs/${orgId}/${tail}`, init)
}

/**
 * Create a WebSocket connection to the events stream, scoped to an org.
 */
export function eventsSocket(orgId: number): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}/api/ws?org_id=${orgId}`
  return new WebSocket(url)
}
