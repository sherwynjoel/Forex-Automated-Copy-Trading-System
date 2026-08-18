import { useState, useEffect, useRef } from 'react'
import { api, eventsSocket } from '../lib/api'
import { EventResponse } from '../lib/types'

export default function Logs() {
  const [events, setEvents] = useState<EventResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [isLive, setIsLive] = useState(true)
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())

  // Filter state
  const [filters, setFilters] = useState({
    account_id: '',
    severity: 'all',
    category: '',
    since: '',
  })

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // Fetch events when filters change
  useEffect(() => {
    const fetchEvents = async () => {
      setLoading(true)
      try {
        const params = new URLSearchParams()
        if (filters.account_id) {
          params.append('account_id', filters.account_id)
        }
        if (filters.severity && filters.severity !== 'all') {
          params.append('severity', filters.severity)
        }
        if (filters.category) {
          params.append('category', filters.category)
        }
        if (filters.since) {
          params.append('since', filters.since)
        }
        params.append('limit', '100')

        const url = `/api/events${params.toString() ? '?' + params.toString() : ''}`
        const data = await api<EventResponse[]>(url)
        setEvents(data || [])
      } catch (err) {
        console.error('Failed to fetch events:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchEvents()
  }, [filters])

  // Connect to WebSocket
  useEffect(() => {
    if (!isLive) {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
      return
    }

    const connectWebSocket = () => {
      const ws = eventsSocket()

      ws.onmessage = (event) => {
        try {
          const newEvent = JSON.parse(event.data) as EventResponse
          // Only add if it matches current filters (account_id, severity, category).
          // Note: 'since' filters history; live events are "now" so we don't filter by since.
          if (
            (!filters.account_id || newEvent.account_id?.toString() === filters.account_id) &&
            (filters.severity === 'all' || newEvent.severity === filters.severity) &&
            (!filters.category || newEvent.category === filters.category)
          ) {
            setEvents((prev) => [newEvent, ...prev])
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err)
        }
      }

      ws.onclose = () => {
        wsRef.current = null
        // Reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000)
      }

      ws.onerror = (error) => {
        console.error('WebSocket error:', error)
        ws.close()
      }

      wsRef.current = ws
    }

    connectWebSocket()

    return () => {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
    }
  }, [isLive, filters])

  const toggleRowExpand = (id: number) => {
    const newExpanded = new Set(expandedRows)
    if (newExpanded.has(id)) {
      newExpanded.delete(id)
    } else {
      newExpanded.add(id)
    }
    setExpandedRows(newExpanded)
  }

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'error':
        return 'bg-loss-wash text-loss-deep'
      case 'warning':
        return 'bg-warn-wash text-warn'
      case 'info':
        return 'bg-brand-wash text-brand'
      default:
        return 'bg-paper text-ink'
    }
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="font-display text-2xl text-ink">Logs</h1>
        <p className="text-sm text-ink-soft mt-1">
          The append-only audit trail: every master event, copy action,
          connection change, and control command.
        </p>
      </header>

      {/* Filters */}
      <div className="bg-card rounded-lg border border-line p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="desk-label">Filters</h3>
          <label className="flex items-center space-x-2">
            <input
              type="checkbox"
              checked={isLive}
              onChange={(e) => setIsLive(e.target.checked)}
              className="rounded accent-brand"
            />
            <span className="text-sm font-medium text-ink">Live</span>
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {/* Account Select */}
          <div>
            <label className="block desk-label mb-1">Account</label>
            <input
              type="text"
              placeholder="Account ID"
              value={filters.account_id}
              onChange={(e) => setFilters({ ...filters, account_id: e.target.value })}
              className="w-full px-3 py-2 border border-line-strong rounded-md text-sm bg-card"
            />
          </div>

          {/* Severity Select */}
          <div>
            <label className="block desk-label mb-1">Severity</label>
            <select
              value={filters.severity}
              onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
              className="w-full px-3 py-2 border border-line-strong rounded-md text-sm bg-card"
            >
              <option value="all">all</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
          </div>

          {/* Category Select */}
          <div>
            <label className="block desk-label mb-1">Category</label>
            <input
              type="text"
              placeholder="Category"
              value={filters.category}
              onChange={(e) => setFilters({ ...filters, category: e.target.value })}
              className="w-full px-3 py-2 border border-line-strong rounded-md text-sm bg-card"
            />
          </div>

          {/* Date Since Select */}
          <div>
            <label className="block desk-label mb-1">Since</label>
            <input
              type="datetime-local"
              value={filters.since}
              onChange={(e) => setFilters({ ...filters, since: e.target.value })}
              className="w-full px-3 py-2 border border-line-strong rounded-md text-sm bg-card"
            />
          </div>
        </div>
      </div>

      {/* Events Table */}
      <div className="bg-card rounded-lg border border-line overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-line">
            <thead className="bg-paper">
              <tr>
                <th className="desk-label px-6 py-3 text-left">
                  Timestamp
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Account
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Category
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Severity
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Latency (ms)
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Payload
                </th>
              </tr>
            </thead>
            <tbody className="bg-card divide-y divide-line">
              {loading && (
                <tr>
                  <td colSpan={6} className="px-6 py-4 text-center text-ink-faint">
                    Loading...
                  </td>
                </tr>
              )}
              {!loading && events.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-4 text-center text-ink-faint">
                    No events found
                  </td>
                </tr>
              )}
              {events.map((event) => (
                <tr key={event.id} className="hover:bg-paper">
                  <td className="num px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.ts}
                  </td>
                  <td className="num px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.account_id || '-'}
                  </td>
                  <td className="num px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.category}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    <span className={`px-3 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${getSeverityColor(event.severity)}`}>
                      {event.severity}
                    </span>
                  </td>
                  <td className="num px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.latency_ms ?? '-'}
                  </td>
                  <td className="px-6 py-4 text-sm text-ink">
                    <button
                      onClick={() => toggleRowExpand(event.id)}
                      className="text-brand hover:text-brand-deep underline"
                    >
                      {expandedRows.has(event.id) ? 'Hide' : 'Show'}
                    </button>
                    {expandedRows.has(event.id) && (
                      <div className="num mt-2 p-3 bg-paper rounded text-xs whitespace-pre-wrap">
                        {JSON.stringify(event.payload, null, 2)}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
