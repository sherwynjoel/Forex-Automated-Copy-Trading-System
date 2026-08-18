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
 * Make an API request with CSRF protection and automatic redirect on 401
 * (except for /api/login where 401 is propagated to the caller)
 */
export async function api<T>(
  path: string,
  init?: RequestInit
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

  // Redirect to login on 401, but only for non-login endpoints
  // (login endpoint 401 should propagate as inline error)
  if (response.status === 401 && path !== '/api/login') {
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  // Throw on non-2xx, surfacing the server's `detail` when the body has one
  // (status prefix kept so callers that pattern-match on the status code —
  // e.g. Join.tsx's `.includes('410')` — keep working).
  if (!response.ok) {
    let detail: string | undefined
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Body wasn't JSON (or was empty) — fall back to the bare status.
    }
    throw new Error(detail ? `${response.status}: ${detail}` : `${response.status}`)
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
 * Create a WebSocket connection to the events stream, scoped to an org when
 * one is given. `orgId` is optional so pages not yet converted to org-scoped
 * routing (Task 17) keep connecting to the unscoped stream unchanged.
 */
export function eventsSocket(orgId?: number): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const query = orgId != null ? `?org_id=${orgId}` : ''
  const url = `${protocol}//${window.location.host}/api/ws${query}`
  return new WebSocket(url)
}
