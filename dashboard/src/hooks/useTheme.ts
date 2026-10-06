import { useCallback, useEffect, useState } from 'react'
import type { ThemePref } from '../lib/types'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'mf.theme'
// Keep in sync with --color-paper in index.css for both themes.
const PAPER = { light: '#f4fafb', dark: '#0e1a1f' } as const
/** Same-tab broadcast between useTheme instances (the rail, the Settings page). */
const CHANGE_EVENT = 'mf-theme-change'

function systemTheme(): Theme {
  try {
    return typeof matchMedia === 'function'
      && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme
  // The browser chrome (mobile address bar) follows the page ground.
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', PAPER[theme])
}

function initialTheme(): Theme {
  // index.html sets data-theme pre-paint; trust it, then storage, then OS.
  const set = document.documentElement.dataset.theme
  if (set === 'dark' || set === 'light') return set
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark' || stored === 'light') return stored
  } catch { /* private mode */ }
  return systemTheme()
}

/** The palette a preference paints. Dim and dark are the one night palette
 *  (ponytail: there is no separate, darker palette yet); system follows the OS. */
export function paletteFor(pref: ThemePref): Theme {
  if (pref === 'system') return systemTheme()
  return pref === 'light' ? 'light' : 'dark'
}

/** Day/night theme. The document attribute is the single source of truth;
 *  index.html paints localStorage's palette before first paint, so storage
 *  stays the first-paint cache: a palette for an explicit choice, absent
 *  while following the OS. `choose` applies a preference (the account's,
 *  from Settings or the server); `toggle` flips light <-> dim and returns the
 *  preference it chose so the caller can save it to the account. */
export function useTheme(): { theme: Theme; toggle: () => ThemePref; choose: (pref: ThemePref) => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = initialTheme()
    apply(t)
    return t
  })

  // Stay in step with the outside world: another tab (storage event),
  // another hook in this tab (CHANGE_EVENT), or the OS flipping while the
  // user follows it.
  useEffect(() => {
    const follow = (next: Theme) => {
      apply(next)
      setTheme(next)
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return
      if (e.newValue === 'dark' || e.newValue === 'light') follow(e.newValue)
    }
    const onChoose = (e: Event) => {
      const next = (e as CustomEvent<Theme>).detail
      if (next === 'dark' || next === 'light') setTheme(next)
    }
    const onOsChange = (e: { matches: boolean }) => {
      let stored: string | null = null
      try {
        stored = localStorage.getItem(STORAGE_KEY)
      } catch { /* private mode */ }
      if (stored === 'dark' || stored === 'light') return
      follow(e.matches ? 'dark' : 'light')
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener(CHANGE_EVENT, onChoose)
    let mq: MediaQueryList | null = null
    try {
      if (typeof matchMedia === 'function') {
        mq = matchMedia('(prefers-color-scheme: dark)')
        mq.addEventListener?.('change', onOsChange)
      }
    } catch { /* no matchMedia */ }
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(CHANGE_EVENT, onChoose)
      mq?.removeEventListener?.('change', onOsChange)
    }
  }, [])

  const choose = useCallback((pref: ThemePref) => {
    const next = paletteFor(pref)
    apply(next)
    try {
      if (pref === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, next)
    } catch { /* private mode */ }
    setTheme(next)
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: next }))
  }, [])

  const toggle = useCallback((): ThemePref => {
    const pref: ThemePref = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dim'
    choose(pref)
    return pref
  }, [choose])

  return { theme, toggle, choose }
}
