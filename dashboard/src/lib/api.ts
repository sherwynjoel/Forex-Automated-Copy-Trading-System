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

  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers,
  })

  // Redirect to login on 401
  if (response.status === 401) {
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  // Throw on non-2xx
  if (!response.ok) {
    throw new Error(`${response.status}`)
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
 * Create a WebSocket connection to the events stream
 */
export function eventsSocket(): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}/api/ws`
  return new WebSocket(url)
}
