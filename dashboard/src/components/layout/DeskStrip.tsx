import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { can } from '../../lib/roles'
import { useLiveRefresh } from '../../hooks/useLiveRefresh'
import type { TicksPayload } from '../../lib/ticks'
import type { Account, ApiState, CloseAllResult, EventResponse, Settings, WebhookSettings } from '../../lib/types'
import Button from '../Button'
import ConfirmDialog from '../ConfirmDialog'
import KillSwitch from '../KillSwitch'
import { publishSettings } from '../../lib/settingsBus'
import { money, signed, errorText } from '../../lib/format'

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
/** One live price chip per open-contract symbol (master book). A symbol
 * with several positions shows once -- the mark is the same quote. */
function contractPrices(
  positions?: { symbol?: string | null; current_price?: number | null }[],
): { symbol: string; price: number | null }[] {
  const out = new Map<string, number | null>()
  for (const p of positions ?? []) {
    if (!p.symbol) continue
    if (!out.has(p.symbol) || p.current_price != null) {
      out.set(p.symbol, p.current_price ?? null)
    }
  }
  return [...out.entries()].map(([symbol, price]) => ({ symbol, price }))
}

/** `onAccounts` hands the fleet the strip already polls to whoever needs
 *  it -- the sidebar caption -- so the platform list costs no extra fetch. */
