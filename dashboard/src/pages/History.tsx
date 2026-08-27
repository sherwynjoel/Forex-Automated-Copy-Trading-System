import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import type { Account, CashFlowEntry, Deal, HistoricalOrder, TradeSymbol } from '../lib/types'
import { money, signed, formatWhen, errorText } from '../lib/format'
import Banner from '../components/Banner'

const DAY_MS = 24 * 3600 * 1000

type Tab = 'closed' | 'bymaster' | 'deals' | 'orders' | 'cashflow'

/**
 * Pacing for whole-fleet history loads.
 *
 * cTrader refuses a burst: fired back-to-back the broker answers 400 part
 * way through, and the refusal is silent from the operator's side -- the
 * account simply shows no history. Measured on production, a ten-account
 * fleet showed roughly the first three.
 *
 * Mutable so tests can run at zero delay without pretending the delay does
 * not exist in production.
 */
export const historyPacing = {
  /** Between every request, including the two for one account. */
  gapMs: 350,
  /** Longer wait before a throttled account's single retry. */
  retryMs: 1500,
}

function accountLabel(account: Account): string {
  const env = account.is_live ? 'Live' : 'Demo'
  return account.nickname
    ? `${account.nickname} · ${account.trader_login} · ${env}`
    : `Account ${account.trader_login} · ${env}`
}

function price(value: number | null | undefined, digits = 5): string {
  return value == null ? '—' : value.toFixed(digits)
}

/** Master position id a copy order points at, or null. Copies carry
 * `copy:m<masterPositionId>` labels stamped by the copier. */
function masterIdFromLabel(label: string): number | null {
  const m = /^copy:m(\d+)$/.exec(label)
  return m ? Number(m[1]) : null
}

interface CopyExecution {
  accountId: number
  accountName: string
  positionId: number
  lots: number
  entry: number | null
  exit: number | null
  net: number
  closeTime: number | null
}

interface MasterGroup {
  positionId: number
  symbol: string | null
  side: string
  lots: number
  entry: number | null
  exit: number | null
  net: number
  closeTime: number | null
  copies: CopyExecution[]
}

const roundCents = (v: number) => Math.round(v * 100) / 100

/** cTrader throttles history payloads hard; explain its enum when it leaks. */
function withBrokerHint(message: string): string {
  return /BLOCKED_PAYLOAD_TYPE/.test(message)
    ? `${message} — the broker rate-limits history requests; wait a few seconds and retry.`
    : message
}

/** Aggregate an account's closing deals per position (a position may close
 * across several partial deals). */
function closedPositionsOf(deals: Deal[]) {
  const byPosition = new Map<number, {
    symbol: string | null
    side: string
    lots: number
    entry: number | null
    exit: number | null
    net: number
    closeTime: number | null
  }>()
  for (const deal of deals) {
    if (deal.close == null) continue
    const positionSide = deal.side === 'SELL' ? 'BUY' : 'SELL'
    const lots = parseFloat(String(deal.close.closed_volume_lots ?? 0)) || 0
    const net = deal.close.gross_profit + deal.close.swap + deal.close.commission
    const existing = byPosition.get(deal.position_id)
    if (existing == null) {
      byPosition.set(deal.position_id, {
        symbol: deal.symbol ?? null,
        side: positionSide,
        lots,
        entry: deal.close.entry_price,
        exit: deal.execution_price ?? null,
        net,
        closeTime: deal.execution_timestamp,
      })
    } else {
      existing.lots += lots
      existing.net += net
      existing.exit = deal.execution_price ?? null
      existing.closeTime = deal.execution_timestamp
    }
  }
  return byPosition
}

/**
 * Account-wise trade history straight from the broker: closed positions
 * (reconstructed from closing deals, exactly how cTrader's own History tab
 * works), every raw deal, and the order log.  cTrader caps history requests
 * at one week, so the desk pages by week (or by day, when a window
 * overflows the broker's row cap).
 */
