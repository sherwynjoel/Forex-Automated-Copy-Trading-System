import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, formatWhen, money } from '../lib/format'
import { moneyOrDash, pillClass, statusLabel } from '../lib/investor'
import Banner from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import type {
  Account, InvestorDeposit, InvestorRow, InvestorWallet, InvestorWithdrawal,
} from '../lib/types'

const POLL_MS = 10000

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
  const [tab, setTab] = useState<'deposits' | 'withdrawals'>('deposits')
  const [pending, setPending] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

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
        setWallet((cur) => cur.address ? cur : { ...wl, memo: wl.memo ?? '' })
      } catch (err) {
        if (!(err instanceof Error && err.message.startsWith('404'))) throw err
      }
      setError(null)
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

  const saveWallet = (e: React.FormEvent) => {
    e.preventDefault()
    act(async () => {
      await orgApi(orgId, 'investor-wallet', { method: 'PUT', body: JSON.stringify(wallet) })
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
      <header>
        <h1 className="page-title">Investors</h1>
        <p className="text-sm text-ink-soft mt-1">
          Who invests through this workspace, their linked accounts, and the money requests
          waiting on you. The app records; you move the funds.
        </p>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3"><h2 className="desk-label">Investors</h2></div>
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
                      <select aria-label={`Account for ${r.email}`}
                              value={r.account_id ?? ''}
                              disabled={busy}
                              onChange={(e) => linkAccount(r.user_id, e.target.value ? Number(e.target.value) : null)}
                              className="border border-line-strong rounded bg-card px-2 py-1 text-sm">
                        <option value="">not linked</option>
                        {r.account_id != null && (
                          <option value={r.account_id}>{r.nickname ?? r.account_id}</option>
                        )}
                        {unlinked.map((a) => (
                          <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                            {a.nickname ?? a.trader_login} ({a.platform ?? 'ctrader'})
                          </option>
                        ))}
                      </select>
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
      </section>

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div role="tablist" className="flex border-b border-line">
          {(['deposits', 'withdrawals'] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                    className={`px-5 py-3 text-sm font-semibold ${tab === t ? 'text-brand border-b-2 border-brand' : 'text-ink-soft'}`}>
              {t === 'deposits' ? `Deposits (${deposits.filter((d) => d.status === 'pending').length} pending)`
                : `Withdrawals (${withdrawals.filter((w) => w.status === 'requested' || w.status === 'approved').length} open)`}
            </button>
          ))}
        </div>
        <div className="overflow-x-auto">
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
                    <td data-label="Status" className="px-5 py-2.5"><span className={`desk-label px-2 py-0.5 rounded ${pillClass(d.status)}`}>{statusLabel(d.status)}</span>{d.decision_note && <div className="text-xs text-ink-soft">{d.decision_note}</div>}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap">
                      {control && d.status === 'pending' && (
                        <>
                          <button aria-label={`Confirm deposit ${d.id}`} disabled={busy} onClick={() => decideDeposit(d, 'confirmed')}
                                  className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50 mr-2">Confirm</button>
                          <button aria-label={`Reject deposit ${d.id}`} disabled={busy} onClick={() => decideDeposit(d, 'rejected')}
                                  className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors disabled:opacity-50">Reject</button>
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
                    <td data-label="Status" className="px-5 py-2.5"><span className={`desk-label px-2 py-0.5 rounded ${pillClass(w.status)}`}>{statusLabel(w.status)}</span>{w.decision_note && <div className="text-xs text-ink-soft">{w.decision_note}</div>}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap">
                      {control && w.status === 'requested' && (
                        <button aria-label={`Approve withdrawal ${w.id}`} disabled={busy} onClick={() => decideWithdrawal(w, 'approved')}
                                className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50 mr-2">Approve</button>
                      )}
                      {control && w.status === 'approved' && (
                        <button aria-label={`Mark withdrawal ${w.id} paid`} disabled={busy} onClick={() => markPaid(w)}
                                className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50 mr-2">Mark paid</button>
                      )}
                      {control && (w.status === 'requested' || w.status === 'approved') && (
                        <button aria-label={`Reject withdrawal ${w.id}`} disabled={busy} onClick={() => decideWithdrawal(w, 'rejected')}
                                className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors disabled:opacity-50">Reject</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <form onSubmit={saveWallet} className="rounded-lg border border-line bg-card p-5 space-y-4">
        <h2 className="desk-label">Deposit wallet</h2>
        <p className="text-sm text-ink-soft">
          The address investors send to. Shown to them with a QR code. This is a receiving
          address only; MirrorFleet never holds a key.
        </p>
        <div className="flex gap-3 flex-wrap items-end">
          {([['coin', 'Coin', 'w-28'], ['network', 'Network', 'w-32'],
             ['address', 'Address', 'flex-1 min-w-64'], ['memo', 'Memo (optional)', 'w-40']] as const).map(([key, label, width]) => (
            <label key={key} className={`block ${width}`}>
              <span className="desk-label block mb-1">{label}</span>
              <input aria-label={label.replace(' (optional)', '')} value={wallet[key]} disabled={!control}
                     onChange={(e) => setWallet({ ...wallet, [key]: e.target.value })}
                     className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
          ))}
          {control && (
            <button type="submit" disabled={busy}
                    className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
              Save wallet
            </button>
          )}
        </div>
      </form>

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
