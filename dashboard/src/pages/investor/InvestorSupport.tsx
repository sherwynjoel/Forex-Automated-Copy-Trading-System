import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import {
  IMAGE_ACCEPT, MAX_IMAGES, TEXTAREA, TICKET_STATUS_LABELS, TICKET_STATUS_TONES, ticketsQuery,
} from '../../lib/engagement'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import FileInput, { MAX_UPLOAD_BYTES } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import Tabs from '../../components/Tabs'
import TicketMessages from '../support/TicketMessages'
import type { Ticket, TicketStatus, TicketSubject, TicketThread, UploadedFile } from '../../lib/types'

type StatusTab = TicketStatus | 'all'
const STATUS_TABS: { key: StatusTab; label: string }[] = [
  { key: 'all', label: 'All' }, { key: 'new', label: 'New' },
  { key: 'open', label: 'Open' }, { key: 'closed', label: 'Closed' },
]
const NO_FILES: (File | null)[] = Array(MAX_IMAGES).fill(null)
const NO_IDS: (number | null)[] = Array(MAX_IMAGES).fill(null)

/**
 * The three optional image slots of one message. A picked file uploads on
 * send; as soon as an upload lands its id is kept and the slot cleared, so
 * a send that fails afterwards neither uploads it again nor drops it on the
 * retry (InvestorProfile's pending-ids pattern). Picking a new file for a
 * slot replaces whatever it held.
 */
function useImageSlots(orgId: number) {
  const [files, setFiles] = useState<(File | null)[]>(NO_FILES)
  const [pending, setPending] = useState<(number | null)[]>(NO_IDS)
  const at = <T,>(list: T[], i: number, value: T) => list.map((x, j) => (j === i ? value : x))

  const pick = (i: number, next: File | null) => {
    setFiles((f) => at(f, i, next))
    setPending((p) => at(p, i, null))
  }

  /** Uploads what is still a File; answers every id, in slot order. */
  const upload = async (): Promise<number[]> => {
    const ids = [...pending]
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      if (!f) continue
      const fd = new FormData()
      fd.append('purpose', 'ticket_attachment')
      fd.append('file', f)
      const id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      ids[i] = id
      setFiles((x) => at(x, i, null))
      setPending((x) => at(x, i, id))
    }
    return ids.filter((id): id is number => id != null)
  }

  const reset = () => { setFiles(NO_FILES); setPending(NO_IDS) }
  return { files, pending, pick, upload, reset }
}

type ImageSlotsState = ReturnType<typeof useImageSlots>

/** One FileInput per slot; a slot whose image is already uploaded says so. */
function ImageSlots({ idBase, slots, disabled }: {
  idBase: string
  slots: ImageSlotsState
  disabled?: boolean
}) {
  return (
    <div className="space-y-3">
      {slots.files.map((f, i) => (
        <FileInput key={i} id={`${idBase}-${i + 1}`} label={`Image ${i + 1} (optional)`}
                   accept={IMAGE_ACCEPT} maxBytes={MAX_UPLOAD_BYTES} value={f} disabled={disabled}
                   hint={slots.pending[i] != null ? 'Uploaded; it goes with your message'
                     : i === 0 ? `JPEG, PNG or WebP, up to 5 MB; at most ${MAX_IMAGES} per message` : undefined}
                   onChange={(next) => slots.pick(i, next)} />
      ))}
    </div>
  )
}

