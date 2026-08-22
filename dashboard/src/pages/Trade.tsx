import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../lib/api'
import { actionBurst } from '../lib/refresh'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import type {
  Account, AccountDetails, ApiState, MarginEstimate, OpenPosition, TradeSymbol,
  Trendbars, WorkingOrder,
} from '../lib/types'
import { Link } from 'react-router-dom'
import Banner from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import SearchSelect from '../components/SearchSelect'
import { CandleChart, PriceLine } from '../components/charts'

const CHART_PERIODS = ['M5', 'M15', 'H1', 'H4', 'D1'] as const
const PERIOD_MS: Record<string, number> = {
  M5: 5 * 60_000, M15: 15 * 60_000, H1: 3_600_000,
  H4: 4 * 3_600_000, D1: 24 * 3_600_000,
}
const CHART_BARS = 120

type Side = 'BUY' | 'SELL'
type OrderType = 'MARKET' | 'LIMIT' | 'STOP'

interface TicketState {
  symbol: string
  side: Side
  orderType: OrderType
  volumeLots: string
  limitPrice: string
  stopPrice: string
  stopLoss: string
  takeProfit: string
}

const EMPTY_TICKET: TicketState = {
  symbol: '', side: 'BUY', orderType: 'MARKET', volumeLots: '0.01',
  limitPrice: '', stopPrice: '', stopLoss: '', takeProfit: '',
}

const defaultSymbolKey = (orgId: number, accountId: number) =>
  `mf.defaultSymbol.${orgId}.${accountId}`

function readDefaultSymbol(orgId: number, accountId: number): string | null {
  try {
    return localStorage.getItem(defaultSymbolKey(orgId, accountId))
  } catch {
    return null
  }
}

function accountLabel(account: Account): string {
  const name = account.nickname || `Account ${account.trader_login}`
  const env = account.is_live ? 'Live' : 'Demo'
  return `${name} · ${account.trader_login} · ${env} (${account.role})`
}

