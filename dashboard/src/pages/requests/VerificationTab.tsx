import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi, type ApiError } from '../../lib/api'
import { errorText, formatWhen } from '../../lib/format'
import { FIELD_LABELS, fieldValue, kycBadge, kycLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Loading from '../../components/Loading'
import { FilePreview, Row, Section } from './RequestDetailsDrawer'
import type { KycFileField, KycProfile, KycTextField } from '../../lib/types'

export interface DeskTabProps {
  orgId: number
  control: boolean
  show: 'open' | 'all'
  onDone: (message: string) => void
}

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'
const SECTIONS: [string, KycTextField[]][] = [
  ['Profile', ['full_name', 'gender', 'date_of_birth', 'phone']],
  ['Identity', ['country_citizenship', 'id_type', 'id_number']],
  ['Address', ['address_line', 'area', 'landmark', 'city', 'state', 'postal_code', 'country_residence']],
]
const DOCUMENTS: KycFileField[] = ['id_front_file_id', 'id_back_file_id', 'address_proof_file_id', 'photo_file_id']

/** Identity verifications waiting on an admin: the profile, the four
 *  documents, Approve, or Reject with a note. Loads its own queue; the
 *  page's summary refresh follows every decision through onDone. */
export default function VerificationTab({ orgId, control, show, onDone }: DeskTabProps) {
  const [rows, setRows] = useState<KycProfile[] | null>(null)
  // A failed load stays in the tab (with Retry): the page's poll clears its
  // own banner, which would leave a misleading empty queue behind.
  const [loadError, setLoadError] = useState<string | null>(null)
  const [details, setDetails] = useState<KycProfile | null>(null)
  const [pending, setPending] = useState<{ row: KycProfile; status: 'approved' | 'rejected' } | null>(null)
  const [note, setNote] = useState('')
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Latest-applied wins, as on the page: a load started before a decision
  // must not land over the queue fetched after it.
  const requestSeq = useRef(0)
  const lastApplied = useRef(0)

  const load = useCallback(async () => {
    const seq = ++requestSeq.current
    try {
      const r = await orgApi<KycProfile[]>(orgId, 'kyc')
      if (seq <= lastApplied.current) return
      lastApplied.current = seq
      setRows(r); setLoadError(null)
    } catch (err) {
      if (seq <= lastApplied.current) return
      lastApplied.current = seq
      setLoadError(errorText(err, 'Could not load the verifications'))
      setRows((r) => r ?? [])
    }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const open = (row: KycProfile, status: 'approved' | 'rejected') => {
    setPending({ row, status }); setNote(''); setDialogError(null)
  }

  const decide = async () => {
    if (!pending) return
    setBusy(true); setDialogError(null)
    try {
      await orgApi(orgId, `kyc/${pending.row.user_id}/decision`, {
        method: 'POST', body: JSON.stringify({ status: pending.status, note: note.trim() }),
      })
      const word = pending.status
      setPending(null)
      setDetails(null)
      await load()
      onDone(`Verification ${word}`)
    } catch (err) {
      setDialogError(errorText(err, 'The action failed'))
      // 409: someone else decided it -- show the row as it is now.
      if ((err as ApiError).response?.status === 409) void load()
    } finally {
      setBusy(false)
    }
  }

  if (rows == null) return <Loading lines={4} label="Loading verifications" />
  const visible = show === 'open' ? rows.filter((r) => r.status === 'submitted') : rows
  const who = (r: KycProfile) => r.display_name ?? r.email ?? `investor ${r.user_id}`

  return (
    <>
      {loadError && (
        <div className="p-3 space-y-2">
          <Banner kind="error">{loadError}</Banner>
          <Button size="sm" variant="secondary" onClick={() => { void load() }}>Retry</Button>
        </div>
      )}
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className={TH}>Submitted</th>
            <th className={TH}>Investor</th>
            <th className={TH}>Name on ID</th>
            <th className={TH}>ID</th>
            <th className={TH}>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && !loadError && (
            <tr><td colSpan={6} className="text-center py-8 text-ink-faint">
              {show === 'open' ? 'No open verifications' : 'No verifications yet'}
            </td></tr>
          )}
          {visible.map((r) => (
            <tr key={r.user_id} className="border-b border-line last:border-0 align-top">
              <td data-label="Submitted" className={`num ${TD}`}>{formatWhen(r.submitted_at)}</td>
              <td data-label="Investor" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{r.display_name ?? '—'}</div>
                  <div className="text-xs text-ink-soft">{r.email ?? ''}</div>
                </div>
              </td>
              <td data-label="Name on ID" className={TD}>{r.full_name ?? '—'}</td>
              <td data-label="ID" className={TD}>{`${fieldValue('id_type', r.id_type)} · ${r.country_citizenship ?? '—'}`}</td>
              <td data-label="Status" className={TD}>
                <div className="min-w-0">
                  <Badge tone={kycBadge(r.status)}>{kycLabel(r.status)}</Badge>
                  {r.decision_note && <div className="text-xs text-ink-soft mt-1">{r.decision_note}</div>}
                </div>
              </td>
              <td className={TD}>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <Button variant="ghost" size="sm" aria-label={`Details of verification ${r.user_id}`}
                          onClick={() => setDetails(r)}>Details</Button>
                  {control && r.status === 'submitted' && (
                    <>
                      <Button size="sm" aria-label={`Approve verification ${r.user_id}`} disabled={busy}
                              onClick={() => open(r, 'approved')}>Approve</Button>
                      <Button size="sm" variant="secondary" tone="loss" aria-label={`Reject verification ${r.user_id}`}
                              disabled={busy} onClick={() => open(r, 'rejected')}>Reject</Button>
                    </>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Drawer open={details != null} title={details ? `Verification of ${who(details)}` : ''}
              onClose={() => setDetails(null)}>
        {details && (
          <div className="space-y-4">
            <Section title="Investor">
              <Row label="Name" value={details.display_name ?? '—'} />
              <Row label="Email" value={details.email ?? '—'} />
              <Row label="Status" value={kycLabel(details.status)} />
              {details.decision_note && <Row label="Note" value={details.decision_note} />}
            </Section>
            {SECTIONS.map(([title, fields]) => (
              <Section key={title} title={title}>
                {fields.map((f) => <Row key={f} label={FIELD_LABELS[f]} value={fieldValue(f, details[f])} />)}
              </Section>
            ))}
            {DOCUMENTS.map((f) => {
              const fileId = details[f]
              return fileId != null
                ? <FilePreview key={f} orgId={orgId} fileId={fileId} label={FIELD_LABELS[f]} />
                : <Section key={f} title={FIELD_LABELS[f]}><Row label="File" value="Not uploaded" /></Section>
            })}
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={pending != null}
        title={pending ? `${pending.status === 'approved' ? 'Approve' : 'Reject'} the verification of ${who(pending.row)}` : ''}
        confirmLabel={pending?.status === 'approved' ? 'Approve' : 'Reject'}
        danger={pending?.status === 'rejected'}
        busy={busy}
        disabled={pending?.status === 'rejected' && note.trim() === ''}
        onConfirm={() => { void decide() }}
        onCancel={() => setPending(null)}
      >
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        <label className="block">
          <span className="desk-label block mb-1">{pending?.status === 'rejected' ? 'Note' : 'Note (optional)'}</span>
          <textarea aria-label="Note" value={note} rows={2} onChange={(e) => setNote(e.target.value)}
                    className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
        </label>
      </ConfirmDialog>
    </>
  )
}
