import { useEffect, useState } from 'react'
import { ApiState, MasterPosition, PendingOrder, PositionCopy, DriftItem } from '../lib/types'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import Banner from '../components/Banner'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { mergeTicksIntoApiState, TicksPayload } from '../lib/ticks'

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
  useLiveRefresh(fetchState, orgId, (evt) => {
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
                  <th className="desk-label p-3 text-right border-b border-line">P&L</th>
                  
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
                  <th className="desk-label p-3 text-left border-b border-line">Symbol</th>
                  <th className="desk-label p-3 text-right border-b border-line">Volume (units)</th>
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
}: {
  position: MasterPosition
  pnlFor: (accountId: number, positionId: number | null | undefined) => number | null
  priceFor: (accountId: number, positionId: number | null | undefined) => number | null
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
        <td data-label="P&L" className={`num p-3 text-right font-medium ${
          position.pnl_quote == null ? '' : position.pnl_quote < 0 ? 'text-loss' : 'text-profit'
        }`}>
          {position.pnl_quote != null
            ? (position.pnl_quote >= 0 ? '+' : '') + position.pnl_quote.toFixed(2)
            : '-'}
        </td>
      </tr>
      {position.copies.length > 0 && (
        <tr className="bg-paper border-b border-line">
          <td colSpan={6} className="p-4">
            <div className="ml-4 space-y-2">
              <h4 className="desk-label mb-2">Slave Copies</h4>
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr>
                    <th className="desk-label p-2 text-left border-b border-line">Account</th>
                    <th className="desk-label p-2 text-left border-b border-line">Status</th>
                    <th className="desk-label p-2 text-right border-b border-line">Fill Price</th>
                    <th className="desk-label p-2 text-right border-b border-line">Slippage (pts)</th>
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
                      entryPrice={position.price}
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
        <td data-label="Symbol" className="num p-3">{order.symbol || `ID:${order.symbol_id}`}</td>
        <td data-label="Volume (units)" className="num p-3 text-right">{order.volume_lots || order.volume}</td>
      </tr>
      {order.copies.length > 0 && (
        <tr className="bg-paper border-b border-line">
          <td colSpan={2} className="p-4">
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
  entryPrice,
  livePnl,
  livePrice,
}: {
  copy: PositionCopy
  entryPrice: number
  livePnl: number | null
  livePrice: number | null
}) {
  // Show "—" if fill_price is not available from backend
  const hasFillPrice = copy.fill_price != null
  const slippageDisplay = hasFillPrice
    ? ((copy.fill_price! - entryPrice) * 10000).toFixed(1)
    : '—'
  const fillPriceDisplay = hasFillPrice ? copy.fill_price!.toFixed(5) : '—'

  return (
    <tr className="border-b border-line last:border-0">
      <td data-label="Account" className="num p-2">{copy.slave_account_id}</td>
      <td data-label="Status" className="num p-2">{copy.status}</td>
      <td data-label="Fill Price" className="num p-2 text-right">{fillPriceDisplay}</td>
      <td data-label="Slippage (pts)" className="num p-2 text-right">{slippageDisplay}</td>
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
