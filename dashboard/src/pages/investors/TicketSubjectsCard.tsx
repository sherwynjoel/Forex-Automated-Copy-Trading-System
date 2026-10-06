import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText } from '../../lib/format'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { TicketSubject } from '../../lib/types'

/**
 * What investors may raise a ticket about. Owns its own load, banners and
 * busy flag (like AccountsDrawer), so a refusal shows here, never on the
 * Investors page banner.
 */
export default function TicketSubjectsCard({ orgId, control }: { orgId: number; control: boolean }) {
  const [rows, setRows] = useState<TicketSubject[] | null>(null)
  const [label, setLabel] = useState('')
  const [renaming, setRenaming] = useState<{ id: number; label: string } | null>(null)
  const [deleting, setDeleting] = useState<TicketSubject | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++seq.current
    try {
      const r = await orgApi<TicketSubject[]>(orgId, 'ticket-subjects')
      if (mine === seq.current) setRows(Array.isArray(r) ? r : [])
    } catch (err) {
      if (mine !== seq.current) return
      setRows((x) => x ?? [])
      setError(errorText(err, 'Could not load the ticket subjects'))
    }
  }, [orgId])

  useEffect(() => { void load() }, [load])

  /** One mutation: busy, the card's banners, a reload. True on success. */
  const run = async (fn: () => Promise<unknown>, done: string): Promise<boolean> => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(done)
      await load()
      return true
    } catch (err) {
      setError(errorText(err, 'The action failed'))
      return false
    } finally {
      setBusy(false)
    }
  }

  const add = (e: FormEvent) => {
    e.preventDefault()
    if (!label.trim()) return
    void run(async () => {
      await orgApi(orgId, 'ticket-subjects', {
        method: 'POST', body: JSON.stringify({ label: label.trim(), sort: rows?.length ?? 0 }) })
      setLabel('')
    }, 'Subject added')
  }

  const patch = (s: TicketSubject, body: Partial<TicketSubject>, done: string) =>
    run(() => orgApi(orgId, `ticket-subjects/${s.id}`, { method: 'PATCH', body: JSON.stringify(body) }), done)

  const saveName = async (s: TicketSubject) => {
    if (renaming && await patch(s, { label: renaming.label.trim() }, 'Subject renamed')) setRenaming(null)
  }

  const remove = async () => {
    if (!deleting) return
    const s = deleting
    setDeleting(null)
    await run(() => orgApi(orgId, `ticket-subjects/${s.id}`, { method: 'DELETE' }), 'Subject deleted')
  }

  return (
    <Card title="Ticket subjects">
      <div className="space-y-3">
        <p className="text-sm text-ink-soft">
          What investors may raise a ticket about. A disabled subject is no longer offered; deleting
          one keeps the wording on tickets already raised.
        </p>
        {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
        {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
        {rows == null ? (
          <Loading lines={2} label="Loading ticket subjects" />
        ) : (
          <div className="inset">
            <ul className="divide-y divide-line">
              {rows.length === 0 && (
                <li className="px-4 py-6 text-center text-ink-faint">
                  No subjects yet — investors cannot raise a ticket until you add one.
                </li>
              )}
              {rows.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                  {renaming?.id === s.id ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <Input aria-label={`New name for ${s.label}`} maxLength={80} value={renaming.label}
                             onChange={(e) => setRenaming({ id: s.id, label: e.target.value })} />
                      <Button size="sm" disabled={busy || !renaming.label.trim()}
                              onClick={() => { void saveName(s) }}>Save name</Button>
                      <Button size="sm" variant="ghost" onClick={() => setRenaming(null)}>Cancel</Button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <span className="text-ink">{s.label}</span>
                      <Badge tone={s.enabled ? 'profit' : 'neutral'}>{s.enabled ? 'Enabled' : 'Disabled'}</Badge>
                    </span>
                  )}
                  {control && renaming?.id !== s.id && (
                    <span className="flex flex-wrap gap-2">
                      <Button variant="secondary" size="sm" disabled={busy} aria-label={`Rename ${s.label}`}
                              onClick={() => setRenaming({ id: s.id, label: s.label })}>Rename</Button>
                      <Button variant="secondary" size="sm" disabled={busy}
                              aria-label={`${s.enabled ? 'Disable' : 'Enable'} ${s.label}`}
                              onClick={() => { void patch(s, { enabled: !s.enabled },
                                                          s.enabled ? 'Subject disabled' : 'Subject enabled') }}>
                        {s.enabled ? 'Disable' : 'Enable'}
                      </Button>
                      <Button variant="ghost" tone="loss" size="sm" disabled={busy} aria-label={`Delete ${s.label}`}
                              onClick={() => setDeleting(s)}>Delete</Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {control && (
          <form onSubmit={add} className="flex flex-wrap items-end gap-2">
            <label className="block">
              <span className="desk-label block mb-1">New subject</span>
              <Input aria-label="New subject" maxLength={80} placeholder="Deposits" value={label}
                     onChange={(e) => setLabel(e.target.value)} />
            </label>
            <Button type="submit" size="sm" disabled={busy || !label.trim()}>Add subject</Button>
          </form>
        )}
      </div>
      <ConfirmDialog open={deleting != null} title={`Delete the subject ${deleting?.label ?? ''}?`}
                     confirmLabel="Delete subject" danger busy={busy}
                     onConfirm={() => { void remove() }} onCancel={() => setDeleting(null)}>
        <p>Investors can no longer pick it. Tickets already raised keep their subject.</p>
      </ConfirmDialog>
    </Card>
  )
}
