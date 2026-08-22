import { useEffect, useState } from 'react'
import { ApiState, MasterPosition, PendingOrder, PositionCopy, DriftItem } from '../lib/types'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import Banner from '../components/Banner'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import ConfirmDialog from '../components/ConfirmDialog'
import { actionBurst } from '../lib/refresh'
import { mergeTicksIntoApiState, TicksPayload } from '../lib/ticks'

/**
 * Why a stop loss / take profit pair would be refused, or null if it is
 * sound. The broker rejects the WHOLE amend when either side is wrong, so
 * a bad take profit silently discards an edit to the stop too -- which is
 * exactly what "the stop moved but the target didn't" looks like from the
 * outside.
 *
 * A stop is the losing side of the market, a target the winning side:
 * BUY  -> stop below, target above
 * SELL -> stop above, target below
 */
export function protectionProblem(
  side: string,
  reference: number | null | undefined,
  stopLoss: number | null,
  takeProfit: number | null,
): string | null {
  if (reference == null) return null // no price to judge against
  const isBuy = side.toUpperCase() === 'BUY'
  const px = reference.toString()
  if (stopLoss != null) {
    if (isBuy && stopLoss >= reference) {
      return `On a BUY the stop loss has to sit below the market (${px}).`
    }
    if (!isBuy && stopLoss <= reference) {
      return `On a SELL the stop loss has to sit above the market (${px}).`
    }
  }
  if (takeProfit != null) {
    if (isBuy && takeProfit <= reference) {
      return `On a BUY the take profit has to sit above the market (${px}).`
    }
    if (!isBuy && takeProfit >= reference) {
      return `On a SELL the take profit has to sit below the market (${px}).`
    }
  }
  return null
}


