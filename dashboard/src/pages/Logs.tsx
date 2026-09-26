import { useState, useEffect, useRef } from 'react'
import { orgApi, eventsSocket } from '../lib/api'
import { useOrg } from '../lib/org'
import { formatWhen } from '../lib/format'
import { EventResponse } from '../lib/types'
import Button from '../components/Button'
import Input from '../components/Input'
import Select from '../components/Select'
import Badge, { type BadgeTone } from '../components/Badge'

export default function Logs() {
  const { orgId } = useOrg()
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

        const url = `events${params.toString() ? '?' + params.toString() : ''}`
        const data = await orgApi<EventResponse[]>(orgId, url)
        setEvents(data || [])
      } catch (err) {
        console.error('Failed to fetch events:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchEvents()
  }, [orgId, filters])

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
      const ws = eventsSocket(orgId)

      ws.onmessage = (event) => {
        try {
          const newEvent = JSON.parse(event.data) as EventResponse
          // The socket also carries the quotes price stream (and any future
          // relay frames): only DATABASE events -- which always have an id
          // and timestamp -- belong in the audit trail.
          if (newEvent.id == null || newEvent.ts == null) return
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
  }, [orgId, isLive, filters])

  const toggleRowExpand = (id: number) => {
    const newExpanded = new Set(expandedRows)
    if (newExpanded.has(id)) {
      newExpanded.delete(id)
    } else {
      newExpanded.add(id)
    }
    setExpandedRows(newExpanded)
  }

  const severityTone = (severity: string): BadgeTone => {
    switch (severity) {
      case 'error':
        return 'loss'
      case 'warning':
        return 'warn'
      case 'info':
        return 'brand'
      default:
        return 'neutral'
    }
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="page-title">Logs</h1>
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
            <Input
              type="text"
              placeholder="Account ID"
              value={filters.account_id}
              onChange={(e) => setFilters({ ...filters, account_id: e.target.value })}
            />
          </div>

          {/* Severity Select */}
          <div>
            <label className="block desk-label mb-1">Severity</label>
            <Select
              block
              value={filters.severity}
              onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
            >
              <option value="all">all</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </Select>
          </div>

          {/* Category Select */}
          <div>
            <label className="block desk-label mb-1">Category</label>
            <Input
              type="text"
              placeholder="Category"
              value={filters.category}
              onChange={(e) => setFilters({ ...filters, category: e.target.value })}
            />
          </div>

          {/* Date Since Select */}
          <div>
            <label className="block desk-label mb-1">Since</label>
            <Input
              type="datetime-local"
              value={filters.since}
              onChange={(e) => setFilters({ ...filters, since: e.target.value })}
            />
          </div>
        </div>
      </div>

      {/* Events Table */}
      <div className="bg-card rounded-lg border border-line overflow-hidden">
        <div className="overflow-x-auto">
          <table className="stack-table min-w-full divide-y divide-line">
            <thead className="bg-paper">
              <tr>
                <th className="desk-label px-6 py-3 text-left">
                  Timestamp
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Account
                </th>
                <th className="desk-label px-6 py-3 text-left">
                  Who
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
                  <td colSpan={7} className="px-6 py-4 text-center text-ink-faint">
                    Loading...
                  </td>
                </tr>
              )}
              {!loading && events.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-4 text-center text-ink-faint">
                    No events found
                  </td>
                </tr>
              )}
              {events.map((event) => (
                <tr key={event.id} className="hover:bg-line">
                  <td data-label="Timestamp" className="num px-6 py-4 whitespace-nowrap text-sm text-ink" title={event.ts}>
                    {formatWhen(event.ts)}
                  </td>
                  <td data-label="Account" className="num px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.account_id || '-'}
                  </td>
                  <td data-label="Who" className="px-6 py-4 whitespace-nowrap text-sm text-ink-soft">
                    {/* Operator-initiated actions name the person; the
                        copier's own work (copy fills, reconnects) is the
                        system acting, not a user. */}
                    {event.actor_email ?? <span className="text-ink-faint">system</span>}
                  </td>
                  <td data-label="Category" className="px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.category}
                  </td>
                  <td data-label="Severity" className="px-6 py-4 whitespace-nowrap text-sm">
                    <Badge tone={severityTone(event.severity)} pill>
                      {event.severity}
                    </Badge>
                  </td>
                  <td data-label="Latency (ms)" className="num px-6 py-4 whitespace-nowrap text-sm text-ink">
                    {event.latency_ms ?? '-'}
                  </td>
                  <td data-label="Payload" className="px-6 py-4 text-sm text-ink">
                    <Button
                      variant="ghost"
                      tone="brand"
                      size="sm"
                      onClick={() => toggleRowExpand(event.id)}
                    >
                      {expandedRows.has(event.id) ? 'Hide' : 'Show'}
                    </Button>
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
