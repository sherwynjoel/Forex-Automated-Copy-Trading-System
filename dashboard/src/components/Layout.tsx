import { useCallback, useEffect, useState } from 'react'
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { api, orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { useTheme } from '../hooks/useTheme'
import { can, type Role } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import type { Account, ApiState, CloseAllResult, EventResponse, Settings } from '../lib/types'
import ConfirmDialog from './ConfirmDialog'
import Logo from './Logo'
import { money, signed, errorText } from '../lib/format'

const navItems = (orgId: number, role: Role) => [
  { path: `/org/${orgId}`, label: 'Overview' },
  { path: `/org/${orgId}/accounts`, label: 'Accounts' },
  { path: `/org/${orgId}/positions`, label: 'Positions' },
  ...(can(role, 'trade') ? [{ path: `/org/${orgId}/trade`, label: 'Trade' }] : []),
  { path: `/org/${orgId}/history`, label: 'History' },
  { path: `/org/${orgId}/performance`, label: 'Performance' },
  { path: `/org/${orgId}/logs`, label: 'Logs' },
  { path: `/org/${orgId}/members`, label: 'Members' },
]

function localISODate(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

function cutoffDaysPhrase(cutoff: string): string {
  const days = Math.round((Date.parse(cutoff) - Date.parse(localISODate())) / 86_400_000)
  if (Number.isNaN(days)) return ''
  if (days <= 0) return ' (today)'
  return days === 1 ? ' (tomorrow)' : ` (in ${days} days)`
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
  const [notice, setNotice] = useState<{ kind: 'notice' | 'error'; text: string } | null>(null)
  const [marginCall, setMarginCall] = useState<EventResponse | null>(null)
  const [dismissedRiskId, setDismissedRiskId] = useState<number | null>(null)
  const [cutoffReminder, setCutoffReminder] = useState<EventResponse | null>(null)
  const [dismissedReminderId, setDismissedReminderId] = useState<number | null>(null)

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
    try {
      // A margin call in the last 30 minutes is a right-now problem; show
      // it on every page until dismissed or aged out. Org-scoped like every
      // other read here: the copier stamps risk events with the owning org
      // (see CopierApp._on_margin_call), and the events feed hides NULL-org
      // rows, so this only ever surfaces THIS desk's margin calls.
      const risk = await orgApi<EventResponse[]>(
        orgId, 'events?category=risk&limit=5')
      const recent = (risk ?? []).find((event) =>
        Date.now() - new Date(event.ts).getTime() < 30 * 60_000)
      setMarginCall(recent ?? null)
    } catch {
      // Older api without the risk category: no banner.
    }
    try {
      // An admin-set account cutoff is a scheduled fact, not a transient
      // alert: show the copier's one-time reminder on every page until
      // dismissed or the date itself has passed. Org-scoped like the risk
      // poll above.
      const reminders = await orgApi<EventResponse[]>(
        orgId, 'events?category=reminder&limit=5')
      const upcoming = (reminders ?? []).find((event) => {
        const cutoff = event.payload?.cutoff_date
        return typeof cutoff === 'string' && cutoff >= localISODate()
      })
      setCutoffReminder(upcoming ?? null)
    } catch {
      // Older api without the reminder category: no banner.
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
      setNotice({
        kind: 'notice',
        text:
          `Closed ${closed} position${closed === 1 ? '' : 's'} and cancelled ` +
          `${cancelled} order${cancelled === 1 ? '' : 's'} across ` +
          `${result.accounts.length} account${result.accounts.length === 1 ? '' : 's'}. Copying is paused.`,
      })
      setDialogOpen(false)
      await refresh()
    } catch (err) {
      setNotice({
        kind: 'error',
        text: `Close all failed: ${errorText(err, 'unknown error')} — positions may still be open.`,
      })
      setDialogOpen(false)
    } finally {
      setBusy(false)
    }
  }

  const copying = settings?.copying_enabled ?? null
  const dryRun = settings?.dry_run ?? false
  const reminderCutoffDate =
    typeof cutoffReminder?.payload?.cutoff_date === 'string'
      ? cutoffReminder.payload.cutoff_date : null
  const reminderAccountName =
    (typeof cutoffReminder?.payload?.nickname === 'string' &&
      cutoffReminder.payload.nickname) ||
    `account ${cutoffReminder?.account_id}`

  return (
    <>
      <div className="min-h-11 border-b border-line bg-card flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 md:px-6 py-2 md:py-1.5 text-sm">
        <div className="order-1 flex items-center gap-2">
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
          <span className="order-2 desk-label text-warn-deep bg-warn-wash px-2 py-0.5 rounded">
            Dry run
          </span>
        )}
        {can(role, 'control') && (
          <button
            onClick={() => setDialogOpen(true)}
            className="order-3 ml-auto md:order-5 md:ml-0 px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors"
          >
            Close all positions
          </button>
        )}
        <div className="order-4 w-full flex items-center justify-between gap-x-4 md:w-auto md:ml-auto md:justify-start md:gap-6">
          <div className="flex items-baseline gap-2">
            <span className="desk-label">Master equity</span>
            <span className="num font-semibold text-ink">{money(masterState?.equity)}</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="desk-label">Open P&L</span>
            <span
              className={`num font-semibold ${
                (masterState?.open_pnl ?? 0) < 0 ? 'text-loss' : 'text-profit'
              }`}
            >
              {signed(masterState?.open_pnl)}
            </span>
          </div>
        </div>
      </div>

      {marginCall && marginCall.id !== dismissedRiskId && (
        <div
          role="alert"
          className="px-6 py-2.5 bg-loss text-on-accent text-sm flex items-center justify-between"
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
            className="text-on-accent hover:underline text-xs font-medium ml-4"
          >
            Dismiss
          </button>
        </div>
      )}

      {cutoffReminder && reminderCutoffDate &&
        cutoffReminder.id !== dismissedReminderId && (
        <div
          role="status"
          className="px-6 py-2.5 bg-warn-wash border-b border-line text-sm text-ink flex items-center justify-between"
        >
          <span>
            <strong className="text-warn-deep">Account cutoff</strong>
            {' — '}{reminderAccountName} reaches its cutoff on{' '}
            <span className="num">{reminderCutoffDate}</span>
            {cutoffDaysPhrase(reminderCutoffDate)}.
          </span>
          <button
            onClick={() => setDismissedReminderId(cutoffReminder.id)}
            className="text-ink-soft hover:text-ink text-xs font-medium ml-4"
          >
            Dismiss
          </button>
        </div>
      )}

      {notice && (
        <div
          role={notice.kind === 'error' ? 'alert' : 'status'}
          className={`px-6 py-2 border-b border-line text-sm flex items-center justify-between ${
            notice.kind === 'error' ? 'bg-loss-wash text-loss-deep' : 'bg-brand-wash text-ink'
          }`}
        >
          <span>{notice.text}</span>
          <button
            onClick={() => setNotice(null)}
            className="text-xs font-medium opacity-70 hover:opacity-100"
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
  const { theme, toggle: toggleTheme } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const { orgId, role, me } = useOrg()
  const [menuOpen, setMenuOpen] = useState(false)

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

  // The drawer never outlives a navigation, and Escape dismisses it.
  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [menuOpen])

  const sidebarContent = (dense: boolean) => (
    <>
      <div className="px-6 pt-6 pb-5 border-b border-line">
        <Logo size={26} />
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

      <nav className="mt-4 flex-1 overflow-y-auto">
        {items.map((item) => {
          const active = location.pathname === item.path
          return (
            <Link
              key={item.path}
              to={item.path}
              onClick={() => setMenuOpen(false)}
              className={`relative block px-6 text-sm transition-colors ${
                dense ? 'py-2.5' : 'py-3'
              } ${
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
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? 'Switch to day mode' : 'Switch to dark mode'}
          className="w-full text-left px-2 py-2 text-sm text-ink-soft hover:text-ink transition-colors"
        >
          {theme === 'dark' ? '☀ Day mode' : '☾ Dark mode'}
        </button>
        <button
          onClick={handleLogout}
          className="w-full text-left px-2 py-2 text-sm text-ink-soft hover:text-ink transition-colors"
        >
          Log out
        </button>
      </div>
    </>
  )

  return (
    <div className="flex h-screen bg-paper">
      {/* Persistent sidebar — desktop only */}
      <div className="hidden lg:flex w-56 border-r border-line bg-card flex-col">
        {sidebarContent(true)}
      </div>

      {/* Off-canvas drawer — phone and tablet */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setMenuOpen(false)}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="relative h-full w-72 max-w-[85vw] bg-card border-r border-line flex flex-col shadow-xl"
          >
            {sidebarContent(false)}
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Mobile header: menu + brand */}
        <div className="lg:hidden h-12 shrink-0 border-b border-line bg-card flex items-center gap-2 px-2">
          <button
            aria-label="Open menu"
            onClick={() => setMenuOpen(true)}
            className="h-11 w-11 flex items-center justify-center rounded text-ink-soft hover:text-ink"
          >
            <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5">
              <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
          <Logo size={22} textClass="text-base" />
        </div>
        <DeskStrip />
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
