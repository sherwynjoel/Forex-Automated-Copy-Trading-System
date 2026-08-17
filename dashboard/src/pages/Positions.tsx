import { useEffect, useState } from 'react'
import { ApiState, MasterPosition, PendingOrder, PositionCopy, DriftItem } from '../lib/types'
import { api } from '../lib/api'
import { useLiveRefresh } from '../hooks/useLiveRefresh'

export default function Positions() {
  const [state, setState] = useState<ApiState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedPositions, setExpandedPositions] = useState<Set<number>>(new Set())
  const [expandedOrders, setExpandedOrders] = useState<Set<number>>(new Set())

  const fetchState = async () => {
    try {
      setError(null)
      const data = await api<ApiState>('/api/state')
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
  }, [])

  // Refetch immediately when a trade event streams in (5s poll is fallback)
  useLiveRefresh(fetchState)

  const handleCloseOrphan = async (driftId: string) => {
    if (!window.confirm('Close this orphan position?')) return

    try {
      await api('/api/drift/close-orphan', {
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
      await api('/api/drift/adopt', {
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
      await api('/api/drift/dismiss', {
        method: 'POST',
        body: JSON.stringify({ id: driftId }),
      })
      await fetchState()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to dismiss')
    }
  }

  const togglePositionExpanded = (positionId: number) => {
    const newSet = new Set(expandedPositions)
    if (newSet.has(positionId)) {
      newSet.delete(positionId)
    } else {
      newSet.add(positionId)
    }
    setExpandedPositions(newSet)
  }

  const toggleOrderExpanded = (orderId: number) => {
    const newSet = new Set(expandedOrders)
    if (newSet.has(orderId)) {
      newSet.delete(orderId)
    } else {
      newSet.add(orderId)
    }
    setExpandedOrders(newSet)
  }

  if (loading && !state) {
    return <div className="p-6">Loading positions...</div>
  }

  if (error && !state) {
    return <div className="p-6 text-red-600">Error: {error}</div>
  }

  if (!state) {
    return <div className="p-6">No data available</div>
  }

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-3xl font-bold">Positions</h1>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded text-red-700">
          {error}
        </div>
      )}

      {/* Master Positions Section */}
      <section>
        <h2 className="text-2xl font-semibold mb-4">Master Positions</h2>
        {state.master_positions.length === 0 ? (
          <p className="text-gray-500">No open master positions</p>
        ) : (
          <div className="overflow-x-auto border rounded">
            <table className="w-full border-collapse">
              <thead className="bg-gray-100">
                <tr>
                  <th className="border p-3 text-left font-semibold">Symbol</th>
                  <th className="border p-3 text-left font-semibold">Side</th>
                  <th className="border p-3 text-right font-semibold">Volume (units)</th>
                  <th className="border p-3 text-right font-semibold">Entry Price</th>
                  <th className="border p-3 text-right font-semibold">P&L</th>
                  <th className="border p-3 text-center font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {state.master_positions.map((pos) => (
                  <PositionRow
                    key={pos.position_id}
                    position={pos}
                    isExpanded={expandedPositions.has(pos.position_id)}
                    onToggleExpand={() => togglePositionExpanded(pos.position_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Pending Orders Section */}
      <section>
        <h2 className="text-2xl font-semibold mb-4">Pending Orders</h2>
        {state.pending_orders.length === 0 ? (
          <p className="text-gray-500">No pending orders</p>
        ) : (
          <div className="overflow-x-auto border rounded">
            <table className="w-full border-collapse">
              <thead className="bg-gray-100">
                <tr>
                  <th className="border p-3 text-left font-semibold">Symbol</th>
                  <th className="border p-3 text-right font-semibold">Volume (units)</th>
                  <th className="border p-3 text-center font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {state.pending_orders.map((order) => (
                  <OrderRow
                    key={order.order_id}
                    order={order}
                    isExpanded={expandedOrders.has(order.order_id)}
                    onToggleExpand={() => toggleOrderExpanded(order.order_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Drift/Orphan Section */}
      <section>
        <h2 className="text-2xl font-semibold mb-4">Drift Items</h2>
        {state.drift.length === 0 ? (
          <p className="text-gray-500">No drift items</p>
        ) : (
          <div className="space-y-3">
            {state.drift.map((drift) => (
              <DriftItemRow
                key={drift.id}
                drift={drift}
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
  isExpanded,
  onToggleExpand,
}: {
  position: MasterPosition
  isExpanded: boolean
  onToggleExpand: () => void
}) {
  return (
    <>
      <tr className="border-t hover:bg-gray-50">
        <td className="border p-3">{position.symbol || `ID:${position.symbol_id}`}</td>
        <td className="border p-3">{position.side}</td>
        <td className="border p-3 text-right">{position.volume_lots || position.volume}</td>
        <td className="border p-3 text-right">{position.price.toFixed(5)}</td>
        <td className="border p-3 text-right">
          {position.pnl_quote != null ? position.pnl_quote.toFixed(2) : '-'}
        </td>
        <td className="border p-3 text-center">
          <button
            onClick={onToggleExpand}
            className="px-3 py-1 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
          >
            {isExpanded ? 'Hide' : 'Show'} Copies
          </button>
        </td>
      </tr>
      {isExpanded && position.copies.length > 0 && (
        <tr className="bg-gray-50 border-t">
          <td colSpan={6} className="p-4">
            <div className="ml-4 space-y-2">
              <h4 className="font-semibold mb-2">Slave Copies</h4>
              <table className="w-full text-sm border">
                <thead className="bg-gray-200">
                  <tr>
                    <th className="border p-2 text-left">Account</th>
                    <th className="border p-2 text-left">Status</th>
                    <th className="border p-2 text-right">Fill Price</th>
                    <th className="border p-2 text-right">Slippage (pts)</th>
                    <th className="border p-2 text-left">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {position.copies.map((copy) => (
                    <CopyRow key={`${copy.slave_account_id}-${copy.slave_position_id}`} copy={copy} entryPrice={position.price} />
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
  isExpanded,
  onToggleExpand,
}: {
  order: PendingOrder
  isExpanded: boolean
  onToggleExpand: () => void
}) {
  return (
    <>
      <tr className="border-t hover:bg-gray-50">
        <td className="border p-3">{order.symbol || `ID:${order.symbol_id}`}</td>
        <td className="border p-3 text-right">{order.volume_lots || order.volume}</td>
        <td className="border p-3 text-center">
          {order.copies.length > 0 && (
            <button
              onClick={onToggleExpand}
              className="px-3 py-1 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
            >
              {isExpanded ? 'Hide' : 'Show'} Copies
            </button>
          )}
        </td>
      </tr>
      {isExpanded && order.copies.length > 0 && (
        <tr className="bg-gray-50 border-t">
          <td colSpan={3} className="p-4">
            <div className="ml-4 space-y-2">
              <h4 className="font-semibold mb-2">Slave Copies</h4>
              <table className="w-full text-sm border">
                <thead className="bg-gray-200">
                  <tr>
                    <th className="border p-2 text-left">Account</th>
                    <th className="border p-2 text-left">Status</th>
                    <th className="border p-2 text-left">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {order.copies.map((copy) => (
                    <tr key={`${copy.slave_account_id}-${copy.slave_order_id}`}>
                      <td className="border p-2">{copy.slave_account_id}</td>
                      <td className="border p-2">{copy.status}</td>
                      <td className="border p-2">{copy.error || '-'}</td>
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
}: {
  copy: PositionCopy
  entryPrice: number
}) {
  // Show "—" if fill_price is not available from backend
  const hasFillPrice = copy.fill_price != null
  const slippageDisplay = hasFillPrice
    ? ((copy.fill_price! - entryPrice) * 10000).toFixed(1)
    : '—'
  const fillPriceDisplay = hasFillPrice ? copy.fill_price!.toFixed(5) : '—'

  return (
    <tr className="border-t hover:bg-gray-100">
      <td className="border p-2">{copy.slave_account_id}</td>
      <td className="border p-2">{copy.status}</td>
      <td className="border p-2 text-right">{fillPriceDisplay}</td>
      <td className="border p-2 text-right">{slippageDisplay}</td>
      <td className="border p-2">{copy.error || '-'}</td>
    </tr>
  )
}

function DriftItemRow({
  drift,
  onCloseOrphan,
  onAdopt,
  onDismiss,
}: {
  drift: DriftItem
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
    <div className="border rounded p-4 bg-white">
      <div className="mb-3">
        <p className="font-semibold">{kindDisplay}</p>
        <p className="text-sm text-gray-600">{drift.detail}</p>
      </div>
      <div className="flex gap-2">
        {isOrphanSlave && (
          <button
            onClick={onCloseOrphan}
            className="px-3 py-2 bg-red-500 text-white rounded hover:bg-red-600 text-sm"
          >
            Close Orphan
          </button>
        )}
        {isOrphanSlave && (
          <button
            onClick={onAdopt}
            className="px-3 py-2 bg-green-500 text-white rounded hover:bg-green-600 text-sm"
          >
            Adopt
          </button>
        )}
        <button
          onClick={onDismiss}
          className="px-3 py-2 bg-gray-500 text-white rounded hover:bg-gray-600 text-sm"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
