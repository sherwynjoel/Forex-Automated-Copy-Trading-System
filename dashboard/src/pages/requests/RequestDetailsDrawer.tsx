import { useState, type ReactNode } from 'react'
import Badge from '../../components/Badge'
import Drawer from '../../components/Drawer'
import { formatWhen, money } from '../../lib/format'
import { BADGE_TONE, moneyOrDash, statusLabel, statusTone } from '../../lib/investor'
import type { PayoutDestination, PortalDeposit, PortalTransfer, PortalWithdrawal } from '../../lib/types'
import { KIND_WORD, moneyRefLabel } from './RequestTabs'

export type Details =
  | { kind: 'deposits'; row: PortalDeposit }
  | { kind: 'withdrawals'; row: PortalWithdrawal }
  | { kind: 'transfers'; row: PortalTransfer }
  | { kind: 'payout_destinations'; row: PayoutDestination }

/** Labels for the JSON detail keys a payout destination carries. */
const DETAIL_LABELS: Record<string, string> = {
  bank_name: 'Bank name',
  holder: 'Account holder',
  account_number: 'Account number',
  code: 'SWIFT / IFSC code',
  bank_address: 'Bank address',
  country: 'Country',
  coin: 'Coin',
  network: 'Network',
  address: 'Address',
}

function Row({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-soft shrink-0">{label}</dt>
      <dd className={`text-ink text-right break-all ${mono ? 'num' : ''}`}>{value}</dd>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="desk-label mb-2">{title}</h3>
      <dl className="inset p-3 space-y-1.5 text-sm">{children}</dl>
    </section>
  )
}

/**
 * The receipt or proof. The browser fetches it like any same-origin image,
 * so the session cookie travels with the request and the admin route
 * (`GET files/{id}`) answers. A PDF is served as a download, never inline,
 * so the <img> fails and the link underneath is the way in.
 */
function FilePreview({ orgId, fileId, label }: { orgId: number; fileId: number; label: string }) {
  const [failed, setFailed] = useState(false)
  const src = `/api/orgs/${orgId}/files/${fileId}`
  return (
    <section>
      <h3 className="desk-label mb-2">{label}</h3>
      <div className="inset p-3 space-y-2">
        {!failed && (
          <img src={src} alt={label} onError={() => setFailed(true)}
               className="max-h-96 w-auto max-w-full rounded-control border border-line" />
        )}
        {failed && (
          <p className="text-sm text-ink-soft">No inline preview for this file (a PDF downloads instead).</p>
        )}
        <a href={src} target="_blank" rel="noreferrer"
           className="block text-sm text-brand underline underline-offset-2 hover:text-brand-deep">
          Open file #{fileId} in a new tab
        </a>
      </div>
    </section>
  )
}

function InvestorSection({ name, email, userId }: { name?: string; email?: string; userId: number }) {
  return (
    <Section title="Investor">
      <Row label="Name" value={name ?? '—'} />
      <Row label="Email" value={email ?? '—'} />
      <Row label="User id" value={String(userId)} mono />
    </Section>
  )
}

