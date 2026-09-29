import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, formatWhen, money } from '../lib/format'
import { moneyOrDash, statusLabel, statusTone } from '../lib/investor'
import Banner from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import Button from '../components/Button'
import Input from '../components/Input'
import Select from '../components/Select'
import Badge, { type BadgeTone } from '../components/Badge'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import type { Account } from '../lib/types'
import type { InvestorDeposit, InvestorRow, InvestorWallet, InvestorWithdrawal } from '../lib/legacyInvestorTypes'

const POLL_MS = 10000

/** Maps the shared status-tone vocabulary onto the Badge's tone names --
 *  `pillClass`'s exact bg-*-wash/text-*-deep pairs, one level up. */
function statusBadgeTone(status: string): BadgeTone {
  const tone = statusTone(status)
  return tone === 'ok' ? 'profit' : tone === 'warn' ? 'warn' : tone === 'bad' ? 'loss' : 'neutral'
}

/** What the admin is being asked to confirm. `requireText` blocks the
 *  confirm button until the note/txid box has something in it. */
interface Pending {
  title: string
  confirmLabel: string
  textLabel: 'Note' | 'Transaction ID'
  requireText: boolean
  danger?: boolean
  run: (text: string) => Promise<void>
}

export default function Investors() {
  const { orgId, role } = useOrg()
  const [rows, setRows] = useState<InvestorRow[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [deposits, setDeposits] = useState<InvestorDeposit[]>([])
  const [withdrawals, setWithdrawals] = useState<InvestorWithdrawal[]>([])
  const [wallet, setWallet] = useState({ coin: '', network: '', address: '', memo: '' })
  // Guards the wallet card against being clobbered mid-edit by the poll /
  // live refresh / post-action refresh -- once the admin has touched the
  // form, refresh() leaves it alone until a save clears the flag. Read via
  // a ref inside refresh() so that callback (memoized once per orgId) never
  // sees a stale value.
  const [walletDirty, setWalletDirty] = useState(false)
  const walletDirtyRef = useRef(false)
  walletDirtyRef.current = walletDirty
  const [tab, setTab] = useState<'deposits' | 'withdrawals'>('deposits')
  const [pending, setPending] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Until the first load lands, the empty lists are unknown, not empty.
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [r, a, d, w] = await Promise.all([
        orgApi<InvestorRow[]>(orgId, 'investors'),
        orgApi<Account[]>(orgId, 'accounts'),
        orgApi<InvestorDeposit[]>(orgId, 'investor-deposits'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor-withdrawals'),
      ])
      setRows(r); setAccounts(a); setDeposits(d); setWithdrawals(w)
      try {
        const wl = await orgApi<InvestorWallet>(orgId, 'investor-wallet')
        setWallet((cur) => walletDirtyRef.current ? cur : { ...wl, memo: wl.memo ?? '' })
      } catch (err) {
        if (!(err instanceof Error && err.message.startsWith('404'))) throw err
      }
      setError(null)
      setLoaded(true)
    } catch (err) {
      setError(errorText(err, 'Could not load investors'))
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])
  useLiveRefresh(refresh, orgId)

  const control = can(role, 'control')

  const act = async (fn: () => Promise<void>, done: string) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(done)
      await refresh()
    } catch (err) {
      setError(errorText(err, 'The action failed'))
    } finally {
      setBusy(false)
    }
  }

  const linkAccount = (userId: number, accountId: number | null) => act(async () => {
    await orgApi(orgId, `investors/${userId}/account`, {
      method: 'PUT', body: JSON.stringify({ account_id: accountId }) })
  }, accountId == null ? 'Account unlinked' : 'Account linked')

  const editWallet = (patch: Partial<typeof wallet>) => {
    setWallet((w) => ({ ...w, ...patch }))
    setWalletDirty(true)
  }

  const saveWallet = (e: React.FormEvent) => {
    e.preventDefault()
    act(async () => {
      await orgApi(orgId, 'investor-wallet', { method: 'PUT', body: JSON.stringify(wallet) })
      setWalletDirty(false)
    }, 'Wallet saved')
  }

  const decideDeposit = (d: InvestorDeposit, status: 'confirmed' | 'rejected') => setPending({
    title: `${status === 'confirmed' ? 'Confirm' : 'Reject'} deposit of ${money(d.amount)} ${d.coin} from ${d.email}`,
    confirmLabel: status === 'confirmed' ? 'Confirm' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `investor-deposits/${d.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Deposit ${status}`),
  })

  const decideWithdrawal = (w: InvestorWithdrawal, status: 'approved' | 'rejected') => setPending({
    title: `${status === 'approved' ? 'Approve' : 'Reject'} withdrawal of ${money(w.amount)} for ${w.email}`,
    confirmLabel: status === 'approved' ? 'Approve' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `investor-withdrawals/${w.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Withdrawal ${status}`),
  })

  const markPaid = (w: InvestorWithdrawal) => setPending({
    title: `Record payment of ${money(w.amount)} to ${w.destination}`,
    confirmLabel: 'Mark paid', textLabel: 'Transaction ID', requireText: true,
    run: (txid) => act(async () => {
      await orgApi(orgId, `investor-withdrawals/${w.id}/paid`, {
        method: 'POST', body: JSON.stringify({ txid }) })
    }, 'Withdrawal marked paid'),
  })

  const linkedIds = new Set(rows.map((r) => r.account_id).filter((id) => id != null))
  const unlinked = accounts.filter((a) => !linkedIds.has(a.ctid_trader_account_id) && a.role !== 'master')

  const closeDialog = () => { setPending(null); setText('') }

  return (
    <div className="space-y-8 max-w-6xl">
      <PageHeader
        title="Investors"
        subtitle="Who invests through this workspace, their linked accounts, and the money requests waiting on you. The app records; you move the funds."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {!loaded ? (
        // An error before the first load shows the banner above, not an
        // endless skeleton.
        !error && <Loading lines={6} label="Loading investors" />
      ) : (
      <>
      <Card title="Investor accounts" inset>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Investor</th>
                <th className="desk-label px-5 py-2 font-semibold">Account</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Equity</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Net deposits</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Profit</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Pending</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No investors yet — invite one from Members with the Investor role.</td></tr>}
              {rows.map((r) => (
                <tr key={r.user_id} className="border-b border-line last:border-0">
                  <td data-label="Investor" className="px-5 py-2.5">
                    <div className="text-ink">{r.display_name}</div>
                    <div className="text-xs text-ink-soft">{r.email}</div>
                  </td>
                  <td data-label="Account" className="px-5 py-2.5">
                    {control ? (
                      <Select aria-label={`Account for ${r.email}`}
                              value={r.account_id ?? ''}
                              disabled={busy}
                              onChange={(e) => linkAccount(r.user_id, e.target.value ? Number(e.target.value) : null)}>
                        <option value="">not linked</option>
                        {r.account_id != null && (
                          <option value={r.account_id}>{r.nickname ?? r.account_id}</option>
                        )}
                        {unlinked.map((a) => (
                          <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                            {a.nickname ?? a.trader_login} ({a.platform ?? 'ctrader'})
                          </option>
                        ))}
                      </Select>
                    ) : (r.nickname ?? r.account_id ?? 'not linked')}
                  </td>
                  <td data-label="Equity" className="tnum px-5 py-2.5 text-right">{moneyOrDash(r.equity)}</td>
                  <td data-label="Net deposits" className="tnum px-5 py-2.5 text-right">{money(r.net_deposits)}</td>
                  <td data-label="Profit" className={`tnum px-5 py-2.5 text-right ${(r.profit ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>{moneyOrDash(r.profit)}</td>
                  <td data-label="Pending" className="tnum px-5 py-2.5 text-right">{r.pending_deposits} dep · {r.pending_withdrawals} wd</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Money requests">
        {/* The tab names carry what needs acting on (pending / open), which
            a bare total would not. */}
        <Tabs
          idBase="investors"
          label="Money requests"
          value={tab}
          onChange={(k) => setTab(k as 'deposits' | 'withdrawals')}
          items={[
            { key: 'deposits', label: `Deposits (${deposits.filter((d) => d.status === 'pending').length} pending)` },
            { key: 'withdrawals', label: `Withdrawals (${withdrawals.filter((w) => w.status === 'requested' || w.status === 'approved').length} open)` },
          ]}
        />
        <div id="investors-panel" role="tabpanel" aria-labelledby={`investors-tab-${tab}`} className="inset mt-4 overflow-x-auto">
          {tab === 'deposits' ? (
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Filed</th>
                  <th className="desk-label px-5 py-2 font-semibold">Investor</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Amount</th>
                  <th className="desk-label px-5 py-2 font-semibold">Transaction</th>
                  <th className="desk-label px-5 py-2 font-semibold">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {deposits.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No deposit notices</td></tr>}
                {deposits.map((d) => (
                  <tr key={d.id} className="border-b border-line last:border-0">
                    <td data-label="Filed" className="num px-5 py-2.5">{formatWhen(d.created_at)}</td>
                    <td data-label="Investor" className="px-5 py-2.5">{d.display_name}<div className="text-xs text-ink-soft">{d.email}</div></td>
                    <td data-label="Amount" className="tnum px-5 py-2.5 text-right">{money(d.amount)} {d.coin}</td>
                    <td data-label="Transaction" className="num px-5 py-2.5 break-all">{d.txid}{d.note && <div className="text-xs text-ink-soft">{d.note}</div>}</td>
                    <td data-label="Status" className="px-5 py-2.5"><Badge tone={statusBadgeTone(d.status)}>{statusLabel(d.status)}</Badge>{d.decision_note && <div className="text-xs text-ink-soft">{d.decision_note}</div>}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap">
                      {control && d.status === 'pending' && (
                        <>
                          <Button size="sm" className="mr-2" disabled={busy} aria-label={`Confirm deposit ${d.id}`} onClick={() => decideDeposit(d, 'confirmed')}>Confirm</Button>
                          <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject deposit ${d.id}`} onClick={() => decideDeposit(d, 'rejected')}>Reject</Button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Requested</th>
                  <th className="desk-label px-5 py-2 font-semibold">Investor</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Amount</th>
                  <th className="desk-label px-5 py-2 font-semibold">Destination</th>
                  <th className="desk-label px-5 py-2 font-semibold">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {withdrawals.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No withdrawal requests</td></tr>}
                {withdrawals.map((w) => (
                  <tr key={w.id} className="border-b border-line last:border-0">
                    <td data-label="Requested" className="num px-5 py-2.5">{formatWhen(w.created_at)}</td>
                    <td data-label="Investor" className="px-5 py-2.5">{w.display_name}<div className="text-xs text-ink-soft">{w.email}</div></td>
                    <td data-label="Amount" className="tnum px-5 py-2.5 text-right">{money(w.amount)}
                      {!w.equity_verified && <div className="text-xs text-warn-deep">equity unverified</div>}
                      {w.equity_verified && w.equity_at_request != null && <div className="text-xs text-ink-soft">of {money(w.equity_at_request)} equity</div>}
                    </td>
                    <td data-label="Destination" className="num px-5 py-2.5 break-all">{w.destination}{w.txid && <div className="text-xs text-ink-soft">tx {w.txid}</div>}</td>
                    <td data-label="Status" className="px-5 py-2.5"><Badge tone={statusBadgeTone(w.status)}>{statusLabel(w.status)}</Badge>{w.decision_note && <div className="text-xs text-ink-soft">{w.decision_note}</div>}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap">
                      {control && w.status === 'requested' && (
                        <Button size="sm" className="mr-2" disabled={busy} aria-label={`Approve withdrawal ${w.id}`} onClick={() => decideWithdrawal(w, 'approved')}>Approve</Button>
                      )}
                      {control && w.status === 'approved' && (
                        <Button size="sm" className="mr-2" disabled={busy} aria-label={`Mark withdrawal ${w.id} paid`} onClick={() => markPaid(w)}>Mark paid</Button>
                      )}
                      {control && (w.status === 'requested' || w.status === 'approved') && (
                        <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject withdrawal ${w.id}`} onClick={() => decideWithdrawal(w, 'rejected')}>Reject</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <Card title="Deposit wallet">
      <form onSubmit={saveWallet} className="space-y-4">
        <p className="text-sm text-ink-soft">
          The address investors send to. Shown to them with a QR code. This is a receiving
          address only; MirrorFleet never holds a key.
        </p>
        <div className="flex gap-3 flex-wrap items-end">
          {([['coin', 'Coin', 'w-28'], ['network', 'Network', 'w-32'],
             ['address', 'Address', 'flex-1 min-w-64'], ['memo', 'Memo (optional)', 'w-40']] as const).map(([key, label, width]) => (
            <label key={key} className={`block ${width}`}>
              <span className="desk-label block mb-1">{label}</span>
              <Input aria-label={label.replace(' (optional)', '')} value={wallet[key]} disabled={!control} num
                     onChange={(e) => editWallet({ [key]: e.target.value })} />
            </label>
          ))}
          {control && (
            <Button type="submit" disabled={busy}>
              Save wallet
            </Button>
          )}
        </div>
      </form>
      </Card>
      </>
      )}

      <ConfirmDialog
        open={pending != null}
        title={pending?.title ?? ''}
        confirmLabel={pending?.confirmLabel ?? 'Confirm'}
        danger={pending?.danger}
        busy={busy}
        disabled={Boolean(pending?.requireText) && text.trim() === ''}
        onConfirm={async () => {
          if (!pending) return
          const run = pending.run
          const value = text.trim()
          closeDialog()
          await run(value)
        }}
        onCancel={closeDialog}
      >
        <label className="block">
          <span className="desk-label block mb-1">
            {pending?.textLabel}{pending?.requireText ? '' : ' (optional)'}
          </span>
          <textarea aria-label={pending?.textLabel} value={text} rows={2}
                    onChange={(e) => setText(e.target.value)}
                    className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
        </label>
      </ConfirmDialog>
    </div>
  )
}