function TicketList({ orgId, reloadKey, onOpen }: {
  orgId: number
  reloadKey: number
  onOpen: (id: number) => void
}) {
  const [status, setStatus] = useState<StatusTab>('all')
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<Ticket[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Bumped per load: a tab click or a search while an older load is in
  // flight drops the older answer.
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    orgApi<Ticket[]>(orgId, ticketsQuery('investor/tickets', status, q)).then(
      (r) => { if (mine === seq.current) { setRows(r); setError(null) } },
      (err) => {
        if (mine !== seq.current) return
        setRows((x) => x ?? [])
        setError(errorText(err, 'Could not load your tickets'))
      },
    )
  }, [orgId, status, q, reloadKey])

  return (
    <div className="space-y-4">
      <Tabs idBase="support" label="Ticket status" value={status}
            onChange={(k) => setStatus(k as StatusTab)} items={STATUS_TABS} />
      <form role="search" onSubmit={(e: FormEvent) => { e.preventDefault(); setQ(draft) }}
            className="flex flex-wrap items-end gap-2">
        <Input aria-label="Search tickets" placeholder="Subject or message" value={draft}
               onChange={(e) => setDraft(e.target.value)} />
        <Button type="submit" variant="secondary" size="sm">Search</Button>
      </form>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      <div id="support-panel" role="tabpanel" aria-labelledby={`support-tab-${status}`}>
        <Card title="Your tickets" inset>
          {rows == null ? (
            <Loading lines={3} label="Loading tickets" className="p-4" />
          ) : (
            <ul className="divide-y divide-line">
              {rows.length === 0 && (
                <li className="text-center py-8 text-ink-faint">
                  {q || status !== 'all' ? 'No tickets match' : 'No tickets yet'}
                </li>
              )}
              {rows.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => onOpen(t.id)}
                          className="block w-full px-4 py-3 text-left text-sm space-y-1 hover:bg-brand-wash focus:bg-brand-wash">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="num text-ink-soft">#{t.id}</span>
                      <span className="text-ink">{t.subject_label}</span>
                      <Badge tone={TICKET_STATUS_TONES[t.status]}>{TICKET_STATUS_LABELS[t.status]}</Badge>
                      {t.last_from_desk && t.status !== 'closed' && <Badge tone="profit">Desk replied</Badge>}
                    </span>
                    <span className="block text-xs text-ink-faint num">Last message {formatWhen(t.last_message_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

function TicketView({ orgId, ticketId, onBack, onNotice }: {
  orgId: number
  ticketId: number
  onBack: () => void
  onNotice: (message: string) => void
}) {
  const [thread, setThread] = useState<TicketThread | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reply, setReply] = useState('')
  const slots = useImageSlots(orgId)
  const [busy, setBusy] = useState(false)
  const [closing, setClosing] = useState(false)
  const seq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++seq.current
    try {
      const t = await orgApi<TicketThread>(orgId, `investor/tickets/${ticketId}`)
      if (mine === seq.current) { setThread(t); setError(null) }
    } catch (err) {
      if (mine === seq.current) setError(errorText(err, 'Could not load this ticket'))
    }
  }, [orgId, ticketId])

  useEffect(() => { void load() }, [load])

  const send = async (e: FormEvent) => {
    e.preventDefault()
    if (!reply.trim()) { setError('Write a message first'); return }
    setBusy(true); setError(null)
    try {
      const file_ids = await slots.upload()
      const t = await orgApi<TicketThread>(orgId, `investor/tickets/${ticketId}/messages`, {
        method: 'POST', body: JSON.stringify({ body: reply.trim(), file_ids }) })
      const reopened = thread?.status === 'closed'
      seq.current++   // a load still in flight must not land over this answer
      setThread(t); setReply(''); slots.reset()
      onNotice(reopened ? 'Reply sent; the ticket is open again.' : 'Reply sent.')
    } catch (err) {
      setError(errorText(err, 'Could not send your reply'))
    } finally {
      setBusy(false)
    }
  }

  const close = async () => {
    setBusy(true)
    try {
      const t = await orgApi<TicketThread>(orgId, `investor/tickets/${ticketId}/close`, { method: 'POST' })
      seq.current++
      setThread(t)
      onNotice('Ticket closed.')
    } catch (err) {
      setError(errorText(err, 'Could not close the ticket'))
    } finally {
      setBusy(false)
      setClosing(false)
    }
  }

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>Back to all tickets</Button>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {thread == null ? (
        !error && <Loading lines={4} label="Loading ticket" />
      ) : (
        <>
          <Card title={`#${thread.id} ${thread.subject_label}`} actions={
            <>
              <Badge tone={TICKET_STATUS_TONES[thread.status]}>{TICKET_STATUS_LABELS[thread.status]}</Badge>
              {thread.status !== 'closed' && (
                <Button variant="secondary" tone="loss" size="sm" disabled={busy} onClick={() => setClosing(true)}>
                  Close ticket
                </Button>
              )}
            </>
          }>
            <TicketMessages messages={thread.messages} viewer="investor"
                            fileUrl={(id) => `/api/orgs/${orgId}/investor/files/${id}`} />
          </Card>
          <Card title="Reply">
            <form onSubmit={send} className="space-y-4">
              {thread.status === 'closed' && (
                <p className="text-sm text-ink-soft">This ticket is closed. A reply opens it again.</p>
              )}
              <label className="block">
                <span className="desk-label block mb-1">Message</span>
                <textarea aria-label="Your reply" rows={4} maxLength={4000} value={reply} disabled={busy}
                          onChange={(e) => setReply(e.target.value)} className={TEXTAREA} />
              </label>
              <ImageSlots idBase="reply-image" slots={slots} disabled={busy} />
              <Button type="submit" disabled={busy}>Send reply</Button>
            </form>
          </Card>
        </>
      )}
      <ConfirmDialog open={closing} title={`Close ticket #${ticketId}?`} confirmLabel="Yes, close it" danger
                     busy={busy} onConfirm={() => { void close() }} onCancel={() => setClosing(false)}>
        <p>You can still reply later; a reply opens it again.</p>
      </ConfirmDialog>
    </div>
  )
}

/**
 * The investor's support: tickets with status tabs and a search, "Raise
 * ticket" (subject, message, up to three images) and a thread view with
 * replies and Close. `?ticket=<id>` -- the link in a support notification --
 * opens a thread.
 */
export default function InvestorSupport() {
  const { orgId } = useOrg()
  const [params, setParams] = useSearchParams()
  const ticketId = Number(params.get('ticket')) || null
  const [subjects, setSubjects] = useState<TicketSubject[] | null>(null)
  const [raising, setRaising] = useState(false)
  const [form, setForm] = useState({ subject: '', body: '' })
  const slots = useImageSlots(orgId)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const subjectsSeq = useRef(0)

  useEffect(() => {
    const mine = ++subjectsSeq.current
    setSubjects(null)
    orgApi<TicketSubject[]>(orgId, 'investor/ticket-subjects').then(
      (s) => { if (mine === subjectsSeq.current) setSubjects(Array.isArray(s) ? s : []) },
      () => { if (mine === subjectsSeq.current) setSubjects([]) },
    )
  }, [orgId])

  const openTicket = (id: number) => setParams({ ticket: String(id) })
  const back = () => { setParams({}); setReloadKey((k) => k + 1) }

  const startRaise = () => {
    setForm({ subject: subjects?.[0] ? String(subjects[0].id) : '', body: '' })
    slots.reset(); setDialogError(null); setRaising(true)
  }

  const raise = async () => {
    if (!form.subject) { setDialogError('Pick a subject'); return }
    if (!form.body.trim()) { setDialogError('Write a message'); return }
    setBusy(true); setDialogError(null)
    try {
      const file_ids = await slots.upload()
      const t = await orgApi<TicketThread>(orgId, 'investor/tickets', {
        method: 'POST',
        body: JSON.stringify({ subject_id: Number(form.subject), body: form.body.trim(), file_ids }),
      })
      setRaising(false)
      slots.reset()
      setNotice('Ticket sent. The desk replies here and in your notifications.')
      openTicket(t.id)
    } catch (err) {
      setDialogError(errorText(err, 'Could not send the ticket'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        title="Support"
        subtitle="Ask the desk about anything in your account. Replies arrive here and in your notifications."
        actions={<Button onClick={startRaise} disabled={!subjects || subjects.length === 0}>Raise ticket</Button>}
      />
      {subjects?.length === 0 && (
        <p className="text-sm text-ink-soft">The desk has not set up support subjects yet.</p>
      )}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {ticketId ? (
        <TicketView key={ticketId} orgId={orgId} ticketId={ticketId} onBack={back} onNotice={setNotice} />
      ) : (
        <TicketList orgId={orgId} reloadKey={reloadKey} onOpen={openTicket} />
      )}
      <ConfirmDialog open={raising} title="Raise a ticket" confirmLabel="Send ticket" busy={busy}
                     onConfirm={() => { void raise() }} onCancel={() => setRaising(false)}>
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        <label className="block">
          <span className="desk-label block mb-1">Subject</span>
          <Select aria-label="Subject" block value={form.subject}
                  onChange={(e) => setForm({ ...form, subject: e.target.value })}>
            {(subjects ?? []).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </Select>
        </label>
        <label className="block">
          <span className="desk-label block mb-1">Message</span>
          <textarea aria-label="Message" rows={5} maxLength={4000} value={form.body}
                    onChange={(e) => setForm({ ...form, body: e.target.value })} className={TEXTAREA} />
        </label>
        <ImageSlots idBase="ticket-image" slots={slots} disabled={busy} />
      </ConfirmDialog>
    </div>
  )
}
