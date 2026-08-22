import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'mf.theme'
// Keep in sync with --color-paper in index.css for both themes.
const PAPER = { light: '#f8f8fa', dark: '#131118' } as const

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

/** Day/dark theme with a persisted toggle. The document attribute is the
 * single source of truth; index.html applies it before first paint. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = initialTheme()
    apply(t)
    return t
  })

  // Stay in step with the outside world: another tab toggling (storage
  // event), or the OS flipping while the user never chose explicitly.
  useEffect(() => {
    const follow = (next: Theme) => {
      apply(next)
      setTheme(next)
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return
      if (e.newValue === 'dark' || e.newValue === 'light') follow(e.newValue)
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
    let mq: MediaQueryList | null = null
    try {
      if (typeof matchMedia === 'function') {
        mq = matchMedia('(prefers-color-scheme: dark)')
        mq.addEventListener?.('change', onOsChange)
      }
    } catch { /* no matchMedia */ }
    return () => {
      window.removeEventListener('storage', onStorage)
      mq?.removeEventListener?.('change', onOsChange)
    }
  }, [])

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === 'dark' ? 'light' : 'dark'
      apply(next)
      try {
        localStorage.setItem(STORAGE_KEY, next)
      } catch { /* private mode */ }
      return next
    })
  }, [])

  return { theme, toggle }
}
