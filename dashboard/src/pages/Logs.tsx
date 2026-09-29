import { useState, useEffect, useId, useRef } from 'react'
import { orgApi, eventsSocket } from '../lib/api'
import { useOrg } from '../lib/org'
import { formatWhen } from '../lib/format'
import type { Account, EventResponse } from '../lib/types'
import Button from '../components/Button'
import Card from '../components/Card'
import Input from '../components/Input'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import Select from '../components/Select'
import Badge, { type BadgeTone } from '../components/Badge'

/** The live list keeps the newest rows only: a busy desk streams events all
 *  day, and an unbounded list grows until the tab slows to a crawl. */
const LIVE_CAP = 500

const SEVERITIES = [
  { value: 'all', label: 'All' },
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'error', label: 'Error' },
]

export default function Logs() {
  const { orgId } = useOrg()
  const [events, setEvents] = useState<EventResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [isLive, setIsLive] = useState(true)
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())
  const [accounts, setAccounts] = useState<Account[]>([])
  const ids = {
    account: useId(), severity: useId(), category: useId(), since: useId(),
  }

  // Filter state
  const [filters, setFilters] = useState({
    account_id: '',
    severity: 'all',
    category: '',
    since: '',
  })

  // The account filter offers the org's own accounts by name. A failed
  // load leaves only "All accounts"; the log itself still works.
  useEffect(() => {
    let cancelled = false
    orgApi<Account[]>(orgId, 'accounts')
      .then((list) => { if (!cancelled && Array.isArray(list)) setAccounts(list) })
      .catch(() => { /* the filter degrades to All accounts */ })
    return () => { cancelled = true }
  }, [orgId])

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
            setEvents((prev) => [newEvent, ...prev].slice(0, LIVE_CAP))
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
      <PageHeader
        title="Logs"
        subtitle="The append-only audit trail: every master event, copy action, connection change, and control command."
      />

      <Card
        title="Filters"
        actions={
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={isLive}
              onChange={(e) => setIsLive(e.target.checked)}
              className="rounded accent-brand"
            />
            <span className="text-sm font-medium text-ink">Live</span>
          </label>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <label htmlFor={ids.account} className="block desk-label mb-1">Account</label>
            {/* The value stays the account id as a string: the events
                request and the live-row match both read it as-is. */}
            <Select
              id={ids.account}
              block
              value={filters.account_id}
              onChange={(e) => setFilters({ ...filters, account_id: e.target.value })}
            >
              <option value="">All accounts</option>
              {accounts.map((a) => (
                <option key={a.ctid_trader_account_id} value={String(a.ctid_trader_account_id)}>
                  {a.nickname ?? a.trader_login}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <label htmlFor={ids.severity} className="block desk-label mb-1">Severity</label>
            <Select
              id={ids.severity}
              block
              value={filters.severity}
              onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
            >
              {SEVERITIES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </Select>
          </div>

          <div>
            <label htmlFor={ids.category} className="block desk-label mb-1">Category</label>
            <Input
              id={ids.category}
              type="text"
              placeholder="e.g. control"
              value={filters.category}
              onChange={(e) => setFilters({ ...filters, category: e.target.value })}
            />
          </div>

          <div>
            <label htmlFor={ids.since} className="block desk-label mb-1">Since</label>
            <Input
              id={ids.since}
              type="datetime-local"
              value={filters.since}
              onChange={(e) => setFilters({ ...filters, since: e.target.value })}
            />
          </div>
        </div>
      </Card>

      {loading ? (
        <Loading lines={6} label="Loading events" />
      ) : (
      <Card inset>
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
              {events.length === 0 && (
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
      </Card>
      )}
    </div>
  )
}
