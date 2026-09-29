import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, BADGE_TONE, statusLabel, statusTone } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { InvestorSummary, PayoutDestination, PortalWithdrawal } from '../../lib/types'

const STEPS = ['requested', 'approved', 'paid'] as const
type Step = typeof STEPS[number]
// The withdrawal timeline's own words: "approved" here means the admin has
// agreed and the payment is still to be made (a transfer's "approved" reads
// differently, see InvestorTransfer).
const STEP_LABELS: Record<Step, string> = {
  requested: 'Awaiting approval', approved: 'Approved, payment pending', paid: 'Paid',
}
const AMOUNT_RE = /^\d+(\.\d{1,2})?$/
const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'

/** Fee and net for the typed amount, matching the server's fee_for exactly:
 *  half-up to the cent, computed with exact integer (BigInt) arithmetic so
 *  it never drifts the way floating point does (1.15% of 250.00 must read
 *  2.88, not the 2.87 that `Math.round(cents * feePct / 100)` gives when
 *  `cents * feePct` lands a hair under its true value, e.g. 28749.999999999996
 *  instead of 28750). `feePct` carries at most three decimals
 *  (withdrawal_fee_pct is NUMERIC(6,3)), so scaling it by 1000 is exact; the
 *  fee is then an exact integer division rounded half-up. Null while the
 *  amount is not a valid figure. The server's figures rule; this only
 *  previews them. */
export function feePreview(amount: string, feePct: number): { fee: number; net: number } | null {
  if (!AMOUNT_RE.test(amount.trim())) return null
  const cents = Math.round(Number(amount) * 100)
  const milli = Math.round(feePct * 1000)
  const feeCents = Number((BigInt(cents) * BigInt(milli) + 50_000n) / 100_000n)
  return { fee: feeCents / 100, net: (cents - feeCents) / 100 }
}

function Timeline({ w }: { w: PortalWithdrawal }) {
  const reached = STEPS.indexOf(w.status as Step)
  if (reached === -1) {
    return <Badge tone={BADGE_TONE[statusTone(w.status)]}>{statusLabel(w.status)}</Badge>
  }
  return (
    <ol aria-label="Withdrawal progress" className="flex flex-wrap items-center gap-2 text-xs">
      {STEPS.map((step, i) => {
        const current = i === reached
        return (
          <li key={step} aria-current={current ? 'step' : undefined} className={current ? 'font-semibold' : ''}>
            <Badge tone={i <= reached ? BADGE_TONE[statusTone(step)] : 'neutral'}>
              {STEP_LABELS[step]}
              {/* Colour is never the only signal: the current step says so.
                  Its own element, so the Badge's direct text stays the bare
                  label and getByText('Approved, payment pending') matches. */}
              {current && <span> · current</span>}
            </Badge>
          </li>
        )
      })}
    </ol>
  )
}

