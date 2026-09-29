import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, moneyOrDash, pillClass, shortAddress, statusLabel } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import NextStep from './NextStep'
import type { InvestorSummary, InvestorWallet, InvestorWithdrawal } from '../../lib/legacyInvestorTypes'

const STEPS = ['requested', 'approved', 'paid'] as const

function Timeline({ w }: { w: InvestorWithdrawal }) {
  if (w.status === 'rejected') {
    return <Badge tone="loss">Rejected</Badge>
  }
  const reached = STEPS.indexOf(w.status as typeof STEPS[number])
  return (
    <ol aria-label="Withdrawal progress" className="flex flex-wrap items-center gap-2 text-xs">
      {STEPS.map((step, i) => {
        const current = i === reached
        return (
          <li key={step} aria-current={current ? 'step' : undefined}
              className={`px-2 py-0.5 rounded-full ${i <= reached ? pillClass(step) : 'bg-paper text-ink-faint'} ${current ? 'font-semibold' : ''}`}>
            <span>{statusLabel(step)}</span>
            {/* Colour is never the only signal: the current step says so. */}
            {current && <span> · current</span>}
          </li>
        )
      })}
    </ol>
  )
}

export default function InvestorWithdraw() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<InvestorWithdrawal[]>([])
  const [coin, setCoin] = useState<string | null>(null)
  const [form, setForm] = useState({ amount: '', destination: '' })
  const [reviewing, setReviewing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

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
    try {
      // The wallet's coin is what the admin pays out in. Read-only, the same
      // endpoint the Deposit page uses; 404 means no wallet yet.
      setCoin((await orgApi<InvestorWallet>(orgId, 'investor/wallet')).coin)
    } catch {
      setCoin(null)
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  // Withdrawal rows carry no coin (investor.py _withdrawal_json), so the
  // unit is the wallet's coin, else the account-currency default.
  const unit = coin ?? ACCOUNT_CURRENCY
  const available = summary?.available ?? null

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setNotice(null)
    // Digits with at most two decimals, so the review shows exactly the
    // number that will be posted ("1000.005" would read as 1,000.01).
    if (!/^\d+(\.\d{1,2})?$/.test(form.amount.trim())) {
      setError('Enter an amount with at most two decimals, digits only (for example 250.00).')
      return
    }
    if (!(Number(form.amount) > 0)) {
      setError('Enter an amount above zero')
      return
    }
    setReviewing(true)
  }

  const send = async () => {
    setBusy(true)
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
      setReviewing(false)
    }
  }

  const linked = summary?.link_state === 'linked'

  return (
    <div className="space-y-6 max-w-4xl">
      <PageHeader
        title="Withdraw"
        subtitle="Ask for an amount and where to send it. An admin approves, pays from the workspace wallet, and records the transaction."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      {summary && !linked && (
        <NextStep title="Your account is being set up">
          Withdrawals open once your admin links your trading account.
        </NextStep>
      )}

      {summary && linked && (
        <Card title="Available to withdraw"
              actions={<span className="num text-2xl font-semibold text-ink">{moneyOrDash(available, ACCOUNT_CURRENCY)}</span>}>
          <form onSubmit={review} className="space-y-4">
            {available == null && (
              <p className="text-xs text-warn-deep">
                Your account is offline right now, so the available figure is unknown; an admin will check it.
              </p>
            )}
            <div className="flex gap-3 flex-wrap items-end">
              <label className="block w-40">
                <span className="desk-label block mb-1">Amount ({unit})</span>
                <Input aria-label={`Amount in ${unit}`} num value={form.amount} required
                       onChange={(e) => setForm({ ...form, amount: e.target.value })} />
              </label>
              <Button variant="ghost" size="sm" disabled={available == null || available <= 0}
                      onClick={() => { if (available != null) setForm({ ...form, amount: available.toFixed(2) }) }}>
                Use max
              </Button>
              <label className="block flex-1 min-w-56">
                <span className="desk-label block mb-1">Destination address</span>
                <Input aria-label="Destination address" num value={form.destination} required
                       onChange={(e) => setForm({ ...form, destination: e.target.value })} />
              </label>
            </div>
            <Button type="submit" disabled={busy}>
              Request withdrawal
            </Button>
          </form>
        </Card>
      )}

      <ConfirmDialog
        open={reviewing}
        title={`Send ${money(form.amount, unit)} to ${shortAddress(form.destination)}?`}
        confirmLabel="Send request"
        busy={busy}
        onConfirm={send}
        onCancel={() => setReviewing(false)}
      >
        <p>An admin reviews the request, pays it from the workspace wallet and records the transaction.</p>
        <div>
          <div className="desk-label">Amount</div>
          <div className="num text-ink">{money(form.amount, unit)}</div>
        </div>
        <div>
          <div className="desk-label">Destination</div>
          <div className="num text-ink break-all">{form.destination}</div>
        </div>
        <p>Check every character: a payment sent to a wrong address cannot be recalled.</p>
      </ConfirmDialog>

      <Card title="Your requests" inset>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No requests yet</li>}
          {rows.map((w) => (
            <li key={w.id} className="px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(w.created_at)}</span>
                <span className="tnum font-semibold text-ink">{money(w.amount, unit)}</span>
                <span className="num text-ink-soft break-all min-w-0">to {w.destination}</span>
              </div>
              <Timeline w={w} />
              {w.decision_note && <p className="text-xs text-ink-soft">Admin: {w.decision_note}</p>}
              {w.txid && <p className="num text-xs text-ink-soft break-all">Transaction: {w.txid}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
