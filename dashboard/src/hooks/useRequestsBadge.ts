import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../lib/api'
import { can, type Role } from '../lib/roles'
import type { RequestsSummary } from '../lib/types'
import { useLiveRefresh } from './useLiveRefresh'

export const REQUESTS_POLL_MS = 30000

/**
 * The open-request total behind the admin rail's Requests pill: polled
 * every 30 s and refetched on `control` events, which every request
 * mutation writes. Non-admins get undefined and no traffic at all -- the
 * endpoint is admin-only and the events socket refuses investors.
 */
export function useRequestsBadge(orgId: number, role: Role): number | undefined {
  const admin = can(role, 'control')
  const [total, setTotal] = useState<number | undefined>(undefined)
  // Layout stays mounted across an org switch; an answer for the previous
  // org must not land on the new one's pill.
  const currentOrg = useRef(orgId)
  currentOrg.current = orgId

  const refetch = useCallback(() => {
    if (!admin) return
    const forOrg = orgId
    orgApi<RequestsSummary>(orgId, 'requests/summary').then(
      (s) => {
        if (currentOrg.current !== forOrg) return
        setTotal(typeof s?.total === 'number' ? s.total : undefined)
      },
      () => {
        // The pill is a hint, not a record: a failed poll leaves it as it was.
      },
    )
  }, [orgId, admin])

  useEffect(() => {
    setTotal(undefined)
    if (!admin) return
    refetch()
    const timer = window.setInterval(refetch, REQUESTS_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refetch, admin])

  useLiveRefresh(refetch, admin ? orgId : null)

  return admin ? total : undefined
}
