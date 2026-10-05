import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import { orgApi } from '../../lib/api'
import { formatWhen } from '../../lib/format'
import { safeLink } from '../../lib/engagement'
import { markAllRead, markRead, withAllRead } from '../../lib/notificationActions'
import type { NotificationsPage, PortalNotification } from '../../lib/types'
import Button from '../Button'
import Loading from '../Loading'

const LATEST = 8
const PANEL_WIDTH = 320

/**
 * The shell's bell: the unread count in the button's name and a small
 * badge, and a glass popover with the latest eight (on an opaque inset),
 * "Mark all read" and a link to the full page. A row click marks it read
 * and follows its in-app link. The popover is portalled and fixed to the
 * trigger, as Menu's is, so no clipping ancestor cuts it off; Escape and an
 * outside click close it. The trigger is 44 px square at every width (a
 * touch target in the tablet top bar as much as on the phone).
 */
export default function NotificationBell({ orgId, pageHref, count, onChange }: {
  orgId: number
  /** The Notifications page of this layout (desk or portal). */
  pageHref: string
  count: number | undefined
  /** After a mark-read: the layout asks the unread count again. */
  onChange: () => void
}) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<PortalNotification[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [pos, setPos] = useState({ top: 0, right: 8 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  // Bumped per open: a slow list for an earlier opening (or org) never lands.
  const seq = useRef(0)

  useEffect(() => { setOpen(false); setItems(null) }, [orgId])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!panelRef.current?.contains(e.target as Node) && !triggerRef.current?.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const toggle = () => {
    if (open) { setOpen(false); return }
    const rect = triggerRef.current?.getBoundingClientRect()
    if (rect) setPos({ top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right) })
    setItems(null)
    setFailed(false)
    setOpen(true)
    const mine = ++seq.current
    orgApi<NotificationsPage>(orgId, `notifications?limit=${LATEST}`).then(
      (p) => { if (mine === seq.current) setItems(Array.isArray(p?.notifications) ? p.notifications : []) },
      () => { if (mine === seq.current) { setItems([]); setFailed(true) } },
    )
  }

  const follow = async (n: PortalNotification) => {
    setOpen(false)
    if (n.read_at == null) {
      try {
        await markRead(orgId, n)
      } catch {
        // The link still works; the badge catches up on the next poll.
      }
      onChange()
    }
    const to = safeLink(n.link)
    if (to) navigate(to)
  }

  const readAll = async () => {
    try {
      await markAllRead(orgId)
      setItems((list) => (list ? withAllRead(list) : null))
    } catch {
      setFailed(true)
    }
    onChange()
  }

  const label = count ? `Notifications, ${count} unread` : 'Notifications'
  return (
    <div className="relative inline-block">
      <Button ref={triggerRef} variant="ghost" tone="neutral" size="sm" aria-label={label}
              aria-haspopup="dialog" aria-expanded={open} onClick={toggle}
              className="relative h-11 w-11 justify-center">
        <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5">
          <path d="M10 3a5 5 0 0 0-5 5v3l-1.5 2.5h13L15 11V8a5 5 0 0 0-5-5zM8 16a2 2 0 0 0 4 0"
                fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {count ? (
          <span aria-hidden="true" style={{ position: 'absolute', top: 0, right: 0 }}
                className="num rounded-full bg-brand px-1 text-xs text-on-accent">
            {count > 99 ? '99+' : count}
          </span>
        ) : null}
      </Button>
      {open && createPortal(
        <div ref={panelRef} role="dialog" aria-label="Latest notifications"
             style={{ position: 'fixed', top: pos.top, right: pos.right, width: PANEL_WIDTH }}
             className="glass z-40 rounded-inset border p-2 shadow-float space-y-2">
          <div className="flex items-center justify-between gap-2 px-1">
            <span className="desk-label">Notifications</span>
            <Button variant="ghost" size="sm" onClick={() => { void readAll() }}
                    disabled={!items?.some((n) => n.read_at == null)}>
              Mark all read
            </Button>
          </div>
          <div className="inset">
            {items == null ? (
              <Loading lines={2} label="Loading notifications" className="p-3" />
            ) : items.length === 0 ? (
              <p className="px-3 py-4 text-sm text-ink-faint">
                {failed ? 'Could not load your notifications' : 'Nothing here yet'}
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {items.map((n) => (
                  <li key={n.id}>
                    <button type="button" onClick={() => { void follow(n) }}
                            className="block w-full px-3 py-2 text-left text-sm hover:bg-brand-wash focus:bg-brand-wash">
                      <span className={`block ${n.read_at == null ? 'font-semibold text-ink' : 'text-ink-soft'}`}>
                        {n.title}
                        {n.read_at == null && <span className="sr-only"> (unread)</span>}
                      </span>
                      <span className="block text-xs text-ink-faint num">{formatWhen(n.created_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <Link to={pageHref} onClick={() => setOpen(false)}
                className="block px-1 text-sm text-brand underline underline-offset-2 hover:text-brand-deep">
            See all notifications
          </Link>
        </div>,
        document.body,
      )}
    </div>
  )
}
