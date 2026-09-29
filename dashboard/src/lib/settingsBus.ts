import { useEffect, useState } from 'react'
import type { Settings } from './types'

type Listener = (s: Settings | null) => void

let current: Settings | null = null
const listeners = new Set<Listener>()

/** The latest org settings anyone fetched or saved; the desk strip writes, pages read. */
export function publishSettings(s: Settings | null): void {
  current = s
  listeners.forEach((l) => l(s))
}

export function readSettings(): Settings | null {
  return current
}

export function useSharedSettings(): Settings | null {
  const [s, setS] = useState<Settings | null>(current)
  useEffect(() => {
    listeners.add(setS)
    setS(current)
    return () => { listeners.delete(setS) }
  }, [])
  return s
}