function Audit({ row, extra }: {
  row: { created_at: string; decided_at: string | null; decided_by: number | null; decision_note: string | null }
  extra?: ReactNode
}) {
  return (
    <Section title="Audit trail">
      <Row label="Filed" value={formatWhen(row.created_at)} mono />
      {row.decided_at && (
        <Row label="Decided" mono
             value={`${formatWhen(row.decided_at)}${row.decided_by != null ? ` by user #${row.decided_by}` : ''}`} />
      )}
      {row.decision_note && <Row label="Decision note" value={row.decision_note} />}
      {extra}
    </Section>
  )
}

function DepositBody({ orgId, d }: { orgId: number; d: PortalDeposit }) {
  return (
    <div className="space-y-5">
      <Section title="Request">
        <Row label="Amount" value={money(d.amount, d.currency)} mono />
        <Row label="Fee" value={money(d.fee, d.currency)} mono />
        <Row label="Credited" value={d.credited_amount != null ? money(d.credited_amount, d.currency) : '—'} mono />
        <Row label="Method" value={`${d.method_label} (${d.method_kind})`} />
        <Row label="Reference" value={d.reference} mono />
        <Row label="Deposit to" value={d.target === 'account' ? `Trading account ${d.target_account_id ?? ''}` : 'My wallet'} />
        {d.note && <Row label="Investor note" value={d.note} />}
      </Section>
      <InvestorSection name={d.display_name} email={d.email} userId={d.user_id} />
      {d.receipt_file_id != null && <FilePreview orgId={orgId} fileId={d.receipt_file_id} label="Receipt" />}
      <Audit row={d} />
    </div>
  )
}

/** The JSON detail keys of a payout destination, one Row each, labelled
 *  through DETAIL_LABELS; account numbers, codes and addresses in the
 *  numeric face so they can be read digit by digit. */
function DetailRows({ details }: { details: Record<string, string> }) {
  return (
    <>
      {Object.entries(details).map(([key, value]) => (
        <Row key={key} label={DETAIL_LABELS[key] ?? key} value={value}
             mono={key === 'account_number' || key === 'code' || key === 'address'} />
      ))}
    </>
  )
}

/**
 * `destination` is the withdrawal's payout account, looked up by
 * `destination_id` in the page's `GET payout-destinations` list (admins get
 * `full=True`, so the bank account number is unmasked). It is what the admin
 * must pay to before clicking "Mark paid"; the summary alone ("ICICI ••4543")
 * is not enough to make a payment. Undefined when the list has no such row
 * (the destination was removed after the withdrawal settled), in which case
 * the summary is all that is left to show.
 */
function WithdrawalBody({ w, destination }: { w: PortalWithdrawal; destination: PayoutDestination | undefined }) {
  return (
    <div className="space-y-5">
      <Section title="Request">
        <Row label="Amount" value={money(w.amount, w.currency)} mono />
        <Row label="Fee" value={money(w.fee, w.currency)} mono />
        <Row label="Net to pay" value={money(w.net_amount, w.currency)} mono />
        <Row label="Destination" value={`${w.destination_summary} (${w.destination_kind})`} />
        {w.txid && <Row label="Transaction id" value={w.txid} mono />}
      </Section>
      <Section title="Destination details">
        {destination ? (
          <>
            <Row label="Nickname" value={destination.nickname} />
            <Row label="Kind" value={destination.kind === 'bank' ? 'Bank account' : 'Crypto address'} />
            <DetailRows details={destination.details} />
          </>
        ) : (
          <Row label="Payout account" value={w.destination_summary} mono />
        )}
      </Section>
      <InvestorSection name={w.display_name} email={w.email} userId={w.user_id} />
      <Audit row={w} extra={w.paid_at && (
        <Row label="Paid" mono
             value={`${formatWhen(w.paid_at)}${w.paid_by != null ? ` by user #${w.paid_by}` : ''}`} />
      )} />
    </div>
  )
}

function TransferBody({ t }: { t: PortalTransfer }) {
  return (
    <div className="space-y-5">
      <Section title="Request">
        <Row label="Amount" value={money(t.amount, t.currency)} mono />
        <Row label="From" value={moneyRefLabel(t.source)} />
        <Row label="To" value={moneyRefLabel(t.target)} />
        <Row label="Equity at request" value={moneyOrDash(t.equity_at_request, t.currency)} mono />
        <Row label="Equity verified" value={t.equity_verified ? 'Yes' : 'No'} />
        {t.note && <Row label="Investor note" value={t.note} />}
      </Section>
      <InvestorSection name={t.display_name} email={t.email} userId={t.user_id} />
      <Audit row={t} extra={t.done_at && (
        <Row label="Done" mono
             value={`${formatWhen(t.done_at)}${t.done_by != null ? ` by user #${t.done_by}` : ''}`} />
      )} />
    </div>
  )
}

function DestinationBody({ orgId, p }: { orgId: number; p: PayoutDestination }) {
  return (
    <div className="space-y-5">
      <Section title="Payout account">
        <Row label="Nickname" value={p.nickname} />
        <Row label="Kind" value={p.kind === 'bank' ? 'Bank account' : 'Crypto address'} />
        <Row label="Summary" value={p.summary} mono />
      </Section>
      <Section title="Destination details">
        <DetailRows details={p.details} />
      </Section>
      <InvestorSection name={p.display_name} email={p.email} userId={p.user_id} />
      {p.proof_file_id != null && <FilePreview orgId={orgId} fileId={p.proof_file_id} label="Proof" />}
      <Audit row={p} />
    </div>
  )
}

function Body({ orgId, details, destinations }: {
  orgId: number
  details: Details
  destinations: PayoutDestination[]
}) {
  switch (details.kind) {
    case 'deposits': return <DepositBody orgId={orgId} d={details.row} />
    case 'withdrawals': {
      const destination = destinations.find((p) => p.id === details.row.destination_id)
      return <WithdrawalBody w={details.row} destination={destination} />
    }
    case 'transfers': return <TransferBody t={details.row} />
    case 'payout_destinations': return <DestinationBody orgId={orgId} p={details.row} />
  }
}

/** One row's full story in the desk's Drawer; stays mounted (open=false)
 *  so the focus trap can hand focus back to the Details button on close.
 *  `destinations` is the page's full payout-destination list, which the
 *  withdrawal body searches for the account it pays to. */
export default function RequestDetailsDrawer({ orgId, details, destinations, onClose }: {
  orgId: number
  details: Details | null
  destinations: PayoutDestination[]
  onClose: () => void
}) {
  const word = details ? KIND_WORD[details.kind] : ''
  const title = details ? `${word.charAt(0).toUpperCase()}${word.slice(1)} #${details.row.id}` : ''
  return (
    <Drawer
      open={details != null}
      title={title}
      onClose={onClose}
      headerExtra={details && (
        <Badge tone={BADGE_TONE[statusTone(details.row.status)]}>{statusLabel(details.row.status)}</Badge>
      )}
    >
      {details && (
        <Body key={`${details.kind}-${details.row.id}`} orgId={orgId} details={details} destinations={destinations} />
      )}
    </Drawer>
  )
}