export default function DeskStrip({ onAccounts }: { onAccounts?: (accounts: Account[]) => void }) {
  const { orgId, role } = useOrg()
  const [settings, setSettings] = useState<Settings | null>(null)
  // The Automation switch, mirrored on every page next to the copying
  // state: an operator who left it ON should see that without opening
  // the page, and one who thinks it is ON should see when it is not.
  const [automation, setAutomation] =
    useState<Pick<WebhookSettings, 'enabled' | 'configured'> | null>(null)
  const [masterState, setMasterState] = useState<{ equity?: number | null; open_pnl?: number } | null>(null)
  const masterIdRef = useRef<number | null>(null)
  const [contracts, setContracts] = useState<{ symbol: string; price: number | null }[]>([])
  // Blast-radius numbers for the close-all dialog: how many enabled accounts
  // it would flatten, and how many positions the master currently carries.
  // Null means "not known yet" -- the dialog falls back to prose, never 0.
  const [enabledAccountCount, setEnabledAccountCount] = useState<number | null>(null)
  const [masterPositionCount, setMasterPositionCount] = useState<number | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'notice' | 'error'; text: string } | null>(null)
  const [marginCall, setMarginCall] = useState<EventResponse | null>(null)
  const [dismissedRiskId, setDismissedRiskId] = useState<number | null>(null)
  const [cutoffReminder, setCutoffReminder] = useState<EventResponse | null>(null)
  const [dismissedReminderId, setDismissedReminderId] = useState<number | null>(null)
  // The org the strip is showing now. Layout (and so the strip) stays
  // mounted across /org/:id changes, so a refresh started for the previous
  // org can land after the switch; it is dropped rather than shown here.
  const currentOrgRef = useRef(orgId)
  currentOrgRef.current = orgId

  const refresh = useCallback(async () => {
    const forOrg = orgId
    const stale = () => currentOrgRef.current !== forOrg
    try {
      const [sett, accounts, state] = await Promise.all([
        orgApi<Settings>(orgId, 'settings'),
        orgApi<Account[]>(orgId, 'accounts'),
        orgApi<ApiState>(orgId, 'state'),
      ])
      if (stale()) return
      setSettings(sett)
      // Pages (Overview's copying line, its setup checklist) read the same
      // settings, so a stop/resume here is reflected there at once.
      publishSettings(sett)
      onAccounts?.(accounts)
      // The copier's close-all only touches an account that is both enabled
      // AND not paused (a per-slave Pause sets status 'paused' without
      // touching `enabled`) -- counting `enabled` alone overstated the
      // dialog's blast radius by every paused slave.
      setEnabledAccountCount(
        accounts.filter((a) => a.enabled && a.status !== 'paused').length)
      const master = accounts.find((a) => a.role === 'master')
      masterIdRef.current = master?.ctid_trader_account_id ?? null
      const masterSnap = master
        ? state.accounts?.[String(master.ctid_trader_account_id)] ?? null : null
      setMasterState(masterSnap)
      setContracts(contractPrices(masterSnap?.positions))
      // Same rule for the master's own position count: a disabled or paused
      // master is not touched by close-all either, so naming its open
      // position count would promise a flatten that will not happen.
      const masterIncluded = master != null && master.enabled && master.status !== 'paused'
      setMasterPositionCount(masterIncluded ? masterSnap?.positions?.length ?? null : null)
    } catch {
      // The strip is a passenger; pages surface their own errors.
    }
    if (can(role, 'trade')) {
      try {
        const hook = await orgApi<WebhookSettings>(orgId, 'webhook')
        if (stale()) return
        setAutomation({ enabled: hook.enabled, configured: hook.configured })
      } catch {
        // Older api without automation: no pill.
        if (stale()) return
        setAutomation(null)
      }
    }
    try {
      // A margin call in the last 30 minutes is a right-now problem; show
      // it on every page until dismissed or aged out. Org-scoped like every
      // other read here: the copier stamps risk events with the owning org
      // (see CopierApp._on_margin_call), and the events feed hides NULL-org
      // rows, so this only ever surfaces THIS desk's margin calls.
      const risk = await orgApi<EventResponse[]>(
        orgId, 'events?category=risk&limit=5')
      if (stale()) return
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
      if (stale()) return
      const upcoming = (reminders ?? []).find((event) => {
        const cutoff = event.payload?.cutoff_date
        return typeof cutoff === 'string' && cutoff >= localISODate()
      })
      setCutoffReminder(upcoming ?? null)
    } catch {
      // Older api without the reminder category: no banner.
    }
  }, [orgId, role, onAccounts])

  // A different org's settings (and every other org-scoped figure) must
  // never be read, or acted on, as this one's: clear them before paint on
  // an org switch, and let the refresh below fill them for the new org.
  useLayoutEffect(() => {
    publishSettings(null)
    setSettings(null)
    setAutomation(null)
    setMasterState(null)
    masterIdRef.current = null
    setContracts([])
    setEnabledAccountCount(null)
    setMasterPositionCount(null)
    setMarginCall(null)
    setCutoffReminder(null)
    setNotice(null)
    setDialogOpen(false)
  }, [orgId])

  const updateSettings = useCallback((next: Settings) => {
    setSettings(next)
    publishSettings(next)
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 10000)
    return () => clearInterval(interval)
  }, [refresh])

  useLiveRefresh(refresh, orgId, (evt) => {
    // The strip's equity/open P&L tick live off the quotes stream.
    if (evt?.category !== 'quotes' || masterIdRef.current == null) return
    const snap = (evt.payload as TicksPayload | undefined)
      ?.accounts?.[String(masterIdRef.current)]
    if (snap) {
      setMasterState({ equity: snap.equity, open_pnl: snap.open_pnl })
      setContracts(contractPrices(snap.positions))
      setMasterPositionCount(snap.positions?.length ?? null)
    }
  })

  const handleCloseAll = async () => {
    try {
      setBusy(true)
      const result = await orgApi<CloseAllResult>(orgId, 'control/close-all', {
        method: 'POST',
        body: JSON.stringify({}),
      })
      // The operator switched org while the flatten ran: its outcome
      // belongs to the org it ran in, not the one on screen now.
      if (currentOrgRef.current !== orgId) return
      const closed = result.accounts.reduce((n, a) => n + a.positions_closed, 0)
      const cancelled = result.accounts.reduce((n, a) => n + a.orders_cancelled, 0)
      // Anything the copier could not close, or could not even check. Both
      // are risk the operator is still carrying, and both used to be
      // reported as success -- the button showed a tick over four accounts
      // whose every close the broker had refused.
      const stuck = result.accounts.filter(
        (a) => (a.positions_remaining?.length ?? 0) > 0 ||
               (a.orders_remaining?.length ?? 0) > 0 || a.error)
      const copyState = result.paused ? 'Copying is stopped.' : 'Copying is still running.'
      if (stuck.length > 0) {
        const stillOpen = stuck.reduce(
          (n, a) => n + (a.positions_remaining?.length ?? 0), 0)
        setNotice({
          kind: 'error',
          text:
            `Closed ${closed} position${closed === 1 ? '' : 's'}, but ` +
            `${stillOpen > 0 ? `${stillOpen} ` : ''}position${stillOpen === 1 ? '' : 's'} ` +
            `could not be closed on account${stuck.length === 1 ? '' : 's'} ` +
            `${stuck.map((a) => a.account_id).join(', ')}. ` +
            `You are still exposed there — close them in the platform. ${copyState}`,
        })
      } else {
        setNotice({
          kind: 'notice',
          text:
            `Closed ${closed} position${closed === 1 ? '' : 's'} and cancelled ` +
            `${cancelled} order${cancelled === 1 ? '' : 's'} across ` +
            `${result.accounts.length} account${result.accounts.length === 1 ? '' : 's'}. ` +
            `Every account verified flat. ${copyState}`,
        })
      }
      setDialogOpen(false)
      await refresh()
    } catch (err) {
      if (currentOrgRef.current !== orgId) return
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
      {/* Below md: one row of about 56 px -- the pulse and a state word,
          the master's equity, and the controls. Prices, open P&L and the
          Automation pill join from md up. */}
      <div className="glass min-h-14 md:min-h-11 border-b flex flex-wrap items-center gap-x-2 md:gap-x-4 gap-y-1.5 px-3 md:px-6 py-1.5 text-sm">
        <div className="order-1 flex items-center gap-1.5 md:gap-2">
          <span
            aria-hidden="true"
            className={`inline-block w-2 h-2 rounded-full ${
              copying == null ? 'bg-line-strong'
              : copying ? 'bg-profit pulse-dot' : 'bg-loss'
            }`}
          />
          <span className="font-medium text-ink">
            {copying == null ? 'Connecting…' : (
              <>
                <span className="md:hidden">{copying ? 'Live' : 'Paused'}</span>
                <span className="hidden md:inline">{copying ? 'Copying live' : 'Copying paused'}</span>
              </>
            )}
          </span>
        </div>
        {automation && typeof automation.enabled === 'boolean' && (
          <Link
            to={`/org/${orgId}/automation`}
            title="Open Automation"
            className="order-2 hidden md:flex items-center gap-2"
          >
            <span
              aria-hidden="true"
              className={`inline-block w-2 h-2 rounded-full ${
                automation.enabled ? 'bg-profit' : 'bg-line-strong'
              }`}
            />
            <span className={`font-medium ${automation.enabled ? 'text-ink' : 'text-ink-soft'}`}>
              {automation.enabled ? 'Automation on' : 'Automation off'}
            </span>
          </Link>
        )}
        {dryRun && !can(role, 'control') && (
          <span className="order-2 desk-label text-warn-deep bg-warn-wash px-2 py-0.5 rounded">
            Dry run
          </span>
        )}
        {can(role, 'control') && (
          // One group, so the kill switch and Close all wrap together
          // instead of splitting the row's free space between them.
          <div className="order-4 ml-auto md:order-5 md:ml-0 flex flex-wrap items-center justify-end gap-1.5 md:gap-2">
            {settings && <KillSwitch settings={settings} onUpdate={updateSettings} compact />}
            <Button
              variant="secondary"
              tone="loss"
              size="sm"
              aria-label="Close all positions"
              onClick={() => setDialogOpen(true)}
            >
              <span className="md:hidden">Close all</span>
              <span className="hidden md:inline">Close all positions</span>
            </Button>
          </div>
        )}
        <div className="order-3 md:order-4 flex items-center gap-x-4 md:ml-auto md:gap-6">
          {contracts.slice(0, 3).map((c) => (
            <div key={c.symbol} className="hidden md:flex items-baseline gap-2">
              <span className="desk-label">{c.symbol}</span>
              <span className="num font-semibold text-brand">
                {c.price != null ? c.price : '\u2014'}
              </span>
            </div>
          ))}
          {contracts.length > 3 && (
            <span className="hidden md:inline desk-label">+{contracts.length - 3}</span>
          )}
          <div className="flex items-baseline gap-2">
            {/* Phones drop the label's ink, not its words: it stays for screen readers. */}
            <span className="desk-label sr-only md:not-sr-only">Master equity</span>
            <span className="num font-semibold text-ink">{money(masterState?.equity)}</span>
          </div>
          <div className="hidden md:flex items-baseline gap-2">
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
          className="px-4 md:px-6 py-2.5 bg-loss text-on-accent text-sm flex flex-wrap items-center justify-between gap-2"
        >
          <span className="min-w-0 flex-1 basis-64">
            <strong>Margin call</strong>
            {marginCall.account_id != null && (
              <> on account <span className="num">{marginCall.account_id}</span></>
            )}
            {' — '}the broker may start force-closing positions. Reduce
            exposure or add funds now.
          </span>
          <Button
            variant="ghost"
            tone="inverse"
            size="sm"
            className="ml-auto"
            onClick={() => setDismissedRiskId(marginCall.id)}
          >
            Dismiss
          </Button>
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
          <Button
            variant="ghost"
            tone="neutral"
            size="sm"
            className="ml-4"
            onClick={() => setDismissedReminderId(cutoffReminder.id)}
          >
            Dismiss
          </Button>
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
          <Button
            variant="ghost"
            tone="neutral"
            size="sm"
            className="ml-4"
            onClick={() => setNotice(null)}
          >
            Dismiss
          </Button>
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
            This closes every open position and cancels every working order in{' '}
            {enabledAccountCount != null
              ? `${enabledAccountCount} enabled account${enabledAccountCount === 1 ? '' : 's'}`
              : 'every enabled account'} in this organization at market
            {masterPositionCount != null
              ? ` — the master alone currently has ${masterPositionCount} open position${masterPositionCount === 1 ? '' : 's'}`
              : ''}
            . It cannot be undone.
          </p>
          <p>
            {copying === false
              ? 'Copying is currently stopped and stays stopped.'
              : 'Copying stays on — though a master trade landing during the flatten itself is not copied.'}
          </p>
        </ConfirmDialog>
      )}
    </>
  )
}
