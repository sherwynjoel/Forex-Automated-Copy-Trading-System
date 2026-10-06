import { api } from './api'
import type { ThemePref, UserSettings } from './types'

export const THEME_PREFS: ThemePref[] = ['light', 'dim', 'dark', 'system']

export function isThemePref(v: unknown): v is ThemePref {
  return typeof v === 'string' && (THEME_PREFS as string[]).includes(v)
}

/** This browser's explicit choice (what index.html painted), or null while
 *  it follows the OS. The stored palette 'dark' is the Dim preference. */
export function localPref(): ThemePref | null {
  try {
    const stored = localStorage.getItem('mf.theme')
    if (stored === 'light') return 'light'
    if (stored === 'dark') return 'dim'
  } catch { /* private mode */ }
  return null
}

/** Write the choice to the account. Best effort: the browser already has
 *  it, and the next change tries again. */
export async function saveThemePref(pref: ThemePref): Promise<void> {
  try {
    await api<UserSettings>('/api/me/settings', { method: 'PUT', body: JSON.stringify({ theme: pref }) })
  } catch { /* see above */ }
}

/** After sign-in: paint the account's theme. An account that never saved
 *  one (`updated_at` null) is left alone -- this browser keeps whatever it
 *  is already showing, and nothing is written back. A browser can be
 *  shared: writing this browser's local look to a server that has never
 *  saved one would hand a PREVIOUS user's choice to whichever account
 *  signs in next. The account only gets a theme once its own user picks
 *  one in Settings. */
export async function syncThemeFromServer(choose: (pref: ThemePref) => void): Promise<void> {
  let s: Partial<UserSettings> | undefined
  try {
    s = await api<UserSettings>('/api/me/settings')
  } catch {
    return
  }
  if (!s || !isThemePref(s.theme)) return
  if (s.updated_at == null) return
  choose(s.theme)
}
