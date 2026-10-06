import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi, type ApiError } from '../../lib/api'
import { errorText, formatWhen } from '../../lib/format'
import { TEXTAREA, TICKET_STATUS_LABELS, TICKET_STATUS_TONES, ticketsQuery } from '../../lib/engagement'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import TicketMessages from '../support/TicketMessages'
import { Row, Section } from './RequestDetailsDrawer'
import type { DeskTabProps } from './VerificationTab'
import type { Ticket, TicketThread } from '../../lib/types'

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'

/**
 * The desk's tickets: the queue (open first), a search, and a drawer with
 * the thread, Reply and Close. Loads its own queue; the page's summary
 * refresh follows every action through onDone. `initialTicket` (?ticket=
 * from a support notification) opens that thread at once, and again
 * whenever it changes while the tab is mounted; closing the drawer calls
 * onDrawerClosed so the page can drop ?ticket from the URL.
 */
export default function SupportTab({ orgId, control, show, onDone, initialTicket, onDrawerClosed }:
  DeskTabProps & { initialTicket: number | null; onDrawerClosed: () => void }) {
  const [rows, setRows] = useState<Ticket[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [openId, setOpenId] = useState<number | null>(initialTicket)
  const [thread, setThread] = useState<TicketThread | null>(null)
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  // Latest-applied wins, separately for the queue and the open thread.
  const listSeq = useRef(0)
  const threadSeq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++listSeq.current
    try {
      const r = await orgApi<Ticket[]>(orgId, ticketsQuery('tickets', 'all', q))
      if (mine !== listSeq.current) return
      setRows(r); setLoadError(null)
    } catch (err) {
      if (mine !== listSeq.current) return
      setLoadError(errorText(err, 'Could not load the tickets'))
      setRows((r) => r ?? [])
    }
  }, [orgId, q])

  useEffect(() => { void load() }, [load])

  // A notification followed while this tab is already mounted changes only
  // initialTicket: open that thread too.
  useEffect(() => { if (initialTicket != null) setOpenId(initialTicket) }, [initialTicket])

  const closeDrawer = () => { setOpenId(null); onDrawerClosed() }

  useEffect(() => {
    const mine = ++threadSeq.current
    setThread(null); setReply(''); setDrawerError(null)
    if (openId == null) return
    orgApi<TicketThread>(orgId, `tickets/${openId}`).then(
      (t) => { if (mine === threadSeq.current) setThread(t) },
      (err) => { if (mine === threadSeq.current) setDrawerError(errorText(err, 'Could not load this ticket')) },
    )
  }, [orgId, openId])

  const run = async (fn: () => Promise<TicketThread>, done: string) => {
    setBusy(true); setDrawerError(null)
    try {
      const t = await fn()
      threadSeq.current++
      setThread(t); setReply('')
      await load()
      onDone(done)
    } catch (err) {
      setDrawerError(errorText(err, 'The action failed'))
      // 409: closed by someone else meanwhile -- show the queue as it is now.
      if ((err as ApiError).response?.status === 409) void load()
    } finally {
      setBusy(false)
    }
  }

  const send = (e: FormEvent) => {
    e.preventDefault()
    if (openId == null || !reply.trim()) return
    void run(() => orgApi<TicketThread>(orgId, `tickets/${openId}/messages`, {
      method: 'POST', body: JSON.stringify({ body: reply.trim() }) }), 'Reply sent')
  }

  const close = () => {
    if (openId == null) return
    void run(() => orgApi<TicketThread>(orgId, `tickets/${openId}/close`, { method: 'POST' }), 'Ticket closed')
  }

  if (rows == null) return <Loading lines={4} label="Loading tickets" />
  const visible = show === 'open' ? rows.filter((t) => t.status !== 'closed') : rows

  return (
    <>
      <form role="search" onSubmit={(e) => { e.preventDefault(); setQ(draft) }}
            className="flex flex-wrap items-end gap-2 p-3">
        <Input aria-label="Search tickets" placeholder="Subject or message" value={draft}
               onChange={(e) => setDraft(e.target.value)} />
        <Button type="submit" variant="secondary" size="sm">Search</Button>
      </form>
      {loadError && (
        <div className="p-3 space-y-2">
          <Banner kind="error">{loadError}</Banner>
          <Button size="sm" variant="secondary" onClick={() => { void load() }}>Retry</Button>
        </div>
      )}
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className={TH}>Last message</th>
            <th className={TH}>Investor</th>
            <th className={TH}>Ticket</th>
            <th className={TH}>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && !loadError && (
            <tr><td colSpan={5} className="text-center py-8 text-ink-faint">
              {show === 'open' ? 'No open tickets' : 'No tickets yet'}
            </td></tr>
          )}
          {visible.map((t) => (
            <tr key={t.id} className="border-b border-line last:border-0 align-top">
              <td data-label="Last message" className={`num ${TD}`}>{formatWhen(t.last_message_at)}</td>
              <td data-label="Investor" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{t.display_name ?? '—'}</div>
                  <div className="text-xs text-ink-soft">{t.email ?? ''}</div>
                </div>
              </td>
              <td data-label="Ticket" className={TD}>{`#${t.id} ${t.subject_label}`}</td>
              <td data-label="Status" className={TD}>
                <div className="flex flex-wrap gap-1.5">
                  <Badge tone={TICKET_STATUS_TONES[t.status]}>{TICKET_STATUS_LABELS[t.status]}</Badge>
                  {/* The server's rule (portal_support.WAITING_ON_DESK), the one requests/summary counts. */}
                  {t.waiting_on_desk && <Badge tone="warn">Waiting on desk</Badge>}
                </div>
              </td>
              <td className={TD}>
                <div className="flex justify-end">
                  <Button variant="ghost" size="sm" aria-label={`Open ticket ${t.id}`}
                          onClick={() => setOpenId(t.id)}>Open</Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Drawer open={openId != null} busy={busy} onClose={closeDrawer}
              title={thread ? `Ticket #${thread.id}: ${thread.subject_label}` : `Ticket #${openId ?? ''}`}>
        <div className="space-y-4">
          {drawerError && <Banner kind="error" onDismiss={() => setDrawerError(null)}>{drawerError}</Banner>}
          {thread == null ? (
            !drawerError && <Loading lines={4} label="Loading ticket" />
          ) : (
            <>
              <Section title="Investor">
                <Row label="Name" value={thread.display_name ?? '—'} />
                <Row label="Email" value={thread.email ?? '—'} />
                <Row label="Status" value={TICKET_STATUS_LABELS[thread.status]} />
              </Section>
              <TicketMessages messages={thread.messages} viewer="desk"
                              fileUrl={(id) => `/api/orgs/${orgId}/files/${id}`} />
              {control && thread.status !== 'closed' && (
                <form onSubmit={send} className="space-y-3">
                  <label className="block">
                    <span className="desk-label block mb-1">Reply</span>
                    <textarea aria-label="Reply to the investor" rows={4} maxLength={4000} value={reply}
                              disabled={busy} onChange={(e) => setReply(e.target.value)} className={TEXTAREA} />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" disabled={busy || !reply.trim()}>Send reply</Button>
                    <Button variant="secondary" tone="loss" disabled={busy} onClick={close}>Close ticket</Button>
                  </div>
                </form>
              )}
              {thread.status === 'closed' && (
                <p className="text-sm text-ink-soft">Closed. The investor can open it again by replying.</p>
              )}
            </>
          )}
        </div>
      </Drawer>
    </>
  )
}
