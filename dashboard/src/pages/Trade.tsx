import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import type {
  Account, AccountDetails, OpenPosition, TradeSymbol, WorkingOrder,
} from '../lib/types'
import ConfirmDialog from '../components/ConfirmDialog'

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

function accountLabel(account: Account): string {
  const name = account.nickname || `Account ${account.trader_login}`
  const env = account.is_live ? 'Live' : 'Demo'
  return `${name} · ${account.trader_login} · ${env} (${account.role})`
}

export default function Trade() {
  const { orgId, role } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  const [symbols, setSymbols] = useState<TradeSymbol[]>([])
  const [details, setDetails] = useState<AccountDetails | null>(null)
  const [ticket, setTicket] = useState<TicketState>(EMPTY_TICKET)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [closing, setClosing] = useState<OpenPosition | null>(null)
  const [partialLots, setPartialLots] = useState('')
  const [cancelling, setCancelling] = useState<WorkingOrder | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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
      const [syms, det] = await Promise.all([
        orgApi<TradeSymbol[]>(orgId, `accounts/${accountId}/symbols`),
        orgApi<AccountDetails>(orgId, `accounts/${accountId}/details`),
      ])
      setSymbols(syms)
      setDetails(det)
      setTicket((t) => (t.symbol && syms.some((s) => s.name === t.symbol)
        ? t : { ...t, symbol: syms[0]?.name ?? '' }))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load account data')
    }
  }, [orgId, accountId])

  useEffect(() => {
    loadAccountData()
  }, [loadAccountData])

  useLiveRefresh(loadAccountData, orgId)

  const selected = accounts.find((a) => a.ctid_trader_account_id === accountId)
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
      setReviewOpen(false)
      setError(null)
      setTimeout(loadAccountData, 1500)
    } catch (err) {
      setReviewOpen(false)
      setError(`Order failed: ${err instanceof Error ? err.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }

  const submitClose = async () => {
    if (!closing || accountId == null) return
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
      setTimeout(loadAccountData, 1500)
    } catch (err) {
      setClosing(null)
      setError(`Close failed: ${err instanceof Error ? err.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }

  const submitCancel = async () => {
    if (!cancelling || accountId == null) return
    try {
      setBusy(true)
      await orgApi(orgId, 'orders/cancel', {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId, order_id: cancelling.order_id }),
      })
      setNotice(`Cancel sent for order ${cancelling.order_id}.`)
      setCancelling(null)
      setTimeout(loadAccountData, 1500)
    } catch (err) {
      setCancelling(null)
      setError(`Cancel failed: ${err instanceof Error ? err.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }

  const sideButton = (side: Side) => (
    <button
      key={side}
      type="button"
      onClick={() => setTicket({ ...ticket, side })}
      className={`flex-1 py-2 text-sm font-semibold rounded transition-colors ${
        ticket.side === side
          ? side === 'BUY'
            ? 'bg-profit text-white'
            : 'bg-loss text-white'
          : 'bg-paper text-ink-soft hover:text-ink border border-line'
      }`}
    >
      {side === 'BUY' ? 'Buy' : 'Sell'}
    </button>
  )

  // The whole order ticket is a trade-level action; the nav link is already
  // hidden below this role, but the page is still reachable by URL, so this
  // gate is belt-and-braces against a direct visit.
  if (!can(role, 'trade')) {
    return (
      <div className="space-y-6 max-w-5xl">
        <header>
          <h1 className="font-display text-2xl text-ink">Trade</h1>
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
        <h1 className="font-display text-2xl text-ink">Trade</h1>
        <p className="text-sm text-ink-soft mt-1">
          Place orders on any connected account. Master orders replicate to
          every slave; slave orders stay where you put them.
        </p>
      </header>

      {notice && (
        <div className="rounded border border-line bg-brand-wash px-4 py-3 text-sm text-ink flex justify-between items-center">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} className="text-xs font-medium text-ink-soft hover:text-ink">
            Dismiss
          </button>
        </div>
      )}
      {error && (
        <div className="rounded border border-loss/30 bg-loss-wash px-4 py-3 text-sm text-loss-deep">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Order ticket */}
        <section className="lg:col-span-2 bg-card rounded-lg border border-line p-5 space-y-4 h-fit">
          <h2 className="desk-label">Order ticket</h2>

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
              <p className="mt-2 text-xs text-warn bg-warn-wash rounded px-2 py-1.5">
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
            <label htmlFor="ticket-symbol" className="desk-label block mb-1">Symbol</label>
            <select
              id="ticket-symbol"
              value={ticket.symbol}
              onChange={(e) => setTicket({ ...ticket, symbol: e.target.value })}
              className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card"
            >
              {symbols.map((s) => (
                <option key={s.symbol_id} value={s.name}>{s.name}</option>
              ))}
            </select>
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
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm"
              />
            </div>
          </div>

          {ticket.orderType === 'LIMIT' && (
            <div>
              <label htmlFor="ticket-limit" className="desk-label block mb-1">Limit price</label>
              <input
                id="ticket-limit" type="number" step="0.00001" value={ticket.limitPrice}
                onChange={(e) => setTicket({ ...ticket, limitPrice: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm"
              />
            </div>
          )}
          {ticket.orderType === 'STOP' && (
            <div>
              <label htmlFor="ticket-stop" className="desk-label block mb-1">Stop price</label>
              <input
                id="ticket-stop" type="number" step="0.00001" value={ticket.stopPrice}
                onChange={(e) => setTicket({ ...ticket, stopPrice: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm"
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
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="ticket-tp" className="desk-label block mb-1">Take profit</label>
              <input
                id="ticket-tp" type="number" step="0.00001" value={ticket.takeProfit}
                placeholder="none"
                onChange={(e) => setTicket({ ...ticket, takeProfit: e.target.value })}
                className="num w-full rounded border border-line-strong px-3 py-2 text-sm"
              />
            </div>
          </div>

          {ticketProblem && (
            <p className="text-xs text-ink-faint">{ticketProblem}</p>
          )}
          <button
            onClick={() => setReviewOpen(true)}
            disabled={Boolean(ticketProblem) || busy}
            className="w-full py-2.5 rounded bg-brand text-white text-sm font-semibold hover:bg-brand-deep transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Review order
          </button>
        </section>

        {/* Live positions + working orders for the selected account */}
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
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-line">
                      <th className="desk-label px-5 py-2 font-semibold">ID</th>
                      <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                      <th className="desk-label px-3 py-2 font-semibold">Side</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Lots</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">Entry</th>
                      <th className="desk-label px-3 py-2 font-semibold text-right">SL / TP</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {details.open_positions.map((pos) => (
                      <tr key={pos.position_id} className="border-b border-line last:border-0">
                        <td className="num px-5 py-2.5 text-ink-soft">{pos.position_id}</td>
                        <td className="num px-3 py-2.5">{pos.symbol ?? pos.symbol_id}</td>
                        <td className={`px-3 py-2.5 font-medium ${pos.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                          {pos.side}
                        </td>
                        <td className="num px-3 py-2.5 text-right">{pos.volume_lots ?? pos.volume}</td>
                        <td className="num px-3 py-2.5 text-right">{pos.price}</td>
                        <td className="num px-3 py-2.5 text-right text-ink-soft">
                          {pos.stop_loss ?? '—'} / {pos.take_profit ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          <button
                            onClick={() => { setClosing(pos); setPartialLots('') }}
                            className="px-3 py-1 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-white transition-colors"
                          >
                            Close
                          </button>
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
                <table className="w-full text-sm">
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
                        <td className="num px-5 py-2.5 text-ink-soft">{order.order_id}</td>
                        <td className="num px-3 py-2.5">{order.symbol ?? order.symbol_id}</td>
                        <td className="px-3 py-2.5">{order.order_type}</td>
                        <td className={`px-3 py-2.5 font-medium ${order.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                          {order.side}
                        </td>
                        <td className="num px-3 py-2.5 text-right">{order.volume_lots ?? order.volume}</td>
                        <td className="num px-3 py-2.5 text-right">
                          {order.limit_price ?? order.stop_price ?? '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          <button
                            onClick={() => setCancelling(order)}
                            className="px-3 py-1 text-xs font-semibold rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors"
                          >
                            Cancel order
                          </button>
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

      {/* Order review */}
      <ConfirmDialog
        open={reviewOpen}
        title="Review order"
        confirmLabel="Place order"
        busy={busy}
        onConfirm={submitOrder}
        onCancel={() => setReviewOpen(false)}
      >
        <dl className="space-y-1.5">
          <div className="flex justify-between">
            <dt className="desk-label">Account</dt>
            <dd className="num">{selected ? accountLabel(selected) : ''}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="desk-label">Order</dt>
            <dd>
              <span className={ticket.side === 'BUY' ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                {ticket.side}
              </span>{' '}
              <span className="num">{ticket.volumeLots}</span> lots{' '}
              <span className="num">{ticket.symbol}</span> · {ticket.orderType}
            </dd>
          </div>
          {ticket.orderType === 'LIMIT' && (
            <div className="flex justify-between">
              <dt className="desk-label">Limit price</dt>
              <dd className="num">{ticket.limitPrice}</dd>
            </div>
          )}
          {ticket.orderType === 'STOP' && (
            <div className="flex justify-between">
              <dt className="desk-label">Stop price</dt>
              <dd className="num">{ticket.stopPrice}</dd>
            </div>
          )}
          {(ticket.stopLoss || ticket.takeProfit) && (
            <div className="flex justify-between">
              <dt className="desk-label">SL / TP</dt>
              <dd className="num">{ticket.stopLoss || '—'} / {ticket.takeProfit || '—'}</dd>
            </div>
          )}
        </dl>
        {selected?.role === 'master' && (
          <p className="text-xs">
            This order fills on the master and is copied to every enabled slave.
          </p>
        )}
        {selected?.is_live && (
          <p className="text-xs text-loss-deep font-medium">
            This is a live account. Real money moves when you place this order.
          </p>
        )}
      </ConfirmDialog>

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
            className="num w-full rounded border border-line-strong px-3 py-2 text-sm"
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
