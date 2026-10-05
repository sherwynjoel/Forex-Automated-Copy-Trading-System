import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, money } from '../lib/format'
import Banner from '../components/Banner'
import Card from '../components/Card'
import ConfirmDialog from '../components/ConfirmDialog'
import Input from '../components/Input'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import Select from '../components/Select'
import Tabs from '../components/Tabs'
import {
  DESK_TABS, DepositsTable, DestinationsTable, TransfersTable, WithdrawalsTable,
  isOpen, moneyRefLabel, tabItems, type DeskTab, type RequestKind,
} from './requests/RequestTabs'
import AccountRequestsTab from './requests/AccountRequestsTab'
import VerificationTab from './requests/VerificationTab'
import RequestDetailsDrawer, { type Details } from './requests/RequestDetailsDrawer'
import type {
  PayoutDestination, PortalDeposit, PortalTransfer, PortalWithdrawal, RequestsSummary,
} from '../lib/types'

const POLL_MS = 10000
const AMOUNT = /^\d+(\.\d{1,2})?$/

/** What the admin is being asked to confirm. `requireText` blocks the
 *  confirm button until the note/txid box has something in it; `credited`
 *  (Confirm deposit only) adds the credited amount input, prefilled. */
interface Pending {
  title: string
  confirmLabel: string
  textLabel: 'Note' | 'Transaction ID'
  requireText: boolean
  danger?: boolean
  credited?: string
  /** The notice amount; the credited amount may not exceed it. */
  creditedMax?: number
  run: (text: string, credited: string) => Promise<void>
}

function isTab(v: string | null): v is DeskTab {
  return DESK_TABS.includes(v as DeskTab)
}

