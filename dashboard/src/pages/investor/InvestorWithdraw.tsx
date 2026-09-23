import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { moneyOrDash, pillClass, statusLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import type { InvestorSummary, InvestorWithdrawal } from '../../lib/types'

const STEPS = ['requested', 'approved', 'paid'] as const

function Timeline({ w }: { w: InvestorWithdrawal }) {
  if (w.status === 'rejected') {
    return <span className={`desk-label px-2 py-0.5 rounded ${pillClass('rejected')}`}>Rejected</span>
  }
  const reached = STEPS.indexOf(w.status as typeof STEPS[number])
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEPS.map((step, i) => (
        <li key={step} className={`px-2 py-0.5 rounded ${i <= reached ? pillClass(step) : 'bg-paper text-ink-faint'}`}>
          {statusLabel(step)}
        </li>
      ))}
    </ol>
  )
}

export default function InvestorWithdraw() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<InvestorWithdrawal[]>([])
  const [form, setForm] = useState({ amount: '', destination: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      setSummary(s); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your withdrawals'))
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<InvestorWithdrawal>(orgId, 'investor/withdrawals', {
        method: 'POST', body: JSON.stringify(form),
      })
      setForm({ amount: '', destination: '' })
      setNotice('Request sent. You will be emailed when an admin decides.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not send the request'))
    } finally {
      setBusy(false)
    }
  }

  const linked = summary?.link_state === 'linked'

  return (
    <div className="space-y-6 max-w-4xl">
      <header>
        <h1 className="page-title">Withdraw</h1>
        <p className="text-sm text-ink-soft mt-1">
          Ask for an amount and where to send it. An admin approves, pays from the
          workspace wallet, and records the transaction.
        </p>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {summary && !linked && (
        <section className="rounded-lg border border-line bg-card p-5">
          <p className="text-sm text-ink">Your account is being set up. Withdrawals open once it is linked.</p>
        </section>
      )}

      {summary && linked && (
        <form onSubmit={submit} className="rounded-lg border border-line bg-card p-5 space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 className="desk-label">Available to withdraw</h2>
            <span className="num text-2xl font-semibold text-ink">{moneyOrDash(summary.available)}</span>
          </div>
          {summary.available == null && (
            <p className="text-xs text-warn-deep">
              Your account is offline right now, so the available figure is unknown; an admin will check it.
            </p>
          )}
          <div className="flex gap-3 flex-wrap items-end">
            <label className="block w-40">
              <span className="desk-label block mb-1">Amount</span>
              <input aria-label="Amount" value={form.amount} required
                     onChange={(e) => setForm({ ...form, amount: e.target.value })}
                     className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
            <label className="block flex-1 min-w-56">
              <span className="desk-label block mb-1">Destination address</span>
              <input aria-label="Destination address" value={form.destination} required
                     onChange={(e) => setForm({ ...form, destination: e.target.value })}
                     className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
          </div>
          <button type="submit" disabled={busy}
                  className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
            Request withdrawal
          </button>
        </form>
      )}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3"><h2 className="desk-label">Your requests</h2></div>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No requests yet</li>}
          {rows.map((w) => (
            <li key={w.id} className="px-5 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(w.created_at)}</span>
                <span className="tnum font-semibold text-ink">{money(w.amount)}</span>
                <span className="num text-ink-soft break-all">to {w.destination}</span>
              </div>
              <Timeline w={w} />
              {w.decision_note && <p className="text-xs text-ink-soft">Admin: {w.decision_note}</p>}
              {w.txid && <p className="num text-xs text-ink-soft break-all">Transaction: {w.txid}</p>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
