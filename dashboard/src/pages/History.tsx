import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Account, CashFlowEntry, Deal, HistoricalOrder } from '../lib/types'

const WEEK_MS = 7 * 24 * 3600 * 1000

type Tab = 'closed' | 'deals' | 'orders' | 'cashflow'

function accountLabel(account: Account): string {
  const name = account.nickname || `Account ${account.trader_login}`
  return `${name} · ${account.trader_login} · ${account.is_live ? 'Live' : 'Demo'}`
}

function formatWhen(ms: number | null | undefined): string {
  if (!ms) return '—'
  const d = new Date(ms)
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function formatMoney(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function signed(value: number): string {
  const formatted = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
  return `${value < 0 ? '-' : '+'}${formatted}`
}

function price(value: number | null | undefined, digits = 5): string {
  return value == null ? '—' : value.toFixed(digits)
}

/**
 * Account-wise trade history straight from the broker: closed positions
 * (reconstructed from closing deals, exactly how cTrader's own History tab
 * works), every raw deal, and the order log.  cTrader caps history requests
 * at one week, so the desk pages by week.
 */
export default function History() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  // End of the visible week (ms). Start is always end - 1 week.
  const [windowEnd, setWindowEnd] = useState<number>(() => Date.now())
  const [tab, setTab] = useState<Tab>('closed')
  const [deals, setDeals] = useState<Deal[]>([])
  const [orders, setOrders] = useState<HistoricalOrder[]>([])
  const [cashFlow, setCashFlow] = useState<CashFlowEntry[]>([])
  const [drillPosition, setDrillPosition] = useState<number | null>(null)
  const [drillDeals, setDrillDeals] = useState<Deal[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const accs = await api<Account[]>('/api/accounts')
        setAccounts(accs)
        const master = accs.find((a) => a.role === 'master')
        setAccountId(master?.ctid_trader_account_id ?? accs[0]?.ctid_trader_account_id ?? null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load accounts')
      }
    }
    load()
  }, [])

  const loadHistory = useCallback(async () => {
    if (accountId == null) return
    const from = windowEnd - WEEK_MS
    const query = `from=${from}&to=${windowEnd}`
    try {
      setLoading(true)
      setError(null)
      const [dealsRes, ordersRes, cashRes] = await Promise.all([
        api<{ deals: Deal[]; has_more: boolean }>(
          `/api/accounts/${accountId}/history/deals?${query}`),
        api<{ orders: HistoricalOrder[]; has_more: boolean }>(
          `/api/accounts/${accountId}/history/orders?${query}`),
        api<{ entries: CashFlowEntry[] }>(
          `/api/accounts/${accountId}/history/cashflow?${query}`).catch(() => ({ entries: [] })),
      ])
      setDeals(dealsRes.deals)
      setOrders(ordersRes.orders)
      setCashFlow(cashRes.entries)
      setHasMore(dealsRes.has_more || ordersRes.has_more)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }, [accountId, windowEnd])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  const closingDeals = deals.filter((d) => d.close != null)
  const from = windowEnd - WEEK_MS

  const openDrill = async (positionId: number) => {
    setDrillPosition(positionId)
    setDrillDeals(null)
    if (accountId == null) return
    try {
      const result = await api<{ deals: Deal[] }>(
        `/api/accounts/${accountId}/positions/${positionId}/deals?from=0&to=${Date.now()}`)
      setDrillDeals(result.deals)
    } catch {
      setDrillDeals([])
    }
  }

  const tabButton = (key: Tab, label: string, count: number) => (
    <button
      key={key}
      onClick={() => setTab(key)}
      className={`px-4 py-2 text-sm rounded-t border-b-2 transition-colors ${
        tab === key
          ? 'border-brand text-brand font-semibold'
          : 'border-transparent text-ink-soft hover:text-ink'
      }`}
    >
      {label} <span className="num text-xs text-ink-faint">{count}</span>
    </button>
  )

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="font-display text-2xl text-ink">History</h1>
        <p className="text-sm text-ink-soft mt-1">
          Closed positions, fills, and orders for any account, straight from
          the broker. One week at a time — that is the most cTrader hands out
          per request.
        </p>
      </header>

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor="history-account" className="desk-label block mb-1">Account</label>
          <select
            id="history-account"
            value={accountId ?? ''}
            onChange={(e) => setAccountId(Number(e.target.value))}
            className="rounded border border-line-strong px-3 py-2 text-sm bg-card min-w-64"
          >
            {accounts.map((a) => (
              <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                {accountLabel(a)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setWindowEnd(windowEnd - WEEK_MS)}
            className="px-3 py-2 text-sm rounded border border-line-strong text-ink-soft hover:text-ink transition-colors"
          >
            ← Earlier
          </button>
          <span className="num text-sm text-ink-soft min-w-44 text-center">
            {new Date(from).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}
            {' – '}
            {new Date(windowEnd).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}
          </span>
          <button
            onClick={() => setWindowEnd(Math.min(windowEnd + WEEK_MS, Date.now()))}
            disabled={windowEnd >= Date.now()}
            className="px-3 py-2 text-sm rounded border border-line-strong text-ink-soft hover:text-ink transition-colors disabled:opacity-40"
          >
            Later →
          </button>
        </div>
        {hasMore && (
          <p className="text-xs text-warn">
            This week has more rows than one request returns; narrow the range
            to see everything.
          </p>
        )}
      </div>

      {error && (
        <div className="rounded border border-loss/30 bg-loss-wash px-4 py-3 text-sm text-loss-deep">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-line flex gap-1">
        {tabButton('closed', 'Closed positions', closingDeals.length)}
        {tabButton('deals', 'Deals', deals.length)}
        {tabButton('orders', 'Orders', orders.length)}
        {tabButton('cashflow', 'Cash flow', cashFlow.length)}
      </div>

      {loading ? (
        <p className="text-sm text-ink-faint py-8">Loading history…</p>
      ) : tab === 'closed' ? (
        <ClosedPositionsTable deals={closingDeals} onDrill={openDrill} />
      ) : tab === 'deals' ? (
        <DealsTable deals={deals} />
      ) : tab === 'orders' ? (
        <OrdersTable orders={orders} />
      ) : (
        <CashFlowTable entries={cashFlow} />
      )}

      {/* Position drill-down drawer */}
      {drillPosition != null && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-ink/30"
          onClick={() => setDrillPosition(null)}
        >
          <aside
            className="w-full max-w-lg h-full bg-card border-l border-line overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
            role="complementary"
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
                onClick={() => setDrillPosition(null)}
                aria-label="Close position details"
                className="text-ink-soft hover:text-ink text-xl leading-none"
              >
                ×
              </button>
            </div>
            {drillDeals == null ? (
              <p className="px-6 py-4 text-sm text-ink-faint">Fetching from the broker…</p>
            ) : drillDeals.length === 0 ? (
              <p className="px-6 py-4 text-sm text-ink-faint">
                The broker returned no deals for this position.
              </p>
            ) : (
              <table className="w-full text-sm">
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
                      <td className="num px-6 py-2.5 text-ink-soft whitespace-nowrap">
                        {formatWhen(deal.execution_timestamp)}
                      </td>
                      <td className={`px-3 py-2.5 font-medium ${deal.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                        {deal.side}
                        <span className="text-xs text-ink-faint ml-1">
                          {deal.close ? 'close' : 'open'}
                        </span>
                      </td>
                      <td className="num px-3 py-2.5 text-right">
                        {deal.volume_lots ?? deal.filled_volume}
                      </td>
                      <td className="num px-3 py-2.5 text-right">{price(deal.execution_price)}</td>
                      <td className={`num px-6 py-2.5 text-right ${
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

function CashFlowTable({ entries }: { entries: CashFlowEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-ink-faint py-8">
        No deposits or withdrawals in this week.
      </p>
    )
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="w-full text-sm">
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
                <td className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                  {formatWhen(entry.timestamp)}
                </td>
                <td className="px-3 py-2.5">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                    isDeposit ? 'bg-profit-wash text-profit' : 'bg-loss-wash text-loss'
                  }`}>
                    {entry.type}
                  </span>
                </td>
                <td className={`num px-3 py-2.5 text-right font-medium ${
                  isDeposit ? 'text-profit' : 'text-loss'
                }`}>
                  {isDeposit ? '+' : '-'}{Math.abs(entry.amount).toLocaleString('en-US', {
                    minimumFractionDigits: 2, maximumFractionDigits: 2,
                  })}
                </td>
                <td className="num px-3 py-2.5 text-right">{formatMoney(entry.balance_after)}</td>
                <td className="px-5 py-2.5 text-ink-soft">{entry.note || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ClosedPositionsTable({ deals, onDrill }: {
  deals: Deal[]
  onDrill: (positionId: number) => void
}) {
  if (deals.length === 0) {
    return (
      <p className="text-sm text-ink-faint py-8">
        No positions were closed in this week. Page back to look earlier.
      </p>
    )
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="w-full text-sm">
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
            <th className="desk-label px-5 py-2.5 font-semibold text-right">Balance after</th>
          </tr>
        </thead>
        <tbody>
          {deals.map((deal) => {
            const close = deal.close!
            // The closing deal's side is the exit; the position itself was the opposite.
            const positionSide = deal.side === 'SELL' ? 'BUY' : 'SELL'
            return (
              <tr key={deal.deal_id} className="border-b border-line last:border-0">
                <td className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                  {formatWhen(deal.execution_timestamp)}
                </td>
                <td className="num px-3 py-2.5">
                  <button
                    onClick={() => onDrill(deal.position_id)}
                    aria-label={`View position ${deal.position_id}`}
                    className="text-brand hover:text-brand-deep underline decoration-dotted underline-offset-2"
                  >
                    {deal.position_id}
                  </button>
                </td>
                <td className="num px-3 py-2.5">{deal.symbol ?? deal.symbol_id}</td>
                <td className={`px-3 py-2.5 font-medium ${positionSide === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                  {positionSide}
                </td>
                <td className="num px-3 py-2.5 text-right">
                  {close.closed_volume_lots ?? close.closed_volume}
                </td>
                <td className="num px-3 py-2.5 text-right whitespace-nowrap">
                  {price(close.entry_price)} → {price(deal.execution_price)}
                </td>
                <td className={`num px-3 py-2.5 text-right font-medium ${close.gross_profit < 0 ? 'text-loss' : 'text-profit'}`}>
                  {signed(close.gross_profit)}
                </td>
                <td className="num px-3 py-2.5 text-right text-ink-soft">{formatMoney(close.swap)}</td>
                <td className="num px-3 py-2.5 text-right text-ink-soft">{formatMoney(close.commission)}</td>
                <td className="num px-5 py-2.5 text-right">{formatMoney(close.balance)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function DealsTable({ deals }: { deals: Deal[] }) {
  if (deals.length === 0) {
    return <p className="text-sm text-ink-faint py-8">No fills in this week.</p>
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="w-full text-sm">
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
              <td className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                {formatWhen(deal.execution_timestamp)}
              </td>
              <td className="num px-3 py-2.5 text-ink-soft">{deal.deal_id}</td>
              <td className="num px-3 py-2.5 text-ink-soft">{deal.position_id}</td>
              <td className="num px-3 py-2.5">{deal.symbol ?? deal.symbol_id}</td>
              <td className={`px-3 py-2.5 font-medium ${deal.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                {deal.side}
              </td>
              <td className="num px-3 py-2.5 text-right">{deal.volume_lots ?? deal.filled_volume}</td>
              <td className="num px-3 py-2.5 text-right">{price(deal.execution_price)}</td>
              <td className="num px-3 py-2.5 text-right text-ink-soft">{formatMoney(deal.commission)}</td>
              <td className="px-5 py-2.5 text-ink-soft">
                {deal.close ? 'Close' : 'Open'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OrdersTable({ orders }: { orders: HistoricalOrder[] }) {
  if (orders.length === 0) {
    return <p className="text-sm text-ink-faint py-8">No orders in this week.</p>
  }
  return (
    <div className="bg-card rounded-lg border border-line overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className="desk-label px-5 py-2.5 font-semibold">Updated</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Order</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Symbol</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Type</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Side</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Lots</th>
            <th className="desk-label px-3 py-2.5 font-semibold text-right">Price</th>
            <th className="desk-label px-3 py-2.5 font-semibold">Status</th>
            <th className="desk-label px-5 py-2.5 font-semibold">Label</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.order_id} className="border-b border-line last:border-0">
              <td className="num px-5 py-2.5 text-ink-soft whitespace-nowrap">
                {formatWhen(order.update_timestamp)}
              </td>
              <td className="num px-3 py-2.5 text-ink-soft">{order.order_id}</td>
              <td className="num px-3 py-2.5">{order.symbol ?? order.symbol_id}</td>
              <td className="px-3 py-2.5">{order.order_type}</td>
              <td className={`px-3 py-2.5 font-medium ${order.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                {order.side}
              </td>
              <td className="num px-3 py-2.5 text-right">{order.volume_lots ?? order.volume}</td>
              <td className="num px-3 py-2.5 text-right">
                {price(order.execution_price ?? order.limit_price ?? order.stop_price)}
              </td>
              <td className="px-3 py-2.5">
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded ${
                    order.status === 'FILLED'
                      ? 'bg-profit-wash text-profit'
                      : order.status === 'REJECTED' || order.status === 'CANCELLED'
                        ? 'bg-loss-wash text-loss'
                        : 'bg-paper text-ink-soft'
                  }`}
                >
                  {order.status}
                </span>
              </td>
              <td className="px-5 py-2.5 text-ink-soft">{order.label || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