export default function History() {
  const { orgId } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  // End of the visible window (ms). Start is always end - windowDays.
  const [windowEnd, setWindowEnd] = useState<number>(() => Date.now())
  const [windowDays, setWindowDays] = useState<7 | 1>(7)
  // Whether the window's trailing edge sits at "now" (Later is meaningless).
  const [atNow, setAtNow] = useState(true)
  // The whole fleet, grouped by master trade, is the question this page
  // exists to answer: what did the master do, and did each slave copy it?
  // Opening on a single account made that the buried case -- an operator
  // had to know the tab existed and pick it every visit.
  const [tab, setTab] = useState<Tab>('bymaster')
  const [deals, setDeals] = useState<Deal[]>([])
  const [orders, setOrders] = useState<HistoricalOrder[]>([])
  const [cashFlow, setCashFlow] = useState<CashFlowEntry[]>([])
  const [digitsBySymbol, setDigitsBySymbol] = useState<Record<string, number>>({})
  const [drillPosition, setDrillPosition] = useState<number | null>(null)
  const [drillDeals, setDrillDeals] = useState<Deal[] | null>(null)
  const [drillError, setDrillError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [fetchedAt, setFetchedAt] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [jumpDate, setJumpDate] = useState('')
  // The By-master tab needs every account's window at once; loaded lazily
  // the first time the tab opens (N accounts x 2 requests).
  const [fleet, setFleet] = useState<Record<number, { deals: Deal[]; orders: HistoricalOrder[] }> | null>(null)
  const [fleetLoading, setFleetLoading] = useState(false)
  const [fleetFailed, setFleetFailed] = useState<string[]>([])
  const [fleetReloadKey, setFleetReloadKey] = useState(0)
  // Which account the fleet load is on, so a paced load of ten accounts
  // reads as progress rather than a hung screen.
  const [fleetProgress, setFleetProgress] = useState<{ done: number; total: number } | null>(null)
  const seqRef = useRef(0)
  const drillSeqRef = useRef(0)
  const drillCloseRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const drillOpenerRef = useRef<HTMLElement | null>(null)

  const windowMs = windowDays * DAY_MS

  const loadAccounts = useCallback(async () => {
    try {
      const accs = await orgApi<Account[]>(orgId, 'accounts')
      setAccounts(accs)
      setError(null)
      const master = accs.find((a) => a.role === 'master')
      setAccountId(master?.ctid_trader_account_id ?? accs[0]?.ctid_trader_account_id ?? null)
    } catch (err) {
      setError(errorText(err, 'the accounts list did not load'))
    }
  }, [orgId])

  useEffect(() => {
    loadAccounts()
  }, [loadAccounts])

  // Per-symbol digits so prices render the way the broker quotes them
  // (JPY pairs 3, metals 2, majors 5) instead of a hardcoded five.
  useEffect(() => {
    if (accountId == null) return
    let cancelled = false
    orgApi<TradeSymbol[]>(orgId, `accounts/${accountId}/symbols`)
      .then((syms) => {
        if (cancelled || !Array.isArray(syms)) return
        const map: Record<string, number> = {}
        for (const s of syms) map[s.name] = s.digits
        setDigitsBySymbol(map)
      })
      .catch(() => { /* digits are a nicety; fall back to 5 */ })
    return () => { cancelled = true }
  }, [orgId, accountId])

  const loadHistory = useCallback(async () => {
    if (accountId == null) return
    const from = windowEnd - windowMs
    const query = `from=${from}&to=${windowEnd}`
    const seq = ++seqRef.current
    try {
      setLoading(true)
      setError(null)
      const [dealsRes, ordersRes, cashRes] = await Promise.all([
        orgApi<{ deals: Deal[]; has_more: boolean }>(
          orgId, `accounts/${accountId}/history/deals?${query}`),
        orgApi<{ orders: HistoricalOrder[]; has_more: boolean }>(
          orgId, `accounts/${accountId}/history/orders?${query}`),
        orgApi<{ entries: CashFlowEntry[] }>(
          orgId, `accounts/${accountId}/history/cashflow?${query}`),
      ])
      // A newer request superseded this one: never paint an older window's
      // rows under the current range label.
      if (seq !== seqRef.current) return
      setDeals(dealsRes.deals)
      setOrders(ordersRes.orders)
      setCashFlow(cashRes.entries)
      setHasMore(dealsRes.has_more || ordersRes.has_more)
      setFetchedAt(Date.now())
    } catch (err) {
      if (seq !== seqRef.current) return
      setError(withBrokerHint(errorText(err, 'the copier did not respond')))
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [orgId, accountId, windowEnd, windowMs])

  useEffect(() => {
    // The fleet tab loads every account itself, paced. Firing a
    // three-request burst for the selected account alongside it competes
    // for the same broker throttle -- the exact thing that drops accounts
    // out of the view.
    if (tab === 'bymaster') return
    loadHistory()
  }, [loadHistory, tab])

  useEffect(() => {
    if (tab !== 'bymaster' || accounts.length === 0) return
    let cancelled = false
    const load = async () => {
      setFleetLoading(true)
      // SEQUENTIAL AND PACED. cTrader throttles history hard: fired
      // back-to-back, the broker starts answering 400/BLOCKED_PAYLOAD_TYPE
      // partway through. Measured on production with four accounts, the
      // first five requests answered and the next three were refused --
      // so a ten-account fleet showed roughly the first three accounts and
      // silently dropped the rest, which read as "most of my accounts have
      // no history". Sequential alone was never enough; the gap is what
      // keeps the broker answering.
      const query = `from=${windowEnd - windowMs}&to=${windowEnd}`
      const result: Record<number, { deals: Deal[]; orders: HistoricalOrder[] }> = {}
      const failed: string[] = []

      // Waits, but stays responsive to the tab being closed mid-load.
      const pause = (ms: number) => new Promise((r) => setTimeout(r, ms))

      const fetchWindow = async (id: number) => {
        const d = await orgApi<{ deals: Deal[] }>(
          orgId, `accounts/${id}/history/deals?${query}`)
        await pause(historyPacing.gapMs)
        const o = await orgApi<{ orders: HistoricalOrder[] }>(
          orgId, `accounts/${id}/history/orders?${query}`)
        return { deals: d.deals, orders: o.orders }
      }

      for (let i = 0; i < accounts.length; i += 1) {
        if (cancelled) return
        const account = accounts[i]
        const id = account.ctid_trader_account_id
        setFleetProgress({ done: i, total: accounts.length })
        try {
          result[id] = await fetchWindow(id)
        } catch {
          // One retry after a longer wait. A throttled account is not a
          // broken account, and reporting it as failed on the first
          // refusal is what made healthy accounts look empty.
          if (cancelled) return
          await pause(historyPacing.retryMs)
          if (cancelled) return
          try {
            result[id] = await fetchWindow(id)
          } catch {
            failed.push(accountLabel(account))
          }
        }
        if (i < accounts.length - 1) await pause(historyPacing.gapMs)
      }
      if (cancelled) return
      setFleetProgress(null)
      setFleet(result)
      setFleetFailed(failed)
      setFleetLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [tab, orgId, accounts, windowEnd, windowMs, fleetReloadKey])

  const closingDeals = deals.filter((d) => d.close != null)
  const from = windowEnd - windowMs

  // Slave copies point at their master trade via order labels.
  const copyMap = useMemo(() => {
    const map: Record<number, number> = {}
    for (const order of orders) {
      const masterId = masterIdFromLabel(order.label)
      if (masterId != null && order.position_id != null) {
        map[order.position_id] = masterId
      }
    }
    return map
  }, [orders])
  const masterAccountId = accounts.find((a) => a.role === 'master')?.ctid_trader_account_id

  // Master trades in the window, each with the slave executions that copied
  // it (linked via copy:m<id> order labels). Copies whose master closed
  // outside this window are not shown -- lineage follows the master's close.
  const masterGroups = useMemo((): MasterGroup[] => {
    if (fleet == null || masterAccountId == null) return []
    const masterData = fleet[masterAccountId]
    if (masterData == null) return []
    const groups = new Map<number, MasterGroup>()
    for (const [positionId, agg] of closedPositionsOf(masterData.deals)) {
      groups.set(positionId, {
        positionId,
        symbol: agg.symbol,
        side: agg.side,
        lots: agg.lots,
        entry: agg.entry,
        exit: agg.exit,
        net: roundCents(agg.net),
        closeTime: agg.closeTime,
        copies: [],
      })
    }
    for (const account of accounts) {
      const id = account.ctid_trader_account_id
      if (id === masterAccountId) continue
      const data = fleet[id]
      if (data == null) continue
      const toMaster: Record<number, number> = {}
      for (const order of data.orders) {
        const masterId = masterIdFromLabel(order.label)
        if (masterId != null && order.position_id != null) {
          toMaster[order.position_id] = masterId
        }
      }
      for (const [positionId, agg] of closedPositionsOf(data.deals)) {
        const masterId = toMaster[positionId]
        const group = masterId != null ? groups.get(masterId) : undefined
        if (group == null) continue
        group.copies.push({
          accountId: id,
          accountName: accountLabel(account),
          positionId,
          lots: agg.lots,
          entry: agg.entry,
          exit: agg.exit,
          net: roundCents(agg.net),
          closeTime: agg.closeTime,
        })
      }
    }
    return Array.from(groups.values())
      .sort((a, b) => (b.closeTime ?? 0) - (a.closeTime ?? 0))
  }, [fleet, accounts, masterAccountId])

  const digitsFor = (symbol: string | null | undefined): number =>
    (symbol != null ? digitsBySymbol[symbol] : undefined) ?? 5

  const goEarlier = () => {
    setWindowEnd(windowEnd - windowMs)
    setAtNow(false)
    setJumpDate('')
  }

  const goLater = () => {
    const next = windowEnd + windowMs
    if (next >= Date.now()) {
      setWindowEnd(Date.now())
      setAtNow(true)
    } else {
      setWindowEnd(next)
    }
    setJumpDate('')
  }

  const jumpToDate = (value: string) => {
    setJumpDate(value)
    const [y, m, d] = value.split('-').map(Number)
    if (!y || !m || !d) return
    // Date handles month/DST rollover; midnight of the NEXT day ends this one.
    const end = new Date(y, m - 1, d + 1).getTime()
    if (end >= Date.now()) {
      setWindowEnd(Date.now())
      setAtNow(true)
    } else {
      setWindowEnd(end)
      setAtNow(false)
    }
  }

  // Refresh must move the window with the clock when it sits at "now" --
  // otherwise a page left open refetches the same stale range while the
  // "as of" stamp claims freshness.
  const refresh = () => {
    if (atNow) setWindowEnd(Date.now())
    else loadHistory()
  }

  const retry = () => {
    if (accountId == null) loadAccounts()
    else loadHistory()
  }

  const openDrill = async (positionId: number, forAccountId?: number) => {
    const account = forAccountId ?? accountId
    drillOpenerRef.current = document.activeElement as HTMLElement | null
    setDrillPosition(positionId)
    setDrillDeals(null)
    setDrillError(null)
    if (account == null) return
    const seq = ++drillSeqRef.current
    try {
      const result = await orgApi<{ deals: Deal[] }>(
        orgId,
        `accounts/${account}/positions/${positionId}/deals?from=0&to=${Date.now()}`)
      if (seq !== drillSeqRef.current) return
      setDrillDeals(result.deals)
    } catch (err) {
      if (seq !== drillSeqRef.current) return
      setDrillError(errorText(err, 'the broker did not respond'))
      setDrillDeals([])
    }
  }

  // Drawer keyboard contract: Escape closes, Tab is trapped inside, focus
  // starts on the close button and returns to the opener afterwards.
  useEffect(() => {
    if (drillPosition == null) return
    drillCloseRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDrillPosition(null)
        return
      }
      if (e.key !== 'Tab') return
      const panel = drawerRef.current
      if (!panel) return
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>('button, input, [href]')
      ).filter((el) => !el.hasAttribute('disabled'))
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (!panel.contains(active)) {
        e.preventDefault()
        first.focus()
      } else if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      drillOpenerRef.current?.focus?.()
    }
  }, [drillPosition])

  const currentYear = new Date().getFullYear()
  const showYear = new Date(from).getFullYear() !== currentYear
    || new Date(windowEnd).getFullYear() !== currentYear
  const rangeDate = (ms: number) =>
    new Date(ms).toLocaleDateString('en-GB',
      showYear
        ? { day: '2-digit', month: 'short', year: 'numeric' }
        : { day: '2-digit', month: 'short' })

  const TAB_ORDER: Tab[] = ['closed', 'bymaster', 'deals', 'orders', 'cashflow']
  const onTablistKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const idx = TAB_ORDER.indexOf(tab)
    const next = e.key === 'ArrowRight'
      ? TAB_ORDER[(idx + 1) % TAB_ORDER.length]
      : TAB_ORDER[(idx + TAB_ORDER.length - 1) % TAB_ORDER.length]
    setTab(next)
    document.getElementById(`history-tab-${next}`)?.focus()
  }

  const tabButton = (key: Tab, label: string, count?: number) => (
    <button
      key={key}
      id={`history-tab-${key}`}
      role="tab"
      aria-selected={tab === key}
      aria-controls="history-tabpanel"
      tabIndex={tab === key ? 0 : -1}
      onClick={() => setTab(key)}
      className={`min-h-11 md:min-h-0 px-4 py-2 text-sm rounded-t border-b-2 transition-colors whitespace-nowrap ${
        tab === key
          ? 'border-brand text-brand font-semibold'
          : 'border-transparent text-ink-soft hover:text-ink'
      }`}
    >
      {label}{count != null && <span className="num text-xs text-ink-faint"> {count}</span>}
    </button>
  )

  const windowNoun = windowDays === 7 ? 'week' : 'day'

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="page-title">History</h1>
        <p className="text-sm text-ink-soft mt-1 max-w-prose">
          Every master trade with the copies each slave placed against it,
          straight from the broker. Switch tabs to look at one account on
          its own. One week at a time — that is the most cTrader hands out
          per request.
        </p>
      </header>

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-4">
        {/* Only meaningful on the per-account tabs. Leaving it visible on
            the fleet view invites the operator to pick an account and
            watch nothing happen. */}
        {tab !== 'bymaster' && (
          <div>
            <label htmlFor="history-account" className="desk-label block mb-1">Account</label>
            <select
              id="history-account"
              value={accountId ?? ''}
              onChange={(e) => { setAccountId(Number(e.target.value)); setJumpDate('') }}
              className="rounded border border-line-strong px-3 py-2 text-sm bg-card min-w-64"
            >
              {accounts.map((a) => (
                <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded border border-line-strong overflow-hidden" role="group" aria-label="Window size">
            {([7, 1] as const).map((days) => (
              <button
                key={days}
                aria-pressed={windowDays === days}
                onClick={() => setWindowDays(days)}
                className={`px-3 py-2 text-sm transition-colors ${
                  windowDays === days
                    ? 'bg-brand text-on-accent font-semibold'
                    : 'bg-card text-ink-soft hover:text-ink'
                }`}
              >
                {days === 7 ? 'Week' : 'Day'}
              </button>
            ))}
          </div>
          <button
            onClick={goEarlier}
            className="px-3 py-2 text-sm rounded border border-line-strong text-ink-soft hover:text-ink transition-colors"
          >
            ← Earlier
          </button>
          <span className="num text-sm text-ink-soft min-w-44 text-center">
            {rangeDate(from)}{' – '}{rangeDate(windowEnd)}
          </span>
          <button
            onClick={goLater}
            disabled={atNow}
            className="px-3 py-2 text-sm rounded border border-line-strong text-ink-soft hover:text-ink transition-colors disabled:opacity-40"
          >
            Later →
          </button>
        </div>
        <div>
          <label htmlFor="history-jump" className="desk-label block mb-1">Jump to date</label>
          <input
            id="history-jump"
            type="date"
            value={jumpDate}
            onChange={(e) => jumpToDate(e.target.value)}
            className="rounded border border-line-strong px-3 py-2 text-sm bg-card"
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="px-3 py-2 text-sm rounded border border-line-strong text-ink-soft hover:text-ink transition-colors"
          >
            Refresh
          </button>
          {fetchedAt != null && (
            <span className="num text-xs text-ink-faint">
              as of {new Date(fetchedAt).toLocaleTimeString('en-GB')}
            </span>
          )}
        </div>
        {hasMore && (
          <p className="text-xs font-medium text-warn-deep">
            {windowDays === 7
              ? 'This week has more rows than one request returns — switch to day view to see everything.'
              : 'This day has more rows than one request returns; rows beyond the first batch are hidden.'}
          </p>
        )}
      </div>

      {error && (
        <div className="space-y-3">
          <Banner kind="error">{error}</Banner>
          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={retry}
              className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep transition-colors"
            >
              Retry
            </button>
            <p className="text-xs text-ink-soft">
              If this keeps happening, the account's cTrader connection may
              need re-authorizing on the Accounts page.
            </p>
          </div>
        </div>
      )}
      {/* An error before ANY data suppresses the tables (no fake empties);
          an error over existing data keeps the last good rows visible. */}
      {!(error && fetchedAt == null) && (
        <>
          <div className={loading ? 'opacity-60' : ''} aria-busy={loading}>
          {/* Tabs */}
          <div
            className="border-b border-line flex gap-1 overflow-x-auto"
            role="tablist"
            onKeyDown={onTablistKeyDown}
          >
            {tabButton('bymaster', 'All accounts', fleet != null ? masterGroups.length : undefined)}
            {tabButton('closed', 'Closed positions', closingDeals.length)}
            {tabButton('deals', 'Deals', deals.length)}
            {tabButton('orders', 'Orders', orders.length)}
            {tabButton('cashflow', 'Cash flow', cashFlow.length)}
          </div>

          {/* This gate is about the SINGLE-ACCOUNT load. The fleet tab does
              not run that load -- its three parallel requests would race
              the paced whole-fleet fetch -- so `loading` stays true there
              forever and the gate never opened: the page sat on "Loading
              history..." and rendered nothing at all. The fleet view
              reports its own progress, so it must not be gated here. */}
          {tab !== 'bymaster' && loading && fetchedAt == null ? (
            <p className="text-sm text-ink-soft py-8">Loading history…</p>
          ) : (
            <div id="history-tabpanel" role="tabpanel" aria-labelledby={`history-tab-${tab}`} className="mt-6">
              {tab === 'bymaster' ? (
                <MasterGroupsView
                  groups={masterGroups}
                  loading={fleetLoading && fleet == null}
                  progress={fleetProgress}
                  masterLoaded={fleet != null && masterAccountId != null
                    && fleet[masterAccountId] != null}
                  failedAccounts={fleetFailed}
                  onRetryFleet={() => { setFleet(null); setFleetReloadKey((k) => k + 1) }}
                  digitsFor={digitsFor}
                  windowNoun={windowNoun}
                  onDrill={openDrill}
                  masterAccountId={masterAccountId}
                />
              ) : tab === 'closed' ? (
                <ClosedPositionsTable
                  deals={closingDeals}
                  onDrill={openDrill}
                  digitsFor={digitsFor}
                  copyMap={copyMap}
                  onDrillMaster={masterAccountId != null
                    ? (masterId) => openDrill(masterId, masterAccountId)
                    : undefined}
                  windowNoun={windowNoun}
                />
              ) : tab === 'deals' ? (
                <DealsTable deals={deals} digitsFor={digitsFor} windowNoun={windowNoun} />
              ) : tab === 'orders' ? (
                <OrdersTable orders={orders} digitsFor={digitsFor} windowNoun={windowNoun} />
              ) : (
                <CashFlowTable entries={cashFlow} windowNoun={windowNoun} />
              )}
            </div>
          )}
          </div>
        </>
      )}

      {/* Position drill-down drawer */}
      {drillPosition != null && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-black/60"
          onClick={() => setDrillPosition(null)}
        >
          <aside
            ref={drawerRef}
            className="w-full max-w-lg h-full bg-card border-l border-line overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={`Deals for position ${drillPosition}`}
          >
            <div className="px-6 py-5 border-b border-line flex items-start justify-between">
              <div>
                <h2 className="font-display text-xl text-ink">Position {drillPosition}</h2>
                <p className="text-sm text-ink-soft mt-0.5">
                  Every fill, partial close, and close of this position.
                </p>
              </div>
              <button
                ref={drillCloseRef}
                onClick={() => setDrillPosition(null)}
                aria-label="Close position details"
                className="text-ink-soft hover:text-ink text-xl leading-none"
              >
                ×
              </button>
            </div>
            {drillError != null ? (
              <p className="px-6 py-4 text-sm text-loss-deep">
                Could not fetch this position's deals: {drillError}
              </p>
            ) : drillDeals == null ? (
              <p className="px-6 py-4 text-sm text-ink-soft">Fetching from the broker…</p>
            ) : drillDeals.length === 0 ? (
              <p className="px-6 py-4 text-sm text-ink-soft">
                The broker returned no deals for this position.
              </p>
            ) : (
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr className="text-left border-b border-line">
                    <th className="desk-label px-6 py-2 font-semibold">When</th>
                    <th className="desk-label px-3 py-2 font-semibold">Side</th>
                    <th className="desk-label px-3 py-2 font-semibold text-right">Lots</th>
                    <th className="desk-label px-3 py-2 font-semibold text-right">Price</th>
                    <th className="desk-label px-6 py-2 font-semibold text-right">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {drillDeals.map((deal) => (
                    <tr key={deal.deal_id} className="border-b border-line last:border-0">
                      <td data-label="When" className="num px-6 py-2.5 text-ink-soft whitespace-nowrap">
                        {formatWhen(deal.execution_timestamp)}
                      </td>
                      <td data-label="Side" className={`px-3 py-2.5 font-medium ${deal.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                        {deal.side}
                        <span className="text-xs text-ink-faint ml-1">
                          {deal.close ? 'close' : 'open'}
                        </span>
                      </td>
                      <td data-label="Lots" className="num px-3 py-2.5 text-right">
                        {deal.volume_lots ?? deal.filled_volume}
                      </td>
                      <td data-label="Price" className="num px-3 py-2.5 text-right">
                        {price(deal.execution_price, digitsFor(deal.symbol))}
                      </td>
                      <td data-label="P&L" className={`num px-6 py-2.5 text-right ${
                        deal.close
                          ? deal.close.gross_profit < 0 ? 'text-loss' : 'text-profit'
                          : 'text-ink-faint'
                      }`}>
                        {deal.close ? signed(deal.close.gross_profit) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}

function MasterGroupsView({
  groups, loading, progress, masterLoaded, failedAccounts, onRetryFleet,
  digitsFor, windowNoun, onDrill, masterAccountId,
}: {
  groups: MasterGroup[]
  loading: boolean
  progress: { done: number; total: number } | null
  masterLoaded: boolean
  failedAccounts: string[]
  onRetryFleet: () => void
  digitsFor: (symbol: string | null | undefined) => number
  windowNoun: string
  onDrill: (positionId: number, forAccountId?: number) => void
  masterAccountId?: number
}) {
  if (loading) {
    // Paced deliberately (the broker refuses a burst), so a ten-account
    // fleet takes a few seconds. Counting up says "working", where a bare
    // spinner on a slow load says "stuck".
    return (
      <p className="text-sm text-ink-soft py-8">
        {progress && progress.total > 1
          ? `Loading the fleet's history… account ${progress.done + 1} of ${progress.total}`
          : "Loading the fleet's history…"}
      </p>
    )
  }
  const retryButton = (
    <button
      onClick={onRetryFleet}
      className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep transition-colors"
    >
      Retry fleet
    </button>
  )
  if (!masterLoaded) {
    // Without the master's rows there is nothing to group under: say so —
    // never render this as a confident "no positions" claim.
    return (
      <div className="space-y-3 py-4">
        <Banner kind="error">
          The master account's history could not be loaded
          {failedAccounts.length > 0
            ? ' — the broker rate-limits history requests; wait a few seconds and retry.'
            : '.'}
        </Banner>
        {retryButton}
      </div>
    )
  }
  const partialWarning = failedAccounts.length > 0 && (
    <div className="space-y-3">
      <Banner kind="warn">
        Could not load {failedAccounts.length} account
        {failedAccounts.length === 1 ? '' : 's'}: {failedAccounts.join(', ')}.
        Their copies are missing from this view — the broker rate-limits
        history requests; wait a few seconds and retry.
      </Banner>
      {retryButton}
    </div>
  )
  if (groups.length === 0) {
    return (
      <div className="space-y-4">
        {partialWarning}
        <p className="text-sm text-ink-soft py-8">
          No master positions were closed in this {windowNoun}. Page back to look earlier.
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-4">
      {partialWarning}
      {groups.map((group) => {
        const digits = digitsFor(group.symbol)
        return (
          <div key={group.positionId} className="bg-card rounded-lg border border-line overflow-hidden">
            <div className="bg-paper border-b border-line px-5 py-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <button
                onClick={() => onDrill(group.positionId, masterAccountId)}
                aria-label={`View master position ${group.positionId}`}
                className="num font-semibold text-brand hover:text-brand-deep underline decoration-dotted underline-offset-2"
              >
                #{group.positionId}
              </button>
              <span className="num font-semibold">{group.symbol ?? '—'}</span>
              <span className={`font-medium ${group.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                {group.side}
              </span>
              <span className="num">{group.lots.toFixed(2)} lots</span>
              <span className="num text-ink-soft">
                {price(group.entry, digits)} → {price(group.exit, digits)}
              </span>
              <span className={`num font-semibold ${group.net < 0 ? 'text-loss' : 'text-profit'}`}>
                {signed(group.net)}
              </span>
              <span className="num text-xs text-ink-faint ml-auto">
                {formatWhen(group.closeTime)}
              </span>
            </div>
            {group.copies.length === 0 ? (
              <p className="px-5 py-2.5 text-xs text-ink-faint">
                No slave copies in this {windowNoun}.
              </p>
            ) : (
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr className="text-left border-b border-line">
                    <th className="desk-label px-5 py-2 font-semibold">Slave</th>
                    <th className="desk-label px-3 py-2 font-semibold">Position</th>
                    <th className="desk-label px-3 py-2 font-semibold text-right">Lots</th>
                    <th className="desk-label px-3 py-2 font-semibold text-right">Entry → Exit</th>
                    <th className="desk-label px-3 py-2 font-semibold text-right">Entry diff</th>
                    <th className="desk-label px-5 py-2 font-semibold text-right">Net</th>
                  </tr>
                </thead>
                <tbody>
                  {group.copies.map((copy) => {
                    const diff = copy.entry != null && group.entry != null
                      ? copy.entry - group.entry
                      : null
                    return (
                      <tr key={`${copy.accountId}-${copy.positionId}`} className="border-b border-line last:border-0">
                        <td data-label="Slave" className="px-5 py-2.5 text-ink-soft">{copy.accountName}</td>
                        <td data-label="Position" className="num px-3 py-2.5">
                          <button
                            onClick={() => onDrill(copy.positionId, copy.accountId)}
                            aria-label={`View position ${copy.positionId}`}
                            className="text-brand hover:text-brand-deep underline decoration-dotted underline-offset-2"
                          >
                            {copy.positionId}
                          </button>
                        </td>
                        <td data-label="Lots" className="num px-3 py-2.5 text-right">{copy.lots.toFixed(2)}</td>
                        <td data-label="Entry → Exit" className="num px-3 py-2.5 text-right whitespace-nowrap">
                          {price(copy.entry, digits)} → {price(copy.exit, digits)}
                        </td>
                        <td data-label="Entry diff" className="num px-3 py-2.5 text-right text-ink-soft">
                          {diff == null ? '—' : (diff >= 0 ? '+' : '') + diff.toFixed(digits)}
                        </td>
                        <td data-label="Net" className={`num px-5 py-2.5 text-right font-medium ${copy.net < 0 ? 'text-loss' : 'text-profit'}`}>
                          {signed(copy.net)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        )
      })}
    </div>
  )
}

function CashFlowTable({ entries, windowNoun }: { entries: CashFlowEntry[]; windowNoun: string }) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-ink-soft py-8">
        No deposits or withdrawals in this {windowNoun}.
      </p>
    )
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className="desk-label px-5 py-2.5 font-semibold">When</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Type</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Amount</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Balance after</th>
            <th className="desk-label px-5 py-2.5 font-semibold">Note</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const isDeposit = entry.type.startsWith('DEPOSIT')
            return (
              <tr key={entry.id} className="border-b border-line last:border-0">
                <td data-label="When" className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                  {formatWhen(entry.timestamp)}
                </td>
                <td data-label="Type" className="px-3 py-2.5">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                    isDeposit ? 'bg-profit-wash text-profit-deep' : 'bg-loss-wash text-loss-deep'
                  }`}>
                    {entry.type}
                  </span>
                </td>
                <td data-label="Amount" className={`num px-3 py-2.5 text-right font-medium ${
                  isDeposit ? 'text-profit' : 'text-loss'
                }`}>
                  {isDeposit ? '+' : '-'}{Math.abs(entry.amount).toLocaleString('en-US', {
                    minimumFractionDigits: 2, maximumFractionDigits: 2,
                  })}
                </td>
                <td data-label="Balance after" className="num px-3 py-2.5 text-right">{money(entry.balance_after)}</td>
                <td data-label="Note" className="px-5 py-2.5 text-ink-soft">{entry.note || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ClosedPositionsTable({ deals, onDrill, digitsFor, copyMap, onDrillMaster, windowNoun }: {
  deals: Deal[]
  onDrill: (positionId: number) => void
  digitsFor: (symbol: string | null | undefined) => number
  copyMap: Record<number, number>
  onDrillMaster?: (masterPositionId: number) => void
  windowNoun: string
}) {
  if (deals.length === 0) {
    return (
      <p className="text-sm text-ink-soft py-8">
        No positions were closed in this {windowNoun}. Page back to look earlier.
      </p>
    )
  }
  // Round to cents BEFORE sign checks: float dust like -2.8e-17 must not
  // paint a "-0.00" total in loss red.
  const cents = (v: number) => Math.round(v * 100) / 100
  const net = (d: Deal) =>
    cents(d.close!.gross_profit + d.close!.swap + d.close!.commission)
  const raw = deals.reduce(
    (acc, d) => ({
      gross: acc.gross + d.close!.gross_profit,
      swap: acc.swap + d.close!.swap,
      commission: acc.commission + d.close!.commission,
      net: acc.net + net(d),
    }),
    { gross: 0, swap: 0, commission: 0, net: 0 },
  )
  const totals = {
    gross: cents(raw.gross), swap: cents(raw.swap),
    commission: cents(raw.commission), net: cents(raw.net),
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className="desk-label px-5 py-2.5 font-semibold">Closed</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Position</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Symbol</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Side</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Lots</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Entry → Exit</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Gross P&L</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Swap</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Commission</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Net</th>
            <th className="desk-label px-5 py-2.5 font-semibold text-right">Balance after</th>
          </tr>
        </thead>
        <tbody>
          {deals.map((deal) => {
            const close = deal.close!
            // The closing deal's side is the exit; the position itself was the opposite.
            const positionSide = deal.side === 'SELL' ? 'BUY' : 'SELL'
            const rowNet = net(deal)
            const masterId = copyMap[deal.position_id]
            const digits = digitsFor(deal.symbol)
            return (
              <tr key={deal.deal_id} className="border-b border-line last:border-0">
                <td data-label="Closed" className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                  {formatWhen(deal.execution_timestamp)}
                </td>
                <td data-label="Position" className="num px-3 py-2.5">
                  <button
                    onClick={() => onDrill(deal.position_id)}
                    aria-label={`View position ${deal.position_id}`}
                    className="text-brand hover:text-brand-deep underline decoration-dotted underline-offset-2"
                  >
                    {deal.position_id}
                  </button>
                  {masterId != null && onDrillMaster != null && (
                    <button
                      onClick={() => onDrillMaster(masterId)}
                      aria-label={`Copy of #${masterId} — view the master position's deals`}
                      className="block text-[11px] font-medium text-brand-deep bg-brand-wash rounded px-1.5 py-0.5 mt-1 hover:bg-brand hover:text-on-accent transition-colors"
                    >
                      copy of #{masterId}
                    </button>
                  )}
                </td>
                <td data-label="Symbol" className="num px-3 py-2.5">{deal.symbol ?? deal.symbol_id}</td>
                <td data-label="Side" className={`px-3 py-2.5 font-medium ${positionSide === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                  {positionSide}
                </td>
                <td data-label="Lots" className="num px-3 py-2.5 text-right">
                  {close.closed_volume_lots ?? close.closed_volume}
                </td>
                <td data-label="Entry → Exit" className="num px-3 py-2.5 text-right whitespace-nowrap">
                  {price(close.entry_price, digits)} → {price(deal.execution_price, digits)}
                </td>
                <td data-label="Gross P&L" className={`num px-3 py-2.5 text-right font-medium ${close.gross_profit < 0 ? 'text-loss' : 'text-profit'}`}>
                  {signed(close.gross_profit)}
                </td>
                <td data-label="Swap" className="num px-3 py-2.5 text-right text-ink-soft">{money(close.swap)}</td>
                <td data-label="Commission" className="num px-3 py-2.5 text-right text-ink-soft">{money(close.commission)}</td>
                <td data-label="Net" className={`num px-3 py-2.5 text-right font-semibold ${rowNet < 0 ? 'text-loss' : 'text-profit'}`}>
                  {signed(rowNet)}
                </td>
                <td data-label="Balance after" className="num px-5 py-2.5 text-right">{money(close.balance)}</td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-line-strong bg-paper">
            <td data-label="Totals" colSpan={6} className="px-5 py-2.5 text-sm font-semibold text-ink">
              {windowNoun === 'day' ? 'Day' : 'Week'} total · {deals.length} position{deals.length === 1 ? '' : 's'}
            </td>
            <td data-label="Gross P&L" className={`num px-3 py-2.5 text-right font-semibold ${totals.gross < 0 ? 'text-loss' : 'text-profit'}`}>
              {signed(totals.gross)}
            </td>
            <td data-label="Swap" className="num px-3 py-2.5 text-right text-ink-soft">{money(totals.swap)}</td>
            <td data-label="Commission" className="num px-3 py-2.5 text-right text-ink-soft">{money(totals.commission)}</td>
            <td data-label="Net" className={`num px-3 py-2.5 text-right font-semibold ${totals.net < 0 ? 'text-loss' : 'text-profit'}`}>
              {signed(totals.net)}
            </td>
            <td className="px-5 py-2.5" />
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

function DealsTable({ deals, digitsFor, windowNoun }: {
  deals: Deal[]
  digitsFor: (symbol: string | null | undefined) => number
  windowNoun: string
}) {
  if (deals.length === 0) {
    return <p className="text-sm text-ink-soft py-8">No fills in this {windowNoun}.</p>
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className="desk-label px-5 py-2.5 font-semibold">When</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Deal</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Position</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Symbol</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Side</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Lots</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Price</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Commission</th>
            <th className="desk-label px-5 py-2.5 font-semibold">Kind</th>
          </tr>
        </thead>
        <tbody>
          {deals.map((deal) => (
            <tr key={deal.deal_id} className="border-b border-line last:border-0">
              <td data-label="When" className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                {formatWhen(deal.execution_timestamp)}
              </td>
              <td data-label="Deal" className="num px-3 py-2.5 text-ink-soft">{deal.deal_id}</td>
              <td data-label="Position" className="num px-3 py-2.5 text-ink-soft">{deal.position_id}</td>
              <td data-label="Symbol" className="num px-3 py-2.5">{deal.symbol ?? deal.symbol_id}</td>
              <td data-label="Side" className={`px-3 py-2.5 font-medium ${deal.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                {deal.side}
              </td>
              <td data-label="Lots" className="num px-3 py-2.5 text-right">{deal.volume_lots ?? deal.filled_volume}</td>
              <td data-label="Price" className="num px-3 py-2.5 text-right">
                {price(deal.execution_price, digitsFor(deal.symbol))}
              </td>
              <td data-label="Commission" className="num px-3 py-2.5 text-right text-ink-soft">{money(deal.commission)}</td>
              <td data-label="Kind" className="px-5 py-2.5 text-ink-soft">
                {deal.close ? 'Close' : 'Open'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OrdersTable({ orders, digitsFor, windowNoun }: {
  orders: HistoricalOrder[]
  digitsFor: (symbol: string | null | undefined) => number
  windowNoun: string
}) {
  if (orders.length === 0) {
    return <p className="text-sm text-ink-soft py-8">No orders in this {windowNoun}.</p>
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className="desk-label px-5 py-2.5 font-semibold">Updated</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Order</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Symbol</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Type</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Side</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Lots</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Price</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">SL / TP</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Status</th>
            <th className="desk-label px-5 py-2.5 font-semibold">Label</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => {
            const digits = digitsFor(order.symbol)
            return (
              <tr key={order.order_id} className="border-b border-line last:border-0">
                <td data-label="Updated" className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                  {formatWhen(order.update_timestamp)}
                </td>
                <td data-label="Order" className="num px-3 py-2.5 text-ink-soft">{order.order_id}</td>
                <td data-label="Symbol" className="num px-3 py-2.5">{order.symbol ?? order.symbol_id}</td>
                <td data-label="Type" className="px-3 py-2.5">{order.order_type}</td>
                <td data-label="Side" className={`px-3 py-2.5 font-medium ${order.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                  {order.side}
                </td>
                <td data-label="Lots" className="num px-3 py-2.5 text-right">{order.volume_lots ?? order.volume}</td>
                <td data-label="Price" className="num px-3 py-2.5 text-right">
                  {price(order.execution_price ?? order.limit_price ?? order.stop_price, digits)}
                </td>
                <td data-label="SL / TP" className="num px-3 py-2.5 text-right text-ink-soft whitespace-nowrap">
                  {price(order.stop_loss, digits)} / {price(order.take_profit, digits)}
                </td>
                <td data-label="Status" className="px-3 py-2.5">
                  <span
                    className={`text-xs font-medium px-2 py-0.5 rounded ${
                      order.status === 'FILLED'
                        ? 'bg-profit-wash text-profit-deep'
                        : order.status === 'REJECTED' || order.status === 'CANCELLED'
                          ? 'bg-loss-wash text-loss-deep'
                          : 'bg-line text-ink'
                    }`}
                  >
                    {order.status}
                  </span>
                </td>
                <td data-label="Label" className="px-5 py-2.5 text-ink-soft">{order.label || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
