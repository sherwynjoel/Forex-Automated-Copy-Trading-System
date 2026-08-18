import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { api } from './api'
import type { Me, OrgSummary } from './types'

export const LAST_ORG_KEY = 'copydesk.lastOrg'

interface OrgContextValue {
  orgId: number
  role: OrgSummary['role']
  org: OrgSummary
  me: Me
  refreshMe: () => Promise<void>
}

const OrgContext = createContext<OrgContextValue | null>(null)

export function useOrg(): OrgContextValue {
  const value = useContext(OrgContext)
  if (!value) throw new Error('useOrg used outside OrgProvider')
  return value
}

/** Resolves :orgId against /api/me. Not logged in → api() redirects to
 * /login; logged in but not a member of :orgId → /welcome. */
export function OrgProvider({ children }: { children: React.ReactNode }) {
  const { orgId: rawOrgId } = useParams()
  const orgId = Number(rawOrgId)
  const [me, setMe] = useState<Me | null>(null)
  const [failed, setFailed] = useState(false)

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>('/api/me'))
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    refreshMe()
  }, [refreshMe])

  const org = me?.orgs.find((o) => o.id === orgId)

  useEffect(() => {
    if (org) localStorage.setItem(LAST_ORG_KEY, String(org.id))
  }, [org?.id])

  if (failed) return <Navigate to="/login" replace />
  if (!me) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }
  if (!org) return <Navigate to="/welcome" replace />
  return (
    <OrgContext.Provider
      value={{ orgId: org.id, role: org.role, org, me, refreshMe }}
    >
      {children}
    </OrgContext.Provider>
  )
}
