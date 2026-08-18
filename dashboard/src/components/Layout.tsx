import { useCallback, useEffect, useState } from 'react'
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import type { Account, ApiState, CloseAllResult, EventResponse, Settings } from '../lib/types'
import ConfirmDialog from './ConfirmDialog'

const navItems = [
  { path: '/', label: 'Overview' },
  { path: '/accounts', label: 'Accounts' },
  { path: '/positions', label: 'Positions' },
  { path: '/trade', label: 'Trade' },
  { path: '/history', label: 'History' },
  { path: '/performance', label: 'Performance' },
  { path: '/logs', label: 'Logs' },
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
  const [settings, setSettings] = useState<Settings | null>(null)
  const [masterState, setMasterState] = useState<{ equity?: number | null; open_pnl?: number } | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [marginCall, setMarginCall] = useState<EventResponse | null>(null)
  const [dismissedRiskId, setDismissedRiskId] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [sett, accounts, state] = await Promise.all([
        api<Settings>('/api/settings'),
        api<Account[]>('/api/accounts'),
        api<ApiState>('/api/state'),
      ])
      setSettings(sett)
      const master = accounts.find((a) => a.role === 'master')
      setMasterState(
        master ? state.accounts?.[String(master.ctid_trader_account_id)] ?? null : null)
    } catch {
      // The strip is a passenger; pages surface their own errors.
    }
    try {
      // A margin call in the last 30 minutes is a right-now problem; show
      // it on every page until dismissed or aged out.
      const risk = await api<EventResponse[]>('/api/events?category=risk&limit=5')
      const recent = (risk ?? []).find((event) =>
        Date.now() - new Date(event.ts).getTime() < 30 * 60_000)
      setMarginCall(recent ?? null)
    } catch {
      // Older api without the risk category: no banner.
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 10000)
    return () => clearInterval(interval)
  }, [refresh])

  useLiveRefresh(refresh)

  const handleCloseAll = async () => {
    try {
      setBusy(true)
      const result = await api<CloseAllResult>('/api/control/close-all', {
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
          <button
            onClick={() => setDialogOpen(true)}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-white transition-colors"
          >
            Close all positions
          </button>
        </div>
      </div>

      {marginCall && marginCall.id !== dismissedRiskId && (
        <div
          role="alert"
          className="px-6 py-2.5 bg-loss text-white text-sm flex items-center justify-between"
        >
          <span>
            <strong>Margin call</strong>
            {marginCall.account_id != null && (
              <> on account <span className="num">{marginCall.account_id}</span></>
            )}
            {' — '}the broker may start force-closing positions. Reduce
            exposure or add funds now.
          </span>
          <button
            onClick={() => setDismissedRiskId(marginCall.id)}
            className="text-white/80 hover:text-white text-xs font-medium ml-4"
          >
            Dismiss
          </button>
        </div>
      )}

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
          every enabled account — master and slaves — at market, and pauses
          copying. It cannot be undone.
        </p>
      </ConfirmDialog>
    </>
  )
}

export default function Layout() {
  const location = useLocation()
  const navigate = useNavigate()

  const handleLogout = async () => {
    try {
      await api('/api/logout', { method: 'POST' })
      navigate('/login')
    } catch (err) {
      console.error('Logout failed:', err)
      navigate('/login')
    }
  }

  return (
    <div className="flex h-screen bg-paper">
      {/* Sidebar */}
      <div className="w-56 border-r border-line bg-card flex flex-col">
        <div className="px-6 pt-6 pb-5 border-b border-line">
          <h1 className="font-display text-xl text-brand">Copy Desk</h1>
          <p className="desk-label mt-1">cTrader</p>
        </div>

        <nav className="mt-4 flex-1">
          {navItems.map((item) => {
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
