import { useCallback, useEffect, useState } from 'react'
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { api, orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can, type Role } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import type { Account, ApiState, CloseAllResult, Settings } from '../lib/types'
import ConfirmDialog from './ConfirmDialog'

const navItems = (orgId: number, role: Role) => [
  { path: `/org/${orgId}`, label: 'Overview' },
  { path: `/org/${orgId}/accounts`, label: 'Accounts' },
  { path: `/org/${orgId}/positions`, label: 'Positions' },
  ...(can(role, 'trade') ? [{ path: `/org/${orgId}/trade`, label: 'Trade' }] : []),
  { path: `/org/${orgId}/history`, label: 'History' },
  { path: `/org/${orgId}/logs`, label: 'Logs' },
  { path: `/org/${orgId}/members`, label: 'Members' },
]

function money(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function signedMoney(value: number | null | undefined): string {
  if (value == null) return '—'
  const formatted = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
  return `${value < 0 ? '-' : '+'}${formatted}`
}

/**
 * The desk strip: one glance = system state. Copying status with a live
 * pulse, the dry-run flag, the master's equity and open P&L, and the global
 * kill switch — present on every page, because the moment it's needed is
 * never the moment you're on the right page.
 */
function DeskStrip() {
  const { orgId, role } = useOrg()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [masterState, setMasterState] = useState<{ equity?: number | null; open_pnl?: number } | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [sett, accounts, state] = await Promise.all([
        orgApi<Settings>(orgId, 'settings'),
        orgApi<Account[]>(orgId, 'accounts'),
        orgApi<ApiState>(orgId, 'state'),
      ])
      setSettings(sett)
      const master = accounts.find((a) => a.role === 'master')
      setMasterState(
        master ? state.accounts?.[String(master.ctid_trader_account_id)] ?? null : null)
    } catch {
      // The strip is a passenger; pages surface their own errors.
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 10000)
    return () => clearInterval(interval)
  }, [refresh])

  useLiveRefresh(refresh, orgId)

  const handleCloseAll = async () => {
    try {
      setBusy(true)
      const result = await orgApi<CloseAllResult>(orgId, 'control/close-all', {
        method: 'POST',
        body: JSON.stringify({}),
      })
      const closed = result.accounts.reduce((n, a) => n + a.positions_closed, 0)
      const cancelled = result.accounts.reduce((n, a) => n + a.orders_cancelled, 0)
      setNotice(
        `Closed ${closed} position${closed === 1 ? '' : 's'} and cancelled ` +
        `${cancelled} order${cancelled === 1 ? '' : 's'} across ` +
        `${result.accounts.length} account${result.accounts.length === 1 ? '' : 's'}. Copying is paused.`)
      setDialogOpen(false)
      await refresh()
    } catch (err) {
      setNotice(`Close all failed: ${err instanceof Error ? err.message : 'unknown error'}`)
      setDialogOpen(false)
    } finally {
      setBusy(false)
    }
  }

  const copying = settings?.copying_enabled ?? null
  const dryRun = settings?.dry_run ?? false

  return (
    <>
      <div className="h-11 border-b border-line bg-card flex items-center gap-6 px-6 text-sm">
        <div className="flex items-center gap-2 min-w-32">
          <span
            aria-hidden="true"
            className={`inline-block w-2 h-2 rounded-full ${
              copying == null ? 'bg-line-strong'
              : copying ? 'bg-profit pulse-dot' : 'bg-loss'
            }`}
          />
          <span className="font-medium text-ink">
            {copying == null ? 'Connecting…' : copying ? 'Copying live' : 'Copying paused'}
          </span>
        </div>
        {dryRun && (
          <span className="desk-label text-warn bg-warn-wash px-2 py-0.5 rounded">
            Dry run
          </span>
        )}
        <div className="hidden md:flex items-center gap-6 ml-auto">
          <div className="flex items-baseline gap-2">
            <span className="desk-label">Master equity</span>
            <span className="num text-ink">{money(masterState?.equity)}</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="desk-label">Open P&L</span>
            <span
              className={`num ${
                (masterState?.open_pnl ?? 0) < 0 ? 'text-loss' : 'text-profit'
              }`}
            >
              {signedMoney(masterState?.open_pnl)}
            </span>
          </div>
          {can(role, 'control') && (
            <button
              onClick={() => setDialogOpen(true)}
              className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-white transition-colors"
            >
              Close all positions
            </button>
          )}
        </div>
      </div>

      {notice && (
        <div className="px-6 py-2 bg-brand-wash border-b border-line text-sm text-ink flex items-center justify-between">
          <span>{notice}</span>
          <button
            onClick={() => setNotice(null)}
            className="text-ink-soft hover:text-ink text-xs font-medium"
          >
            Dismiss
          </button>
        </div>
      )}

      {can(role, 'control') && (
        <ConfirmDialog
          open={dialogOpen}
          title="Close every position, everywhere"
          confirmLabel="Close every position"
          danger
          typeToConfirm="CLOSE ALL"
          busy={busy}
          onConfirm={handleCloseAll}
          onCancel={() => setDialogOpen(false)}
        >
          <p>
            This closes every open position and cancels every working order in
            every enabled account in this organization at market, and pauses
            copying. It cannot be undone.
          </p>
        </ConfirmDialog>
      )}
    </>
  )
}

export default function Layout() {
  const location = useLocation()
  const navigate = useNavigate()
  const { orgId, role, me } = useOrg()

  const handleLogout = async () => {
    try {
      await api('/api/logout', { method: 'POST' })
      navigate('/login')
    } catch (err) {
      console.error('Logout failed:', err)
      navigate('/login')
    }
  }

  const items = navItems(orgId, role)

  return (
    <div className="flex h-screen bg-paper">
      {/* Sidebar */}
      <div className="w-56 border-r border-line bg-card flex flex-col">
        <div className="px-6 pt-6 pb-5 border-b border-line">
          <h1 className="font-display text-xl text-brand">Copy Desk</h1>
          <select
            aria-label="Organization"
            value={orgId}
            onChange={(e) => navigate(`/org/${e.target.value}`)}
            className="mt-2 w-full text-sm border border-line-strong rounded bg-card text-ink px-2 py-1"
          >
            {me.orgs.map((o) => (
              <option key={o.id} value={o.id}>{o.name}</option>
            ))}
          </select>
          <p className="desk-label mt-1">FP Markets · cTrader</p>
        </div>

        <nav className="mt-4 flex-1">
          {items.map((item) => {
            const active = location.pathname === item.path
            return (
              <Link
                key={item.path}
                to={item.path}
                className={`relative block px-6 py-2.5 text-sm transition-colors ${
                  active
                    ? 'text-brand font-semibold'
                    : 'text-ink-soft hover:text-ink hover:bg-paper'
                }`}
              >
                {active && (
                  <span
                    aria-hidden="true"
                    className="absolute left-0 top-1 bottom-1 w-0.5 bg-brand rounded-r"
                  />
                )}
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="border-t border-line p-4">
          <button
            onClick={handleLogout}
            className="w-full text-left px-2 py-2 text-sm text-ink-soft hover:text-ink transition-colors"
          >
            Log out
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <DeskStrip />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