export default function Trade() {
  const { orgId, role } = useOrg()
  // null = not loaded yet; [] = loaded and genuinely empty (onboarding state).
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [accountId, setAccountId] = useState<number | null>(null)
  const accountIdRef = useRef<number | null>(null)
  useEffect(() => {
    accountIdRef.current = accountId
  }, [accountId])
  const [symbols, setSymbols] = useState<TradeSymbol[]>([])
  const [details, setDetails] = useState<AccountDetails | null>(null)
  // Live per-position P&L from the copier's state snapshot, keyed by id.
  const [livePnl, setLivePnl] = useState<Record<number, number | null>>({})
  const [livePrice, setLivePrice] = useState<Record<number, number | null>>({})
  const [ticket, setTicket] = useState<TicketState>(EMPTY_TICKET)
  const [closing, setClosing] = useState<OpenPosition | null>(null)
  // Rows acknowledged instantly while the broker works.
  const [closingIds, setClosingIds] = useState<Set<number>>(new Set())
  const [cancellingIds, setCancellingIds] = useState<Set<number>>(new Set())
  const [partialLots, setPartialLots] = useState('')
  const [cancelling, setCancelling] = useState<WorkingOrder | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Order failures live apart from page-load errors: background refreshes
  // clear `error` on success, but only the user dismisses an order failure.
  const [orderError, setOrderError] = useState<string | null>(null)
  const [quote, setQuote] = useState<{ bid: number | null; ask: number | null } | null>(null)
  const [margin, setMargin] = useState<MarginEstimate | null>(null)
  const [chartPeriod, setChartPeriod] = useState<string>('H1')
  const [chart, setChart] = useState<Trendbars | null>(null)
  // Bumped whenever the pinned default changes, so the pin button re-renders.
  const [, setPinVersion] = useState(0)

  // Load accounts once; default the ticket to the master.
  useEffect(() => {
    const load = async () => {
      try {
        const accs = await orgApi<Account[]>(orgId, 'accounts')
        setAccounts(accs)
        const master = accs.find((a) => a.role === 'master')
        setAccountId(master?.ctid_trader_account_id ?? accs[0]?.ctid_trader_account_id ?? null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load accounts')
      }
    }
    load()
  }, [orgId])

  // Symbols + live positions follow the selected account.
  const loadAccountData = useCallback(async () => {
    if (accountId == null) return
    try {
      const [syms, det, stateEnv] = await Promise.all([
        orgApi<TradeSymbol[]>(orgId, `accounts/${accountId}/symbols`),
        orgApi<AccountDetails>(orgId, `accounts/${accountId}/details`),
        orgApi<ApiState>(orgId, 'state').catch(() => null),
      ])
      // The account may have changed while this request was in flight
      // (routine with the 5s poll): never paint one account's book under
      // another account's header.
      if (accountIdRef.current !== accountId) return
      setSymbols(syms)
      setDetails(det)
      const snap = stateEnv?.accounts?.[String(accountId)]
      const pnlMap: Record<number, number | null> = {}
      const priceMap: Record<number, number | null> = {}
      for (const p of snap?.positions ?? []) {
        pnlMap[p.position_id] = p.pnl_quote ?? null
        priceMap[p.position_id] = p.current_price ?? null
      }
      setLivePnl(pnlMap)
      setLivePrice(priceMap)
      // A pinned default symbol wins; otherwise keep the current pick or
      // fall back to the first cached symbol.
      const pinned = readDefaultSymbol(orgId, accountId)
      setTicket((t) => {
        if (t.symbol && syms.some((s) => s.name === t.symbol)) return t
        const fallback = (pinned && syms.some((s) => s.name === pinned))
          ? pinned
          : syms[0]?.name ?? ''
        return { ...t, symbol: fallback }
      })
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load account data')
    }
  }, [orgId, accountId])

  useEffect(() => {
    loadAccountData()
  }, [loadAccountData])

  // Broker order rejections stream in as control events; without this a
  // weekend MARKET_CLOSED order looked exactly like success.
  useLiveRefresh(loadAccountData, orgId, (evt) => {
    if (evt?.category === 'control' && evt?.payload?.action === 'order_rejected') {
      const code = String(evt.payload.error_code ?? 'UNKNOWN')
      setOrderError(code === 'MARKET_CLOSED'
        ? 'Order rejected: the market is closed. Forex and metals reopen when the trading week starts.'
        : `Order rejected by the broker: ${code}`)
    }
  })

  // Poll fallback: if the websocket drops, positions/orders/balance must not
  // freeze silently on the one page where the user acts on them.
  useEffect(() => {
    if (accountId == null) return
    const interval = setInterval(loadAccountData, 5000)
    return () => clearInterval(interval)
  }, [accountId, loadAccountData])

  // Live bid/ask for the ticket symbol. Asking also subscribes the symbol
  // on the copier's feed, so the first poll may answer nulls and the next
  // one ticks.
  useEffect(() => {
    setQuote(null)
    if (accountId == null || !ticket.symbol) return
    let cancelled = false
    const pull = async () => {
      try {
        const q = await orgApi<{ bid: number | null; ask: number | null }>(
          orgId,
          `accounts/${accountId}/quote?symbol=${encodeURIComponent(ticket.symbol)}`)
        if (!cancelled) setQuote(q)
      } catch {
        // A quote is a nicety; the next poll retries.
      }
    }
    pull()
    const interval = setInterval(pull, 2000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [orgId, accountId, ticket.symbol])

  const selected = accounts?.find((a) => a.ctid_trader_account_id === accountId)

  // Pre-trade margin estimate, debounced against typing in the volume box.
  useEffect(() => {
    if (accountId == null || !ticket.symbol) {
      setMargin(null)
      return
    }
    const lots = parseFloat(ticket.volumeLots)
    if (!Number.isFinite(lots) || lots <= 0) {
      setMargin(null)
      return
    }
    const timer = setTimeout(async () => {
      try {
        setMargin(await orgApi<MarginEstimate>(
          orgId,
          `accounts/${accountId}/margin-estimate` +
          `?symbol=${encodeURIComponent(ticket.symbol)}&volume_lots=${lots}`))
      } catch {
        setMargin(null)
      }
    }, 350)
    return () => clearTimeout(timer)
  }, [orgId, accountId, ticket.symbol, ticket.volumeLots])

  // Price chart for the ticket's symbol; refreshed on a slow poll.
  const loadChart = useCallback(async () => {
    if (accountId == null || !ticket.symbol) return
    const periodMs = PERIOD_MS[chartPeriod] ?? 3_600_000
    const to = Date.now()
    const from = to - CHART_BARS * periodMs
    try {
      setChart(await orgApi<Trendbars>(
        orgId,
        `accounts/${accountId}/trendbars` +
        `?symbol=${encodeURIComponent(ticket.symbol)}&period=${chartPeriod}` +
        `&from=${from}&to=${to}`))
    } catch {
      setChart(null)
    }
  }, [orgId, accountId, ticket.symbol, chartPeriod])

  useEffect(() => {
    loadChart()
    const interval = setInterval(loadChart, 15000)
    return () => clearInterval(interval)
  }, [loadChart])
  const selectedSymbol = symbols.find((s) => s.name === ticket.symbol)

  const numOrNull = (raw: string): number | null => {
    const value = parseFloat(raw)
    return Number.isFinite(value) ? value : null
  }

  const ticketProblem = ((): string | null => {
    if (!selected) return 'Pick an account'
    if (!ticket.symbol) return 'Pick a symbol'
    const lots = numOrNull(ticket.volumeLots)
    if (lots == null || lots <= 0) return 'Volume must be greater than 0'
    if (selectedSymbol?.min_volume_lots != null && lots < selectedSymbol.min_volume_lots) {
      return `Minimum volume for ${ticket.symbol} is ${selectedSymbol.min_volume_lots} lots`
    }
    if (ticket.orderType === 'LIMIT' && numOrNull(ticket.limitPrice) == null) {
      return 'Limit orders need a limit price'
    }
    if (ticket.orderType === 'STOP' && numOrNull(ticket.stopPrice) == null) {
      return 'Stop orders need a stop price'
    }
    return null
  })()

  const submitOrder = async () => {
    if (!selected) return
    try {
      setBusy(true)
      setOrderError(null)
      const body: Record<string, unknown> = {
        account_id: selected.ctid_trader_account_id,
        symbol: ticket.symbol,
        side: ticket.side,
        order_type: ticket.orderType,
        volume_lots: numOrNull(ticket.volumeLots),
      }
      if (ticket.orderType === 'LIMIT') body.limit_price = numOrNull(ticket.limitPrice)
      if (ticket.orderType === 'STOP') body.stop_price = numOrNull(ticket.stopPrice)
      if (numOrNull(ticket.stopLoss) != null) body.stop_loss = numOrNull(ticket.stopLoss)
      if (numOrNull(ticket.takeProfit) != null) body.take_profit = numOrNull(ticket.takeProfit)

      await orgApi(orgId, 'orders', { method: 'POST', body: JSON.stringify(body) })
      setNotice(
        `Order sent: ${ticket.side} ${ticket.volumeLots} ${ticket.symbol} ` +
        `${ticket.orderType.toLowerCase()} on ${selected.nickname || selected.trader_login}. ` +
        'The fill shows up in Positions within a couple of seconds.')
      setError(null)
      actionBurst(loadAccountData)
    } catch (err) {
      setOrderError(`Order failed: ${err instanceof Error ? err.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }

  const submitClose = async () => {
    if (!closing || accountId == null) return
    const positionId = closing.position_id
    setClosingIds((prev) => new Set([...prev, positionId]))
    try {
      setBusy(true)
      const body: Record<string, unknown> = {
        account_id: accountId, position_id: closing.position_id,
      }
      const lots = parseFloat(partialLots)
      if (Number.isFinite(lots) && lots > 0) body.volume_lots = lots
      await orgApi(orgId, 'positions/close', { method: 'POST', body: JSON.stringify(body) })
      setNotice(`Close sent for position ${closing.position_id}.`)
      setClosing(null)
      setPartialLots('')
      actionBurst(loadAccountData)
    } catch (err) {
      setClosing(null)
      setClosingIds((prev) => {
        const next = new Set(prev)
        next.delete(positionId)
        return next
      })
      setError(`Close failed: ${err instanceof Error ? err.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }

  const submitCancel = async () => {
    if (!cancelling || accountId == null) return
    const orderId = cancelling.order_id
    setCancellingIds((prev) => new Set([...prev, orderId]))
    try {
      setBusy(true)
      await orgApi(orgId, 'orders/cancel', {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId, order_id: cancelling.order_id }),
      })
      setNotice(`Cancel sent for order ${cancelling.order_id}.`)
      setCancelling(null)
      actionBurst(loadAccountData)
    } catch (err) {
      setCancelling(null)
      setCancellingIds((prev) => {
        const next = new Set(prev)
        next.delete(orderId)
        return next
      })
      setError(`Cancel failed: ${err instanceof Error ? err.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }

  const priceDigits = selectedSymbol?.digits ?? 5
  // Only this symbol's bars may stand in as a reference price: right after a
  // symbol switch, `chart` still holds the previous symbol for a moment.
  const lastClose = chart?.symbol === ticket.symbol && chart.bars?.length
    ? chart.bars[chart.bars.length - 1].close
    : null
  const liveQuote = quote?.bid != null && quote?.ask != null

  // Category-standard ticket: the sell button carries the bid, buy the ask.
  const sideButton = (side: Side) => {
    const price = side === 'BUY' ? quote?.ask : quote?.bid
    return (
      <button
        key={side}
        type="button"
        onClick={() => setTicket({ ...ticket, side })}
        className={`flex-1 py-2 text-sm font-semibold rounded transition-colors ${
          ticket.side === side
            ? side === 'BUY'
              ? 'bg-profit text-on-accent'
              : 'bg-loss text-on-accent'
            : 'bg-paper text-ink-soft hover:text-ink border border-line'
        }`}
      >
        <span className="block">{side === 'BUY' ? 'Buy' : 'Sell'}</span>
        {/* Always rendered so an arriving quote never shifts the layout. */}
        <span className="num block text-[11px] font-medium opacity-90">
          {price != null ? price.toFixed(priceDigits) : ' '}
        </span>
      </button>
    )
  }

  // The whole order ticket is a trade-level action; the nav link is already
  // hidden below this role, but the page is still reachable by URL, so this
  // gate is belt-and-braces against a direct visit.
  if (!can(role, 'trade')) {
    return (
      <div className="space-y-6">
        <header>
          <h1 className="page-title">Trade</h1>
        </header>
        <p className="text-sm text-ink-soft">
          Your role does not allow placing orders.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="page-title">Trade</h1>
        <p className="text-sm text-ink-soft mt-1 max-w-prose">
          Place orders on any connected account. Master orders replicate to
          every slave; slave orders stay where you put them.
        </p>
      </header>

      {/* Permanently mounted so screen readers reliably announce notices. */}
      <div role="status" className={notice ? undefined : 'sr-only'}>
        {notice && (
          <Banner kind="notice" announce={false} onDismiss={() => setNotice(null)}>
            {notice}
          </Banner>
        )}
      </div>
      {orderError && (
        <Banner kind="error" onDismiss={() => setOrderError(null)}>{orderError}</Banner>
      )}
      {error && <Banner kind="error">{error}</Banner>}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Order ticket */}
        <section className="lg:col-span-2 bg-card rounded-lg border border-line p-5 space-y-4 h-fit">
          <h2 className="desk-label">Order ticket</h2>

          {accounts == null ? (
            <p className="text-sm text-ink-faint">Loading accounts…</p>
          ) : accounts.length === 0 ? (
            <div className="py-4 text-center space-y-3">
              <p className="text-sm text-ink-soft">
                No trading accounts are connected yet.
              </p>
              <Link
                to={`/org/${orgId}/accounts`}
                className="inline-block px-4 py-2.5 bg-brand text-on-accent text-sm font-semibold rounded hover:bg-brand-deep transition-colors"
              >
                Connect a cTrader account
              </Link>
              <p className="text-xs text-ink-faint">
                One grant covers every account under your cTrader ID; orders on
                the master replicate to every enabled slave.
              </p>
            </div>
          ) : (
          <>
          <div>
            <label htmlFor="ticket-account" className="desk-label block mb-1">Account</label>
            <select
              id="ticket-account"
              value={accountId ?? ''}
              onChange={(e) => setAccountId(Number(e.target.value))}
              className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
            >
              {accounts.map((a) => (
                <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
            {selected?.role === 'slave' && (
              <p className="mt-2 text-xs text-warn-deep bg-warn-wash rounded px-2 py-1.5">
                Manual order on a slave: it is not copied anywhere and the
                copier leaves the position for you to manage.
              </p>
            )}
            {selected?.role === 'master' && (
              <p className="mt-2 text-xs text-ink-soft">
                Fills on the master are copied to every enabled slave.
              </p>
            )}
          </div>

          <div>
            <div className="flex items-baseline justify-between mb-1">
              <label htmlFor="ticket-symbol" className="desk-label">Symbol</label>
              {accountId != null && ticket.symbol && (
                readDefaultSymbol(orgId, accountId) === ticket.symbol ? (
                  <button
                    onClick={() => {
                      try { localStorage.removeItem(defaultSymbolKey(orgId, accountId)) } catch { /* private mode */ }
                      setPinVersion((v) => v + 1)
                    }}
                    aria-label="Default symbol — click to clear"
                    className="text-[11px] font-semibold text-brand-deep bg-brand-wash rounded px-1.5 py-0.5 hover:bg-brand hover:text-on-accent transition-colors"
                  >
                    ★ Default
                  </button>
                ) : (
                  <button
                    onClick={() => {
                      try { localStorage.setItem(defaultSymbolKey(orgId, accountId), ticket.symbol) } catch { /* private mode */ }
                      setPinVersion((v) => v + 1)
                    }}
                    className="text-[11px] font-medium text-ink-soft hover:text-brand-deep transition-colors"
                  >
                    ☆ Set as default
                  </button>
                )
              )}
            </div>
            <SearchSelect
              id="ticket-symbol"
              mono
              options={symbols.map((s) => ({ value: s.name, label: s.name }))}
              value={ticket.symbol}
              onChange={(name) => setTicket((t) => ({ ...t, symbol: name }))}
              placeholder="Search symbols…"
              emptyMessage={symbols.length === 0
                ? 'No symbols on this account yet.'
                : 'No symbols match.'}
            />
          </div>

          <div className="flex gap-2">{sideButton('BUY')}{sideButton('SELL')}</div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="ticket-type" className="desk-label block mb-1">Order type</label>
              <select
                id="ticket-type"
                value={ticket.orderType}
                onChange={(e) => setTicket({ ...ticket, orderType: e.target.value as OrderType })}
                className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
              >
                <option value="MARKET">Market</option>
                <option value="LIMIT">Limit</option>
                <option value="STOP">Stop</option>
              </select>
            </div>
            <div>
              <label htmlFor="ticket-volume" className="desk-label block mb-1">Volume (lots)</label>
              <input
                id="ticket-volume"
                type="number"
                step={selectedSymbol?.step_volume_lots ?? 0.01}
                min={selectedSymbol?.min_volume_lots ?? 0.01}
                value={ticket.volumeLots}
                onChange={(e) => setTicket({ ...ticket, volumeLots: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
              />
            </div>
          </div>

          {ticket.orderType === 'LIMIT' && (
            <div>
              <label htmlFor="ticket-limit" className="desk-label block mb-1">Limit price</label>
              <input
                id="ticket-limit" type="number" step="0.00001" value={ticket.limitPrice}
                onChange={(e) => setTicket({ ...ticket, limitPrice: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
              />
            </div>
          )}
          {ticket.orderType === 'STOP' && (
            <div>
              <label htmlFor="ticket-stop" className="desk-label block mb-1">Stop price</label>
              <input
                id="ticket-stop" type="number" step="0.00001" value={ticket.stopPrice}
                onChange={(e) => setTicket({ ...ticket, stopPrice: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="ticket-sl" className="desk-label block mb-1">Stop loss</label>
              <input
                id="ticket-sl" type="number" step="0.00001" value={ticket.stopLoss}
                placeholder="none"
                onChange={(e) => setTicket({ ...ticket, stopLoss: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
              />
            </div>
            <div>
              <label htmlFor="ticket-tp" className="desk-label block mb-1">Take profit</label>
              <input
                id="ticket-tp" type="number" step="0.00001" value={ticket.takeProfit}
                placeholder="none"
                onChange={(e) => setTicket({ ...ticket, takeProfit: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
              />
            </div>
          </div>

          {margin && (
            <p className="text-xs text-ink-soft bg-paper border border-line rounded px-2 py-1.5">
              Margin required ≈{' '}
              <span className="num text-ink">{margin.buy_margin.toFixed(2)}</span> buy ·{' '}
              <span className="num text-ink">{margin.sell_margin.toFixed(2)}</span> sell
              {details?.balance != null && (
                <> · balance <span className="num text-ink">
                  {details.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </span></>
              )}
            </p>
          )}
          {ticketProblem && (
            <p className="text-xs font-medium text-warn-deep">{ticketProblem}</p>
          )}
          {!ticketProblem && selected && (
            <div
              role="note"
              aria-label="Order summary"
              className="text-xs bg-paper border border-line rounded px-3 py-2 space-y-1"
            >
              <div className="flex justify-between gap-2">
                <span className="desk-label">Account</span>
                <span className="num text-right">{accountLabel(selected)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="desk-label">Order</span>
                <span className="text-right">
                  <span className={ticket.side === 'BUY' ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                    {ticket.side}
                  </span>{' '}
                  <span className="num">{ticket.volumeLots}</span> lots{' '}
                  <span className="num">{ticket.symbol}</span> · {ticket.orderType}
                </span>
              </div>
              {liveQuote ? (
                <div className="flex justify-between gap-2">
                  <span className="desk-label">Price now</span>
                  <span className="num">
                    {quote!.bid!.toFixed(priceDigits)} / {quote!.ask!.toFixed(priceDigits)}
                  </span>
                </div>
              ) : lastClose != null ? (
                <div className="flex justify-between gap-2">
                  <span className="desk-label">Reference</span>
                  <span className="num">
                    {`Last close ${lastClose.toFixed(priceDigits)} · indicative`}
                  </span>
                </div>
              ) : null}
              {ticket.orderType === 'LIMIT' && (
                <div className="flex justify-between gap-2">
                  <span className="desk-label">Limit price</span>
                  <span className="num">{ticket.limitPrice}</span>
                </div>
              )}
              {ticket.orderType === 'STOP' && (
                <div className="flex justify-between gap-2">
                  <span className="desk-label">Stop price</span>
                  <span className="num">{ticket.stopPrice}</span>
                </div>
              )}
              {(ticket.stopLoss || ticket.takeProfit) && (
                <div className="flex justify-between gap-2">
                  <span className="desk-label">SL / TP</span>
                  <span className="num">{ticket.stopLoss || '—'} / {ticket.takeProfit || '—'}</span>
                </div>
              )}
              {selected.is_live && (
                <p className="text-loss-deep font-medium">
                  Live account — real money moves when you place this order.
                </p>
              )}
            </div>
          )}
          <button
            onClick={submitOrder}
            disabled={Boolean(ticketProblem) || busy}
            className="w-full py-2.5 rounded bg-brand text-on-accent text-sm font-semibold hover:bg-brand-deep transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? 'Placing…' : 'Place order'}
          </button>
          </>
          )}
        </section>

        {/* Chart + live positions + working orders for the selected account */}
        <section className="lg:col-span-3 space-y-6">
          <div className="bg-card rounded-lg border border-line">
            <div className="px-5 py-3 border-b border-line flex items-baseline justify-between">
              <h2 className="desk-label">Open positions</h2>
              {details?.balance != null && (
                <span className="text-xs text-ink-soft">
                  Balance <span className="num text-ink">{details.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                  {details.deposit_currency ? ` ${details.deposit_currency}` : ''}
                </span>
              )}
            </div>
            {!details || details.open_positions.length === 0 ? (
              <p className="px-5 py-6 text-sm text-ink-faint">
                No open positions on this account.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="stack-table w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-line">
                      <th className="desk-label px-5 py-2 font-semibold">ID</th>
                      <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                      <th className="desk-label px-3 py-2 font-semibold">Side</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Lots</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Entry</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Current</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Live P&L</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">SL / TP</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {details.open_positions.map((pos) => (
                      <tr key={pos.position_id} className="border-b border-line last:border-0">
                        <td data-label="ID" className="num px-5 py-2.5 text-ink-soft">{pos.position_id}</td>
                        <td data-label="Symbol" className="num px-3 py-2.5">{pos.symbol ?? pos.symbol_id}</td>
                        <td data-label="Side" className={`px-3 py-2.5 font-medium ${pos.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                          {pos.side}
                        </td>
                        <td data-label="Lots" className="num px-3 py-2.5 text-right">{pos.volume_lots ?? pos.volume}</td>
                        <td data-label="Entry" className="num px-3 py-2.5 text-right">{pos.price}</td>
                        <td data-label="Current" className={`num px-3 py-2.5 text-right font-medium ${
                          livePrice[pos.position_id] != null ? 'text-brand' : 'text-ink-faint'
                        }`}>
                          {livePrice[pos.position_id] ?? '\u2014'}
                        </td>
                        <td data-label="Live P&L" className={`num px-3 py-2.5 text-right font-medium ${
                          livePnl[pos.position_id] == null ? 'text-ink-faint'
                            : livePnl[pos.position_id]! < 0 ? 'text-loss' : 'text-profit'
                        }`}>
                          {livePnl[pos.position_id] != null
                            ? (livePnl[pos.position_id]! >= 0 ? '+' : '') + livePnl[pos.position_id]!.toFixed(2)
                            : '—'}
                        </td>
                        <td data-label="SL / TP" className="num px-3 py-2.5 text-right text-ink-soft">
                          {pos.stop_loss ?? '—'} / {pos.take_profit ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          {closingIds.has(pos.position_id) ? (
                            <span className="px-3 py-2.5 md:py-1 text-xs font-semibold text-ink-faint animate-pulse motion-reduce:animate-none">
                              Closing…
                            </span>
                          ) : (
                          <button
                            onClick={() => { setClosing(pos); setPartialLots('') }}
                            className="px-3 py-2.5 md:py-1 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors"
                          >
                            Close
                          </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="bg-card rounded-lg border border-line">
            <div className="px-5 py-3 border-b border-line">
              <h2 className="desk-label">Working orders</h2>
            </div>
            {!details || details.pending_orders.length === 0 ? (
              <p className="px-5 py-6 text-sm text-ink-faint">
                No working orders on this account.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="stack-table w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-line">
                      <th className="desk-label px-5 py-2 font-semibold">ID</th>
                      <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                      <th className="desk-label px-3 py-2 font-semibold">Type</th>
                      <th className="desk-label px-3 py-2 font-semibold">Side</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Lots</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Price</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {details.pending_orders.map((order) => (
                      <tr key={order.order_id} className="border-b border-line last:border-0">
                        <td data-label="ID" className="num px-5 py-2.5 text-ink-soft">{order.order_id}</td>
                        <td data-label="Symbol" className="num px-3 py-2.5">{order.symbol ?? order.symbol_id}</td>
                        <td data-label="Type" className="px-3 py-2.5">{order.order_type}</td>
                        <td data-label="Side" className={`px-3 py-2.5 font-medium ${order.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                          {order.side}
                        </td>
                        <td data-label="Lots" className="num px-3 py-2.5 text-right">{order.volume_lots ?? order.volume}</td>
                        <td data-label="Price" className="num px-3 py-2.5 text-right">
                          {order.limit_price ?? order.stop_price ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          {cancellingIds.has(order.order_id) ? (
                            <span className="px-3 py-2.5 md:py-1 text-xs font-semibold text-ink-faint animate-pulse motion-reduce:animate-none">
                              Cancelling…
                            </span>
                          ) : (
                          <button
                            onClick={() => setCancelling(order)}
                            className="px-3 py-2.5 md:py-1 text-xs font-semibold rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors"
                          >
                            Cancel order
                          </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

        </section>
      </div>

      {/* The chart keeps out of the way of the desk: full width, below the fold. */}
      <div className="bg-card rounded-lg border border-line p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="desk-label">
            {ticket.symbol || 'Price'} · {chartPeriod}
          </h2>
          <div className="flex gap-1">
            {CHART_PERIODS.map((p) => (
              <button
                key={p}
                onClick={() => setChartPeriod(p)}
                className={`min-h-11 min-w-11 md:min-h-0 md:min-w-0 px-2 py-1 text-xs rounded transition-colors ${
                  chartPeriod === p
                    ? 'bg-brand text-on-accent font-semibold'
                    : 'text-ink-soft hover:text-ink'
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
        {chart && chart.bars.length > 0 ? (
          <CandleChart
            bars={chart.bars}
            lines={(details?.open_positions ?? [])
              .filter((pos) => pos.symbol === ticket.symbol)
              .flatMap((pos): PriceLine[] => {
                const lines: PriceLine[] = [
                  { price: pos.price, label: `entry ${pos.position_id}`, kind: 'entry' },
                ]
                if (pos.stop_loss != null) lines.push({ price: pos.stop_loss, label: 'SL', kind: 'sl' })
                if (pos.take_profit != null) lines.push({ price: pos.take_profit, label: 'TP', kind: 'tp' })
                return lines
              })}
            digits={symbols.find((s) => s.name === ticket.symbol)?.digits ?? 5}
          />
        ) : (
          <p className="text-sm text-ink-faint py-10 text-center">
            No candles for this symbol yet — the copier may still be
            connecting, or the market has no bars in this range.
          </p>
        )}
      </div>


      {/* Close position */}
      <ConfirmDialog
        open={closing != null}
        title={`Close position ${closing?.position_id ?? ''}`}
        confirmLabel="Close position"
        danger
        busy={busy}
        onConfirm={submitClose}
        onCancel={() => setClosing(null)}
      >
        <p>
          {closing?.side} <span className="num">{closing?.volume_lots ?? closing?.volume}</span> lots{' '}
          <span className="num">{closing?.symbol}</span> closes at market.
        </p>
        <div>
          <label htmlFor="partial-lots" className="desk-label block mb-1">
            Lots to close (leave empty for all)
          </label>
          <input
            id="partial-lots"
            type="number"
            step="0.01"
            min="0.01"
            value={partialLots}
            placeholder={closing?.volume_lots ?? 'all'}
            onChange={(e) => setPartialLots(e.target.value)}
            className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
          />
        </div>
      </ConfirmDialog>

      {/* Cancel order */}
      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel order ${cancelling?.order_id ?? ''}`}
        confirmLabel="Cancel this order"
        danger
        busy={busy}
        onConfirm={submitCancel}
        onCancel={() => setCancelling(null)}
      >
        <p>
          The working {cancelling?.order_type} order for{' '}
          <span className="num">{cancelling?.symbol}</span> is removed from the
          broker. Nothing is closed.
        </p>
      </ConfirmDialog>
    </div>
  )
}
