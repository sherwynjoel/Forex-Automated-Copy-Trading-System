import type { ReactNode } from 'react'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import type { TabItem } from '../../components/Tabs'
import { formatWhen, money } from '../../lib/format'
import { BADGE_TONE, statusLabel, statusTone, walletLabel } from '../../lib/investor'
import type { RequestKind as StatusKind } from '../../lib/investor'
import type {
  MoneyRef, PayoutDestination, PortalDeposit, PortalTransfer, PortalWithdrawal, RequestsSummary,
} from '../../lib/types'

export type RequestKind = 'deposits' | 'withdrawals' | 'transfers' | 'payout_destinations'
export const REQUEST_KINDS: RequestKind[] = ['deposits', 'withdrawals', 'transfers', 'payout_destinations']

/** The desk's tabs: the four money queues plus phase 2's two identity queues. */
export type DeskTab = RequestKind | 'kyc' | 'account_requests'
export const DESK_TABS: DeskTab[] = [...REQUEST_KINDS, 'kyc', 'account_requests']

/** Task 11's `statusLabel`/`statusTone` take their own singular `RequestKind`
 *  (`lib/investor.ts`) -- an "approved" transfer reads "Approved, in
 *  progress" and an "approved" payout account reads "Approved" at ok tone,
 *  neither of which the generic `approved` label/tone gets right. This maps
 *  the desk's plural, per-endpoint kind onto that contract's kind so every
 *  status chip on this page -- table row and drawer header alike -- goes
 *  through the one function instead of a hand-coded override. */
export const STATUS_KIND: Record<RequestKind, StatusKind> = {
  deposits: 'deposit',
  withdrawals: 'withdrawal',
  transfers: 'transfer',
  payout_destinations: 'destination',
}

/** Statuses that still need an admin: what the Open view shows and what
 *  requests/summary counts (withdrawals and transfers count approved too,
 *  because paying or funding is still owed). */
const OPEN_STATUSES: Record<RequestKind, ReadonlySet<string>> = {
  deposits: new Set(['pending']),
  withdrawals: new Set(['requested', 'approved']),
  transfers: new Set(['requested', 'approved']),
  payout_destinations: new Set(['pending']),
}

export function isOpen(kind: RequestKind, status: string): boolean {
  return OPEN_STATUSES[kind].has(status)
}

/** What a row is called in aria-labels, notices and drawer titles. */
export const KIND_WORD: Record<RequestKind, string> = {
  deposits: 'deposit',
  withdrawals: 'withdrawal',
  transfers: 'transfer',
  payout_destinations: 'payout account',
}

export function tabItems(summary: RequestsSummary | null): TabItem[] {
  // `?? 0`: an api that predates a queue answers without its count.
  const n = (k: Exclude<keyof RequestsSummary, 'total'>) => summary?.[k] ?? 0
  return [
    { key: 'deposits', label: `Deposits (${n('deposits')})` },
    { key: 'withdrawals', label: `Withdrawals (${n('withdrawals')})` },
    { key: 'transfers', label: `Transfers (${n('transfers')})` },
    { key: 'payout_destinations', label: `Payout accounts (${n('payout_destinations')})` },
    { key: 'kyc', label: `Verification (${n('kyc')})` },
    { key: 'account_requests', label: `Account requests (${n('account_requests')})` },
  ]
}

/** "My wallet" or "Trading account 1001". */
export function moneyRefLabel(ref: MoneyRef): string {
  return ref.kind === 'wallet' && ref.wallet ? walletLabel(ref.wallet) : `Trading account ${ref.account_id ?? '?'}`
}

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'

interface Common {
  control: boolean
  busy: boolean
  show: 'open' | 'all'
}

function Head({ cols }: { cols: (string | [string, 'right'])[] }) {
  return (
    <thead>
      <tr className="text-left border-b border-line">
        {cols.map((c) => Array.isArray(c)
          ? <th key={c[0]} className={`${TH} text-right`}>{c[0]}</th>
          : <th key={c} className={TH}>{c}</th>)}
        <th></th>
      </tr>
    </thead>
  )
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return <tr><td colSpan={colSpan} className="text-center py-8 text-ink-faint">{text}</td></tr>
}

function emptyText(show: 'open' | 'all', plural: string): string {
  return show === 'open' ? `No open ${plural}` : `No ${plural} yet`
}

