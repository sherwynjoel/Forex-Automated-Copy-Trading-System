import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { errorText, formatWhen } from '../lib/format'
import { TOPIC_LABELS, safeLink } from '../lib/engagement'
import { markAllRead, markRead, withAllRead } from '../lib/notificationActions'
import Badge from '../components/Badge'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import type { NotificationsPage, PortalNotification } from '../lib/types'

const PAGE = 50

/**
 * Every notification of the signed-in user in this org, newest first, for
 * the desk (/org/:id/notifications) and the portal (invest/notifications).
 * A row click marks it read and follows its link. The bell's badge catches
 * up on its next poll (ponytail: no shared store between the two).
 */
export default function Notifications() {
  const { orgId } = useOrg()
  const navigate = useNavigate()
  const [rows, setRows] = useState<PortalNotification[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Bumped per load: an org switch or a second Load more drops older answers.
  const seq = useRef(0)

  const load = useCallback(async (before: number | null) => {
    const mine = ++seq.current
    setBusy(true)
    try {
      const page = await orgApi<NotificationsPage>(
        orgId, `notifications?limit=${PAGE}${before != null ? `&before=${before}` : ''}`)
      if (mine !== seq.current) return
      setRows((r) => (before == null ? page.notifications : [...r, ...page.notifications]))
      setHasMore(page.has_more)
      setNextBefore(page.next_before)
      setError(null)
    } catch (err) {
      if (mine !== seq.current) return
      setError(errorText(err, 'Could not load your notifications'))
    } finally {
      if (mine === seq.current) { setBusy(false); setLoaded(true) }
    }
  }, [orgId])

  useEffect(() => {
    setRows([]); setLoaded(false)
    void load(null)
  }, [load])

  const open = async (n: PortalNotification) => {
    if (n.read_at == null) {
      try {
        const updated = await markRead(orgId, n)
        setRows((r) => r.map((x) => (x.id === n.id ? updated : x)))
      } catch (err) {
        setError(errorText(err, 'Could not mark it read'))
        return
      }
    }
    const to = safeLink(n.link)
    if (to) navigate(to)
  }

  const readAll = async () => {
    try {
      await markAllRead(orgId)
      setRows((r) => withAllRead(r))
    } catch (err) {
      setError(errorText(err, 'Could not mark them read'))
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        title="Notifications"
        subtitle="Every decision on your requests, every ticket reply and every bonus, newest first. Your email switches live under Settings."
        actions={
          <Button variant="secondary" size="sm" disabled={!rows.some((n) => n.read_at == null)}
                  onClick={() => { void readAll() }}>
            Mark all read
          </Button>
        }
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!loaded ? (
        !error && <Loading lines={4} label="Loading notifications" />
      ) : (
        <Card title="Your notifications" inset>
          <ul className="divide-y divide-line">
            {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No notifications yet</li>}
            {rows.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => { void open(n) }}
                        className="block w-full px-4 py-3 text-left text-sm space-y-1 hover:bg-brand-wash focus:bg-brand-wash">
                  <span className="flex flex-wrap items-center gap-2">
                    {n.read_at == null && <Badge tone="brand">Unread</Badge>}
                    <Badge tone="neutral">{TOPIC_LABELS[n.topic]}</Badge>
                    <span className={n.read_at == null ? 'font-semibold text-ink' : 'text-ink'}>{n.title}</span>
                  </span>
                  <span className="block text-ink-soft whitespace-pre-wrap">{n.body}</span>
                  <span className="block text-xs text-ink-faint num">{formatWhen(n.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
          {hasMore && (
            <div className="p-3">
              <Button variant="secondary" size="sm" disabled={busy} onClick={() => { void load(nextBefore) }}>
                Load more
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