export default function Positions() {
  const { orgId, role } = useOrg()
  const [state, setState] = useState<ApiState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchState = async () => {
    try {
      setError(null)
      const data = await orgApi<ApiState>(orgId, 'state')
      setState(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch state')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchState()
    const interval = setInterval(fetchState, 5000)
    return () => clearInterval(interval)
  }, [orgId])

  // Refetch immediately when a trade event streams in (5s poll is fallback)
  // The position whose protection is being edited, and the draft values.
  const [editing, setEditing] = useState<MasterPosition | null>(null)
  const [slDraft, setSlDraft] = useState('')
  const [tpDraft, setTpDraft] = useState('')
  const [amendBusy, setAmendBusy] = useState(false)
  // Broker refusals stream in as control events. Kept apart from `error`
  // (the page-load failure) so a background refresh cannot wipe it: only
  // the reader dismisses it.
  const [brokerError, setBrokerError] = useState<string | null>(null)

  // Pre-fill from the position: the broker's amend replaces BOTH values,
  // so editing one must not silently clear the other.
  useEffect(() => {
    setSlDraft(editing?.stop_loss != null ? String(editing.stop_loss) : '')
    setTpDraft(editing?.take_profit != null ? String(editing.take_profit) : '')
  }, [editing])

  const draftPrice = (raw: string): number | null => {
    const trimmed = raw.trim()
    if (trimmed === '') return null
    const value = Number(trimmed)
    return Number.isFinite(value) ? value : null
  }

  // Judged against the live mark, falling back to the entry price.
  const amendProblem = editing
    ? protectionProblem(
        editing.side,
        editing.current_price ?? editing.price,
        draftPrice(slDraft),
        draftPrice(tpDraft))
    : null

  const submitAmend = async () => {
    if (!editing || amendProblem) return
    setAmendBusy(true)
    setError(null)
    try {
      await orgApi(orgId, 'positions/amend', {
        method: 'POST',
        body: JSON.stringify({
          account_id: editing.account_id,
          position_id: editing.position_id,
          stop_loss: draftPrice(slDraft),
          take_profit: draftPrice(tpDraft),
        }),
      })
      setEditing(null)
      await fetchState()
      actionBurst(fetchState)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update SL/TP')
    } finally {
      setAmendBusy(false)
    }
  }

  useLiveRefresh(fetchState, orgId, (evt) => {
    if (evt?.category === 'control' && evt?.payload?.action === 'order_rejected') {
      const code = String(evt.payload.error_code ?? 'UNKNOWN')
      setBrokerError(code === 'TRADING_BAD_STOPS'
        ? 'The broker refused those levels (TRADING_BAD_STOPS): a stop loss '
          + 'must sit on the losing side of the market and a take profit on '
          + 'the winning side, both far enough away to be accepted. Nothing '
          + 'was changed.'
        : code === 'MARKET_CLOSED'
          ? 'The market is closed, so the broker refused the change.'
          : `The broker refused the change: ${code}`)
      return
    }
    // Quotes ticks update marks and P&L in place; structural changes
    // still arrive through the refetch path above.
    if (evt?.category !== 'quotes') return
    const ticks = (evt.payload as TicksPayload | undefined)?.accounts
    if (!ticks) return
    setState((prev) => (prev ? mergeTicksIntoApiState(prev, ticks) : prev))
  })

  const handleCloseOrphan = async (driftId: string) => {
    if (!window.confirm('Close this orphan position?')) return

    try {
      await orgApi(orgId, 'drift/close-orphan', {
        method: 'POST',
        body: JSON.stringify({ id: driftId }),
      })
      await fetchState()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to close orphan')
    }
  }

  const handleAdopt = async (driftId: string, driftDetail: string) => {
    // Try to parse master position id from label (format: copy:m<id>)
    let master_position_id: number | null = null
    const match = driftDetail.match(/copy:m(\d+)/)
    if (match) {
      master_position_id = parseInt(match[1], 10)
    }

    // If not found in label, prompt user
    if (!master_position_id) {
      const input = prompt('Enter master position ID to adopt to:')
      if (!input) return
      master_position_id = parseInt(input, 10)
      if (isNaN(master_position_id)) {
        setError('Invalid master position ID')
        return
      }
    }

    try {
      await orgApi(orgId, 'drift/adopt', {
        method: 'POST',
        body: JSON.stringify({ id: driftId, master_position_id }),
      })
      await fetchState()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to adopt')
    }
  }

  const handleDismiss = async (driftId: string) => {
    try {
      await orgApi(orgId, 'drift/dismiss', {
        method: 'POST',
        body: JSON.stringify({ id: driftId }),
      })
      await fetchState()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to dismiss')
    }
  }

  if (loading && !state) {
    return <div className="p-6">Loading positions...</div>
  }

  if (error && !state) {
    return <div className="p-6"><Banner kind="error">{error}</Banner></div>
  }

  if (!state) {
    return <div className="p-6">No data available</div>
  }

  return (
    <div className="space-y-8 max-w-6xl">
      <header>
        <h1 className="page-title">Positions</h1>
        <p className="text-sm text-ink-soft mt-1">
          The master's open book and how each slave copy tracks it. Close or
          trim positions from the Trade page.
        </p>
      </header>

      {error && (
        <div className="p-4 bg-loss-wash border border-loss/30 rounded text-loss-deep">
          {error}
        </div>
      )}

      {brokerError && (
        <Banner kind="error" onDismiss={() => setBrokerError(null)}>{brokerError}</Banner>
      )}

      {/* Master Positions Section */}
      <section>
        <h2 className="font-display text-xl text-ink mb-4">Master Positions</h2>
        {state.master_positions.length === 0 ? (
          <p className="text-ink-faint">No open master positions</p>
        ) : (
          <div className="overflow-x-auto bg-card border border-line rounded-lg">
            <table className="stack-table w-full border-collapse">
              <thead>
                <tr>
                  <th className="desk-label p-3 text-left border-b border-line">Symbol</th>
                  <th className="desk-label p-3 text-left border-b border-line">Side</th>
                  <th className="desk-label p-3 text-right border-b border-line">Volume (units)</th>
                  <th className="desk-label p-3 text-right border-b border-line">Entry Price</th>
                  <th className="desk-label p-3 text-right border-b border-line">Current</th>
                  <th className="desk-label p-3 text-right border-b border-line whitespace-nowrap">SL / TP</th>
                  <th className="desk-label p-3 text-right border-b border-line">P&L</th>
                  {can(role, 'trade') && <th className="p-3 border-b border-line" />}
                  
                </tr>
              </thead>
              <tbody>
                {state.master_positions.map((pos) => (
                  <PositionRow
                    key={pos.position_id}
                    position={pos}
                    pnlFor={(accountId, positionId) => {
                      const snap = state.accounts?.[String(accountId)]
                      const found = snap?.positions?.find((p) => p.position_id === positionId)
                      return found?.pnl_quote ?? null
                    }}
                    canTrade={can(role, 'trade')}
                    onEdit={() => setEditing(pos)}
                    priceFor={(accountId, positionId) => {
                      const snap = state.accounts?.[String(accountId)]
                      const found = snap?.positions?.find((p) => p.position_id === positionId)
                      return found?.current_price ?? null
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Pending Orders Section */}
      <section>
        <h2 className="font-display text-xl text-ink mb-4">Pending Orders</h2>
        {state.pending_orders.length === 0 ? (
          <p className="text-ink-faint">No pending orders</p>
        ) : (
          <div className="overflow-x-auto bg-card border border-line rounded-lg">
            <table className="stack-table w-full border-collapse">
              <thead>
                <tr>
                  <th className="desk-label p-3 text-left border-b border-line">ID</th>
                  <th className="desk-label p-3 text-left border-b border-line">Symbol</th>
                  <th className="desk-label p-3 text-left border-b border-line">Type</th>
                  <th className="desk-label p-3 text-left border-b border-line">Side</th>
                  <th className="desk-label p-3 text-right border-b border-line">Lots</th>
                  <th className="desk-label p-3 text-right border-b border-line">Price</th>
                </tr>
              </thead>
              <tbody>
                {state.pending_orders.map((order) => (
                  <OrderRow
                    key={order.order_id}
                    order={order}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={editing != null}
        title={`Stop loss / take profit · ${editing?.symbol ?? ''}`}
        confirmLabel="Update protection"
        busy={amendBusy}
        disabled={amendProblem != null}
        onConfirm={submitAmend}
        onCancel={() => setEditing(null)}
      >
        <p>
          Applies to the master position and, through the normal copy path,
          to every slave copy of it.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="amend-sl" className="desk-label block mb-1">Stop loss</label>
            <input
              id="amend-sl"
              type="number"
              step="0.00001"
              value={slDraft}
              onChange={(e) => setSlDraft(e.target.value)}
              placeholder="none"
              className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
            />
          </div>
          <div>
            <label htmlFor="amend-tp" className="desk-label block mb-1">Take profit</label>
            <input
              id="amend-tp"
              type="number"
              step="0.00001"
              value={tpDraft}
              onChange={(e) => setTpDraft(e.target.value)}
              placeholder="none"
              className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
            />
          </div>
        </div>
        <p className="text-xs text-ink-faint">
          Leaving a field empty removes that protection — the broker replaces
          both together.
        </p>
        {amendProblem && (
          <p role="alert" className="text-sm text-loss-deep bg-loss-wash border border-loss/30 rounded px-3 py-2">
            {amendProblem} The broker would refuse the whole change, losing
            the other value with it.
          </p>
        )}
      </ConfirmDialog>

      {/* Drift/Orphan Section */}
      <section>
        <h2 className="font-display text-xl text-ink mb-4">Drift Items</h2>
        {state.drift.length === 0 ? (
          <p className="text-ink-faint">No drift items</p>
        ) : (
          <div className="space-y-3">
            {state.drift.map((drift) => (
              <DriftItemRow
                key={drift.id}
                drift={drift}
                canClose={can(role, 'trade')}
                canRemedy={can(role, 'control')}
                onCloseOrphan={() => handleCloseOrphan(drift.id)}
                onAdopt={() => handleAdopt(drift.id, drift.detail)}
                onDismiss={() => handleDismiss(drift.id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function PositionRow({
  position,
  pnlFor,
  priceFor,
  canTrade,
  onEdit,
}: {
  position: MasterPosition
  pnlFor: (accountId: number, positionId: number | null | undefined) => number | null
  priceFor: (accountId: number, positionId: number | null | undefined) => number | null
  canTrade: boolean
  onEdit: () => void
}) {
  return (
    <>
      <tr className="border-b border-line last:border-0 hover:bg-line">
        <td data-label="Symbol" className="num p-3">{position.symbol || `ID:${position.symbol_id}`}</td>
        <td data-label="Side" className="num p-3">{position.side}</td>
        <td data-label="Volume (units)" className="num p-3 text-right">{position.volume_lots || position.volume}</td>
        <td data-label="Entry Price" className="num p-3 text-right">{position.price.toFixed(5)}</td>
        <td data-label="Current" className={`num p-3 text-right font-medium ${
          position.current_price != null ? 'text-brand' : 'text-ink-faint'
        }`}>
          {position.current_price != null ? position.current_price.toFixed(5) : '\u2014'}
        </td>
        <td data-label="SL / TP" className="num p-3 text-right text-ink-soft whitespace-nowrap">
          {position.stop_loss ?? '—'} / {position.take_profit ?? '—'}
        </td>
        <td data-label="P&L" className={`num p-3 text-right font-medium ${
          position.pnl_quote == null ? '' : position.pnl_quote < 0 ? 'text-loss' : 'text-profit'
        }`}>
          {position.pnl_quote != null
            ? (position.pnl_quote >= 0 ? '+' : '') + position.pnl_quote.toFixed(2)
            : '-'}
        </td>
        {canTrade && (
          <td className="p-3 text-right">
            <button
              onClick={onEdit}
              className="min-h-11 md:min-h-0 px-3 py-1 text-xs font-semibold rounded border border-line-strong text-ink hover:bg-line transition-colors"
            >
              SL / TP
            </button>
          </td>
        )}
      </tr>
      {position.copies.length > 0 && (
        <tr className="bg-paper border-b border-line">
          <td colSpan={8} className="p-4">
            <div className="ml-4 space-y-2">
              <h4 className="desk-label mb-2">Slave Copies</h4>
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr>
                    <th className="desk-label p-2 text-left border-b border-line">Account</th>
                    <th className="desk-label p-2 text-left border-b border-line">Status</th>
                    <th className="desk-label p-2 text-right border-b border-line">Fill Price</th>
                    <th className="desk-label p-2 text-right border-b border-line whitespace-nowrap">SL / TP</th>
                    <th className="desk-label p-2 text-right border-b border-line">Current</th>
                    <th className="desk-label p-2 text-right border-b border-line">Live P&L</th>
                    <th className="desk-label p-2 text-left border-b border-line">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {position.copies.map((copy) => (
                    <CopyRow
                      key={`${copy.slave_account_id}-${copy.slave_position_id}`}
                      copy={copy}
                      livePnl={pnlFor(copy.slave_account_id, copy.slave_position_id)}
                      livePrice={priceFor(copy.slave_account_id, copy.slave_position_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function OrderRow({
  order,
}: {
  order: PendingOrder
}) {
  return (
    <>
      <tr className="border-b border-line last:border-0 hover:bg-line">
        <td data-label="ID" className="num p-3 text-ink-soft">{order.order_id}</td>
        <td data-label="Symbol" className="num p-3">{order.symbol || `ID:${order.symbol_id}`}</td>
        <td data-label="Type" className="num p-3">{order.order_type ?? '—'}</td>
        <td data-label="Side" className={`p-3 font-medium ${
          order.side === 'BUY' ? 'text-profit' : order.side === 'SELL' ? 'text-loss' : ''
        }`}>
          {order.side ?? '—'}
        </td>
        <td data-label="Lots" className="num p-3 text-right">{order.volume_lots || order.volume}</td>
        <td data-label="Price" className="num p-3 text-right font-medium text-brand">
          {order.price ?? '—'}
        </td>
      </tr>
      {order.copies.length > 0 && (
        <tr className="bg-paper border-b border-line">
          <td colSpan={6} className="p-4">
            <div className="ml-4 space-y-2">
              <h4 className="desk-label mb-2">Slave Copies</h4>
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="desk-label p-2 text-left border-b border-line">Account</th>
                    <th className="desk-label p-2 text-left border-b border-line">Status</th>
                    <th className="desk-label p-2 text-left border-b border-line">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {order.copies.map((copy) => (
                    <tr key={`${copy.slave_account_id}-${copy.slave_order_id}`}>
                      <td className="num p-2">{copy.slave_account_id}</td>
                      <td className="num p-2">{copy.status}</td>
                      <td className="num p-2">{copy.error || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function CopyRow({
  copy,
  livePnl,
  livePrice,
}: {
  copy: PositionCopy
  livePnl: number | null
  livePrice: number | null
}) {
  // Show "—" if fill_price is not available from backend
  const fillPriceDisplay = copy.fill_price != null
    ? copy.fill_price.toFixed(5) : '—'

  return (
    <tr className="border-b border-line last:border-0">
      <td data-label="Account" className="num p-2">{copy.slave_account_id}</td>
      <td data-label="Status" className="num p-2">{copy.status}</td>
      <td data-label="Fill Price" className="num p-2 text-right">{fillPriceDisplay}</td>
      {/* A copy can be live without the protection its master carries --
          say so rather than leaving the operator to assume it followed. */}
      <td data-label="SL / TP" className={`num p-2 text-right whitespace-nowrap ${
        copy.status === 'active' && copy.stop_loss == null && copy.take_profit == null
          ? 'text-warn-deep' : 'text-ink-soft'
      }`}>
        {copy.stop_loss ?? '—'} / {copy.take_profit ?? '—'}
      </td>
      <td data-label="Current" className={`num p-2 text-right font-medium ${
        livePrice != null ? 'text-brand' : 'text-ink-faint'
      }`}>
        {livePrice != null ? livePrice.toFixed(5) : '\u2014'}
      </td>
      <td data-label="Live P&L" className={`num p-2 text-right font-medium ${
        livePnl == null ? 'text-ink-faint' : livePnl < 0 ? 'text-loss' : 'text-profit'
      }`}>
        {livePnl != null ? (livePnl >= 0 ? '+' : '') + livePnl.toFixed(2) : '—'}
      </td>
      <td data-label="Error" className="num p-2">{copy.error || '-'}</td>
    </tr>
  )
}

function DriftItemRow({
  drift,
  canClose,
  canRemedy,
  onCloseOrphan,
  onAdopt,
  onDismiss,
}: {
  drift: DriftItem
  canClose: boolean
  canRemedy: boolean
  onCloseOrphan: () => void
  onAdopt: () => void
  onDismiss: () => void
}) {
  const isOrphanSlave = drift.kind === 'orphan_slave_position'
  const kindDisplay = drift.kind
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')

  return (
    <div className="border border-line rounded-lg p-4 bg-card">
      <div className="mb-3">
        <p className="font-semibold">{kindDisplay}</p>
        <p className="text-sm text-ink-soft">{drift.detail}</p>
      </div>
      <div className="flex gap-2">
        {isOrphanSlave && canClose && (
          <button
            onClick={onCloseOrphan}
            className="px-3 py-2 bg-loss text-on-accent rounded hover:bg-loss-deep text-sm font-medium transition-colors"
          >
            Close Orphan
          </button>
        )}
        {isOrphanSlave && canRemedy && (
          <button
            onClick={onAdopt}
            className="px-3 py-2 bg-brand text-on-accent rounded hover:bg-brand-deep text-sm font-medium transition-colors"
          >
            Adopt
          </button>
        )}
        {canRemedy && (
          <button
            onClick={onDismiss}
            className="px-3 py-2 rounded border border-line-strong text-ink-soft hover:text-ink text-sm font-medium transition-colors"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  )
}
