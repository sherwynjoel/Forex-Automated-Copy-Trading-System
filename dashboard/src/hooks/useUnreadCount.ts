import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../lib/api'

export const UNREAD_POLL_MS = 10000

/**
 * The caller's unread notification count in this org, polled every 10 s
 * (the portal's usual cadence); undefined until the first answer. `refresh`
 * asks again at once, after a mark-read. Every member may ask, so there is
 * no role gate. Every poll and refresh bumps a `seq` ref and only the
 * newest answer lands: an earlier poll that answers after a later refresh
 * is dropped, and so is any answer for the previous org (Layout stays
 * mounted across an org switch).
 */
export function useUnreadCount(orgId: number): { count: number | undefined; refresh: () => void } {
  const [count, setCount] = useState<number | undefined>(undefined)
  const seq = useRef(0)

  const refresh = useCallback(() => {
    const mine = ++seq.current
    orgApi<{ count?: unknown }>(orgId, 'notifications/unread-count').then(
      (r) => {
        if (mine !== seq.current) return
        setCount(typeof r?.count === 'number' ? r.count : undefined)
      },
      () => {
        // A hint, not a record: a failed poll leaves the badge as it was.
      },
    )
  }, [orgId])

  useEffect(() => {
    setCount(undefined)
    refresh()
    const timer = window.setInterval(refresh, UNREAD_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  return { count, refresh }
}