function Who({ name, email }: { name?: string; email?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-ink">{name ?? '—'}</div>
      <div className="text-xs text-ink-soft">{email ?? ''}</div>
    </div>
  )
}

/** The status chip plus the admin's note under it. A transfer's `approved`
 *  means "acknowledged, funding in progress", not "payment pending"; a
 *  payout account's `approved` is simply usable, so it reads "Approved" at
 *  ok tone rather than "payment pending" at warn -- both come from
 *  `statusLabel`/`statusTone` once the kind is passed through. */
function Status({ kind, status, note }: { kind: RequestKind; status: string; note: string | null }) {
  const statusKind = STATUS_KIND[kind]
  return (
    <div className="min-w-0">
      <Badge tone={BADGE_TONE[statusTone(status, statusKind)]}>{statusLabel(status, statusKind)}</Badge>
      {note && <div className="text-xs text-ink-soft mt-1">{note}</div>}
    </div>
  )
}

function Actions({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-end gap-2">{children}</div>
}

export function DepositsTable({ rows, control, busy, show, onConfirm, onReject, onDetails }: Common & {
  rows: PortalDeposit[]
  onConfirm: (d: PortalDeposit) => void
  onReject: (d: PortalDeposit) => void
  onDetails: (d: PortalDeposit) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Filed', 'Investor', ['Amount', 'right'], 'Method', 'Reference', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={7} text={emptyText(show, 'deposits')} />}
        {rows.map((d) => (
          <tr key={d.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Filed" className={`num ${TD}`}>{formatWhen(d.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={d.display_name} email={d.email} /></td>
            <td data-label="Amount" className={`${TD} text-right`}>
              <div className="min-w-0">
                <div className="num text-ink">{money(d.amount, d.currency)}</div>
                <div className="num text-xs text-ink-soft">
                  {`${d.fee > 0 ? `fee ${money(d.fee)} · ` : ''}${
                    d.status === 'confirmed' ? `credited ${money(d.credited_amount)}`
                    : `to ${d.target === 'account' ? 'trading account' : 'wallet'}`}`}
                </div>
              </div>
            </td>
            <td data-label="Method" className={TD}>{d.method_label}</td>
            <td data-label="Reference" className={`num ${TD} break-all`}>
              <div className="min-w-0">
                <div>{d.reference}</div>
                {d.note && <div className="text-xs text-ink-soft">{d.note}</div>}
              </div>
            </td>
            <td data-label="Status" className={TD}><Status kind="deposits" status={d.status} note={d.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && d.status === 'pending' && (
                  <>
                    <Button size="sm" disabled={busy} aria-label={`Confirm deposit ${d.id}`} onClick={() => onConfirm(d)}>Confirm</Button>
                    <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject deposit ${d.id}`} onClick={() => onReject(d)}>Reject</Button>
                  </>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of deposit ${d.id}`} onClick={() => onDetails(d)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function WithdrawalsTable({ rows, control, busy, show, onApprove, onReject, onPaid, onDetails }: Common & {
  rows: PortalWithdrawal[]
  onApprove: (w: PortalWithdrawal) => void
  onReject: (w: PortalWithdrawal) => void
  onPaid: (w: PortalWithdrawal) => void
  onDetails: (w: PortalWithdrawal) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Requested', 'Investor', ['Amount', 'right'], 'Destination', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={6} text={emptyText(show, 'withdrawals')} />}
        {rows.map((w) => (
          <tr key={w.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Requested" className={`num ${TD}`}>{formatWhen(w.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={w.display_name} email={w.email} /></td>
            <td data-label="Amount" className={`${TD} text-right`}>
              <div className="min-w-0">
                <div className="num text-ink">{money(w.amount, w.currency)}</div>
                <div className="num text-xs text-ink-soft">
                  {`${w.fee > 0 ? `fee ${money(w.fee)} · ` : ''}pay ${money(w.net_amount)}`}
                </div>
              </div>
            </td>
            <td data-label="Destination" className={`${TD} break-all`}>
              <div className="min-w-0">
                <div className="text-ink">{w.destination_summary}</div>
                {w.txid && <div className="num text-xs text-ink-soft">tx {w.txid}</div>}
              </div>
            </td>
            <td data-label="Status" className={TD}><Status kind="withdrawals" status={w.status} note={w.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && w.status === 'requested' && (
                  <Button size="sm" disabled={busy} aria-label={`Approve withdrawal ${w.id}`} onClick={() => onApprove(w)}>Approve</Button>
                )}
                {control && w.status === 'approved' && (
                  <Button size="sm" disabled={busy} aria-label={`Mark withdrawal ${w.id} paid`} onClick={() => onPaid(w)}>Mark paid</Button>
                )}
                {control && (w.status === 'requested' || w.status === 'approved') && (
                  <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject withdrawal ${w.id}`} onClick={() => onReject(w)}>Reject</Button>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of withdrawal ${w.id}`} onClick={() => onDetails(w)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function TransfersTable({ rows, control, busy, show, onApprove, onDone, onReject, onDetails }: Common & {
  rows: PortalTransfer[]
  onApprove: (t: PortalTransfer) => void
  onDone: (t: PortalTransfer) => void
  onReject: (t: PortalTransfer) => void
  onDetails: (t: PortalTransfer) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Requested', 'Investor', ['Amount', 'right'], 'Movement', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={6} text={emptyText(show, 'transfers')} />}
        {rows.map((t) => (
          <tr key={t.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Requested" className={`num ${TD}`}>{formatWhen(t.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={t.display_name} email={t.email} /></td>
            <td data-label="Amount" className={`${TD} text-right`}>
              <div className="min-w-0">
                <div className="num text-ink">{money(t.amount, t.currency)}</div>
                {t.source.kind === 'account' && !t.equity_verified && (
                  <div className="text-xs text-warn-deep">equity unverified</div>
                )}
                {t.source.kind === 'account' && t.equity_verified && t.equity_at_request != null && (
                  <div className="num text-xs text-ink-soft">of {money(t.equity_at_request)} equity</div>
                )}
              </div>
            </td>
            <td data-label="Movement" className={TD}>
              <div className="min-w-0">
                <div className="text-ink">{`${moneyRefLabel(t.source)} → ${moneyRefLabel(t.target)}`}</div>
                {t.note && <div className="text-xs text-ink-soft">{t.note}</div>}
              </div>
            </td>
            <td data-label="Status" className={TD}><Status kind="transfers" status={t.status} note={t.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && t.status === 'requested' && (
                  <Button variant="secondary" size="sm" disabled={busy} aria-label={`Approve transfer ${t.id}`} onClick={() => onApprove(t)}>Approve</Button>
                )}
                {control && (t.status === 'requested' || t.status === 'approved') && (
                  <>
                    <Button size="sm" disabled={busy} aria-label={`Mark transfer ${t.id} done`} onClick={() => onDone(t)}>Mark done</Button>
                    <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject transfer ${t.id}`} onClick={() => onReject(t)}>Reject</Button>
                  </>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of transfer ${t.id}`} onClick={() => onDetails(t)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function DestinationsTable({ rows, control, busy, show, onApprove, onReject, onDetails }: Common & {
  rows: PayoutDestination[]
  onApprove: (p: PayoutDestination) => void
  onReject: (p: PayoutDestination) => void
  onDetails: (p: PayoutDestination) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Added', 'Investor', 'Nickname', 'Destination', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={6} text={emptyText(show, 'payout accounts')} />}
        {rows.map((p) => (
          <tr key={p.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Added" className={`num ${TD}`}>{formatWhen(p.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={p.display_name} email={p.email} /></td>
            <td data-label="Nickname" className={TD}>
              <div className="min-w-0">
                <div className="text-ink">{p.nickname}</div>
                <Badge tone="neutral" className="mt-1">{p.kind === 'bank' ? 'Bank' : 'Crypto'}</Badge>
              </div>
            </td>
            <td data-label="Destination" className={`num ${TD} break-all`}>{p.summary}</td>
            <td data-label="Status" className={TD}><Status kind="payout_destinations" status={p.status} note={p.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && p.status === 'pending' && (
                  <>
                    <Button size="sm" disabled={busy} aria-label={`Approve payout account ${p.id}`} onClick={() => onApprove(p)}>Approve</Button>
                    <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject payout account ${p.id}`} onClick={() => onReject(p)}>Reject</Button>
                  </>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of payout account ${p.id}`} onClick={() => onDetails(p)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
