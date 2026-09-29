import { useEffect, useState } from 'react'

/**
 * The "hide balances" switch: one flag every Money on the page follows,
 * persisted per browser so it survives a reload (localStorage
 * `mf.hideBalances`, '1' hidden / '0' shown). A module store like
 * settingsBus: writers call setHidden, readers mount useHiddenBalances.
 * When storage is unavailable (private mode) the choice still holds for
 * the life of the page through `memory`.
 */
export const HIDE_KEY = 'mf.hideBalances'

type Listener = (hidden: boolean) => void

const listeners = new Set<Listener>()
let memory = false

export function readHidden(): boolean {
  try {
    const stored = localStorage.getItem(HIDE_KEY)
    if (stored !== null) return stored === '1'
  } catch {
    // Storage blocked: fall through to the in-memory value.
  }
  return memory
}

export function setHidden(v: boolean): void {
  memory = v
  try {
    localStorage.setItem(HIDE_KEY, v ? '1' : '0')
  } catch {
    // Private mode or a full store: the page still honours the choice.
  }
  listeners.forEach((l) => l(v))
}

export function useHiddenBalances(): [boolean, (v: boolean) => void] {
  const [hidden, set] = useState<boolean>(readHidden)
  useEffect(() => {
    listeners.add(set)
    set(readHidden())
    // Another tab flipping the switch reaches this one through the storage event.
    const onStorage = (e: StorageEvent) => { if (e.key === HIDE_KEY) set(readHidden()) }
    window.addEventListener('storage', onStorage)
    return () => {
      listeners.delete(set)
      window.removeEventListener('storage', onStorage)
    }
  }, [])
  return [hidden, setHidden]
}