export default function Requests() {
  const { orgId, role } = useOrg()
  const [searchParams] = useSearchParams()
  // The Investors page's chips deep-link here with ?tab=<kind>.
  const [tab, setTab] = useState<DeskTab>(() => {
    const t = searchParams.get('tab')
    return isTab(t) ? t : 'deposits'
  })
  const [show, setShow] = useState<'open' | 'all'>('open')
  const [summary, setSummary] = useState<RequestsSummary | null>(null)
  const [deposits, setDeposits] = useState<PortalDeposit[]>([])
  const [withdrawals, setWithdrawals] = useState<PortalWithdrawal[]>([])
  const [transfers, setTransfers] = useState<PortalTransfer[]>([])
  const [destinations, setDestinations] = useState<PayoutDestination[]>([])
  const [pending, setPending] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  const [credited, setCredited] = useState('')
  // Set only while `pending` is open: a decision that fails (a 409, most
  // often -- someone else already decided the row) is shown inside the
  // dialog that is still open over it, never dumped on the page banner
  // behind the overlay.
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [details, setDetails] = useState<Details | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Until the first load lands, the empty lists are unknown, not empty.
  const [loaded, setLoaded] = useState(false)
  // Bumped on every refresh(): the 10s poll, a live-refresh burst and a
  // just-decided action can all fire a fetch while an earlier one is still
  // in flight. An answer lands only if it is newer than the one already on
  // screen (latest-APPLIED wins): an older answer arriving late is stale
  // and dropped, but a newer one is never held back just because an even
  // newer request has started -- on a slow link, with the poll and live
  // events overlapping, latest-STARTED-wins could starve every answer.
  const requestSeq = useRef(0)
  const lastApplied = useRef(0)

  const refresh = useCallback(async () => {
    const seq = ++requestSeq.current
    try {
      const [s, d, w, t, p] = await Promise.all([
        orgApi<RequestsSummary>(orgId, 'requests/summary'),
        orgApi<PortalDeposit[]>(orgId, 'deposits'),
        orgApi<PortalWithdrawal[]>(orgId, 'withdrawals'),
        orgApi<PortalTransfer[]>(orgId, 'transfers'),
        orgApi<PayoutDestination[]>(orgId, 'payout-destinations'),
      ])
      if (seq <= lastApplied.current) return
      lastApplied.current = seq
      setSummary(s); setDeposits(d); setWithdrawals(w); setTransfers(t); setDestinations(p)
      setError(null)
      setLoaded(true)
    } catch (err) {
      if (seq <= lastApplied.current) return
      lastApplied.current = seq
      setError(errorText(err, 'Could not load the requests'))
    }
  }, [orgId])

  // An org switch drops every answer still in flight for the previous org
  // and clears its rows -- and closes any open decision dialog, whose
  // action targets the previous org's row. Declared before the effect below
  // so the new org's first refresh is numbered after the cut.
  useEffect(() => {
    lastApplied.current = requestSeq.current
    setSummary(null); setDeposits([]); setWithdrawals([]); setTransfers([]); setDestinations([])
    setLoaded(false)
    setPending(null); setText(''); setCredited(''); setDialogError(null)
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])
  useLiveRefresh(refresh, orgId)

  const control = can(role, 'control')
  // The identity tabs load their own queues; a decision there refreshes the
  // summary counts here and is announced on the page banner.
  const tabDone = (message: string) => { setNotice(message); void refresh() }

  /** Runs one decision POST for the open dialog. On success the dialog
   *  closes, a page notice announces it and the queues refetch. On failure
   *  the dialog stays open with the server's message inside it (verbatim,
   *  minus the status-code prefix) so the admin can read it and retry or
   *  cancel -- never a page banner hidden behind the still-open overlay. */
  const act = async (fn: () => Promise<void>, done: string) => {
    setBusy(true); setDialogError(null)
    try {
      await fn()
      setNotice(done)
      closeDialog()
      await refresh()
    } catch (err) {
      setDialogError(errorText(err, 'The action failed'))
    } finally {
      setBusy(false)
    }
  }

  const open = (p: Pending) => { setPending(p); setText(''); setCredited(p.credited ?? ''); setDialogError(null) }
  const closeDialog = () => { setPending(null); setText(''); setCredited(''); setDialogError(null) }

  const decideDeposit = (d: PortalDeposit, status: 'confirmed' | 'rejected') => open({
    title: `${status === 'confirmed' ? 'Confirm' : 'Reject'} deposit of ${money(d.amount, d.currency)} from ${d.email}`,
    confirmLabel: status === 'confirmed' ? 'Confirm' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    credited: status === 'confirmed' ? (d.amount - d.fee).toFixed(2) : undefined,
    creditedMax: d.amount,
    run: (note, creditedAmount) => act(async () => {
      const body = status === 'confirmed'
        ? { status, credited_amount: creditedAmount, note }
        : { status, note }
      await orgApi(orgId, `deposits/${d.id}/decision`, { method: 'POST', body: JSON.stringify(body) })
    }, `Deposit ${status}`),
  })

  const decideWithdrawal = (w: PortalWithdrawal, status: 'approved' | 'rejected') => open({
    title: `${status === 'approved' ? 'Approve' : 'Reject'} withdrawal of ${money(w.net_amount, w.currency)} to ${w.destination_summary} for ${w.email}`,
    confirmLabel: status === 'approved' ? 'Approve' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `withdrawals/${w.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Withdrawal ${status}`),
  })

  const markPaid = (w: PortalWithdrawal) => open({
    title: `Record payment of ${money(w.net_amount, w.currency)} to ${w.destination_summary}`,
    confirmLabel: 'Mark paid', textLabel: 'Transaction ID', requireText: true,
    run: (txid) => act(async () => {
      await orgApi(orgId, `withdrawals/${w.id}/paid`, { method: 'POST', body: JSON.stringify({ txid }) })
    }, 'Withdrawal marked paid'),
  })

  const decideTransfer = (t: PortalTransfer, status: 'approved' | 'done' | 'rejected') => {
    const movement = `${money(t.amount, t.currency)} from ${moneyRefLabel(t.source)} to ${moneyRefLabel(t.target)}`
    open({
      title: status === 'approved' ? `Acknowledge transfer of ${movement}`
        : status === 'done' ? `Mark transfer of ${movement} done`
        : `Reject transfer of ${movement}`,
      confirmLabel: status === 'approved' ? 'Approve' : status === 'done' ? 'Mark done' : 'Reject',
      textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
      run: (note) => act(async () => {
        await orgApi(orgId, `transfers/${t.id}/decision`, {
          method: 'POST', body: JSON.stringify({ status, note }) })
      }, `Transfer ${status}`),
    })
  }

  const decideDestination = (p: PayoutDestination, status: 'approved' | 'rejected') => open({
    title: `${status === 'approved' ? 'Approve' : 'Reject'} payout account ${p.nickname} (${p.summary}) for ${p.email}`,
    confirmLabel: status === 'approved' ? 'Approve' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `payout-destinations/${p.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Payout account ${status}`),
  })

  const visible = <T extends { status: string }>(kind: RequestKind, rows: T[]): T[] =>
    show === 'open' ? rows.filter((r) => isOpen(kind, r.status)) : rows

  const overCredit = pending?.creditedMax != null && Number(credited.trim()) > pending.creditedMax
  const confirmBlocked =
    (Boolean(pending?.requireText) && text.trim() === '') ||
    (pending?.credited != null && !AMOUNT.test(credited.trim())) || overCredit

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Requests"
        subtitle="Every request waiting on you: deposit notices to confirm, withdrawals to approve and pay, transfers to fund, payout accounts to vet, identities to verify and trading accounts to open. The app records; you move the funds."
        actions={
          <Select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value as 'open' | 'all')}>
            <option value="open">Open</option>
            <option value="all">All</option>
          </Select>
        }
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      <Tabs
        idBase="requests"
        label="Request types"
        value={tab}
        onChange={(k) => setTab(k as DeskTab)}
        items={tabItems(summary)}
      />

      {!loaded ? (
        // An error before the first load shows the banner above, not an
        // endless skeleton.
        !error && <Loading lines={6} label="Loading requests" />
      ) : (
        <div id="requests-panel" role="tabpanel" aria-labelledby={`requests-tab-${tab}`}>
          <Card inset>
            <div className="overflow-x-auto">
              {tab === 'deposits' && (
                <DepositsTable rows={visible('deposits', deposits)} control={control} busy={busy} show={show}
                               onConfirm={(d) => decideDeposit(d, 'confirmed')}
                               onReject={(d) => decideDeposit(d, 'rejected')}
                               onDetails={(row) => setDetails({ kind: 'deposits', row })} />
              )}
              {tab === 'withdrawals' && (
                <WithdrawalsTable rows={visible('withdrawals', withdrawals)} control={control} busy={busy} show={show}
                                  onApprove={(w) => decideWithdrawal(w, 'approved')}
                                  onReject={(w) => decideWithdrawal(w, 'rejected')}
                                  onPaid={markPaid}
                                  onDetails={(row) => setDetails({ kind: 'withdrawals', row })} />
              )}
              {tab === 'transfers' && (
                <TransfersTable rows={visible('transfers', transfers)} control={control} busy={busy} show={show}
                                onApprove={(t) => decideTransfer(t, 'approved')}
                                onDone={(t) => decideTransfer(t, 'done')}
                                onReject={(t) => decideTransfer(t, 'rejected')}
                                onDetails={(row) => setDetails({ kind: 'transfers', row })} />
              )}
              {tab === 'payout_destinations' && (
                <DestinationsTable rows={visible('payout_destinations', destinations)} control={control} busy={busy} show={show}
                                   onApprove={(p) => decideDestination(p, 'approved')}
                                   onReject={(p) => decideDestination(p, 'rejected')}
                                   onDetails={(row) => setDetails({ kind: 'payout_destinations', row })} />
              )}
              {/* key={orgId}: an org switch remounts the tab, so its queue,
                  open dialog/drawer and any revealed passwords are dropped. */}
              {tab === 'kyc' && (
                <VerificationTab key={orgId} orgId={orgId} control={control} show={show}
                                 onDone={tabDone} />
              )}
              {tab === 'account_requests' && (
                <AccountRequestsTab key={orgId} orgId={orgId} control={control} show={show}
                                    onDone={tabDone} />
              )}
            </div>
          </Card>
        </div>
      )}

      <ConfirmDialog
        open={pending != null}
        title={pending?.title ?? ''}
        confirmLabel={pending?.confirmLabel ?? 'Confirm'}
        danger={pending?.danger}
        busy={busy}
        disabled={confirmBlocked}
        onConfirm={() => {
          if (!pending) return
          void pending.run(text.trim(), credited.trim())
        }}
        onCancel={closeDialog}
      >
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        {pending?.credited != null && (
          <label className="block">
            <span className="desk-label block mb-1">Credited amount</span>
            <Input aria-label="Credited amount" num inputMode="decimal" value={credited}
                   onChange={(e) => setCredited(e.target.value)} />
            {overCredit && (
              <span role="alert" className="block mt-1 text-xs text-loss-deep">
                Credited amount cannot exceed the notice amount ({pending?.creditedMax?.toFixed(2)})
              </span>
            )}
            <span className="block mt-1 text-xs text-ink-soft">
              Prefilled with the amount less the method fee. Change it when what arrived differs.
            </span>
          </label>
        )}
        <label className="block">
          <span className="desk-label block mb-1">
            {pending?.textLabel}{pending?.requireText ? '' : ' (optional)'}
          </span>
          <textarea aria-label={pending?.textLabel} value={text} rows={2}
                    onChange={(e) => setText(e.target.value)}
                    className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
        </label>
      </ConfirmDialog>

      {/* The full destination list rides along so a withdrawal's drawer can
          show the unmasked account the admin pays to. */}
      <RequestDetailsDrawer orgId={orgId} details={details} destinations={destinations}
                            onClose={() => setDetails(null)} />
    </div>
  )
}