export default function InvestorWithdraw() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [destinations, setDestinations] = useState<PayoutDestination[]>([])
  const [rows, setRows] = useState<PortalWithdrawal[]>([])
  const [form, setForm] = useState({ amount: '', destination_id: '' })
  const [reviewing, setReviewing] = useState(false)
  const [cancelling, setCancelling] = useState<PortalWithdrawal | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, d, list] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<PayoutDestination[]>(orgId, 'investor/payout-destinations'),
        orgApi<PortalWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      setSummary(s); setDestinations(d); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your withdrawals'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const approved = destinations.filter((d) => d.status === 'approved')
  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  // Already floored to cents by the server, so "Use max" is never refused.
  const available = summary?.wallets.main.available ?? 0
  const onHold = summary?.wallets.main.on_hold ?? 0
  const rules = summary?.withdrawal_rules ?? { min: 0, fee_pct: 0 }
  const destination = approved.find((d) => String(d.id) === form.destination_id) ?? approved[0] ?? null
  const preview = feePreview(form.amount, rules.fee_pct)

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setNotice(null)
    // Digits with at most two decimals, so the review shows exactly the
    // number that will be posted ("1000.005" would read as 1,000.01).
    if (!AMOUNT_RE.test(form.amount.trim())) { setError(TWO_DECIMALS); return }
    if (!(Number(form.amount) > 0)) { setError('Enter an amount above zero'); return }
    if (!destination) { setError('Add a payout account first'); return }
    setReviewing(true)
  }

  // Rejections propagate: PinConfirmDialog shows 401/423/409 and any other
  // refusal inline and clears the PIN; the dialog closes only on success.
  const send = async (mpin: string) => {
    if (!destination) return
    setBusy(true)
    try {
      await orgApi<PortalWithdrawal>(orgId, 'investor/withdrawals', {
        method: 'POST',
        body: JSON.stringify({ destination_id: destination.id, amount: form.amount.trim(), mpin }),
      }, { redirectOn401: false })
      setReviewing(false)
      setForm({ amount: '', destination_id: form.destination_id })
      setNotice('Request sent. You will be emailed when an admin decides.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!cancelling) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PortalWithdrawal>(orgId, `investor/withdrawals/${cancelling.id}/cancel`, { method: 'POST' })
      setNotice('Request cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the request'))
    } finally {
      setBusy(false)
      setCancelling(null)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Withdraw"
        subtitle="Ask for an amount from your wallet and where to send it. An admin approves, pays to your payout account, and records the transaction."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      {summary && (
        <Card title="Available to withdraw"
              actions={<span className="text-2xl font-semibold text-ink"><Money value={available} unit={unit} /></span>}>
          <div className="space-y-4">
            {onHold > 0 && (
              <p className="text-xs text-ink-soft">
                <Money value={onHold} unit={unit} /> is on hold for open requests.
              </p>
            )}
            {approved.length === 0 ? (
              <Banner kind="notice" announce={false}>
                <span>Add a payout account first; an admin approves it before it can be used.</span>{' '}
                <Button variant="ghost" size="sm" to={`${base}/payout-accounts`}>Add a payout account</Button>
              </Banner>
            ) : (
              <form onSubmit={review} noValidate className="space-y-4">
                <label className="block">
                  <span className="desk-label block mb-1">Payout account</span>
                  <Select aria-label="Payout account" block value={destination ? String(destination.id) : ''}
                          onChange={(e) => setForm({ ...form, destination_id: e.target.value })}>
                    {approved.map((d) => (
                      <option key={d.id} value={d.id}>{`${d.nickname} · ${d.summary}`}</option>
                    ))}
                  </Select>
                </label>
                <div className="flex gap-3 flex-wrap items-end">
                  <label className="block w-40">
                    <span className="desk-label block mb-1">Amount ({unit})</span>
                    <Input aria-label={`Amount in ${unit}`} num value={form.amount}
                           onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                  </label>
                  <Button variant="ghost" size="sm" disabled={available <= 0}
                          onClick={() => setForm({ ...form, amount: available.toFixed(2) })}>
                    Use max
                  </Button>
                </div>
                <p className="text-xs text-ink-soft">
                  {rules.min > 0 && <>Minimum {money(rules.min, unit)} · </>}
                  {preview
                    ? <>Fee {money(preview.fee, unit)} ({rules.fee_pct}%) · You receive {money(preview.net, unit)}</>
                    : <>Fee {rules.fee_pct}% of the amount</>}
                </p>
                <Button type="submit" disabled={busy}>Request withdrawal</Button>
              </form>
            )}
          </div>
        </Card>
      )}

      <PinConfirmDialog
        open={reviewing}
        title={`Send ${money(form.amount, unit)} to ${destination?.summary ?? ''}?`}
        confirmLabel="Send request"
        busy={busy}
        onConfirm={send}
        onCancel={() => setReviewing(false)}
      >
        <p>An admin reviews the request, pays it to your payout account and records the transaction.</p>
        <dl className="grid grid-cols-2 gap-2">
          <div><dt className="desk-label">Amount</dt><dd className="num text-ink">{money(form.amount, unit)}</dd></div>
          <div><dt className="desk-label">Fee</dt><dd className="num text-ink">{money(preview?.fee ?? 0, unit)}</dd></div>
          <div><dt className="desk-label">You receive</dt><dd className="num text-ink">{money(preview?.net ?? 0, unit)}</dd></div>
          <div><dt className="desk-label">Payout account</dt>
            <dd className="text-ink">{destination ? `${destination.nickname} · ${destination.summary}` : ''}</dd></div>
        </dl>
      </PinConfirmDialog>

      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel withdrawal #${cancelling?.id ?? ''}?`}
        confirmLabel="Yes, cancel it"
        danger
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      >
        <p>The amount on hold returns to your available balance and no payment is made.</p>
      </ConfirmDialog>

      <Card title="Your requests" inset>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No requests yet</li>}
          {rows.map((w) => (
            <li key={w.id} className="px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(w.created_at)}</span>
                <span className="font-semibold text-ink"><Money value={w.amount} unit={w.currency} /></span>
                <span className="num text-ink-soft min-w-0">to {w.destination_summary}</span>
                {w.status === 'requested' && (
                  <Button variant="ghost" tone="loss" size="sm" aria-label={`Cancel withdrawal ${w.id}`}
                          onClick={() => setCancelling(w)} disabled={busy}>
                    Cancel
                  </Button>
                )}
              </div>
              <p className="text-xs text-ink-soft">Fee <Money value={w.fee} unit={w.currency} /> · Net <Money value={w.net_amount} unit={w.currency} /></p>
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
