import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, BADGE_TONE, statusLabel, statusTone, walletLabel } from '../../lib/investor'
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
import type { InvestorSummary, MoneyRef, PortalTransfer, WalletKind } from '../../lib/types'

const AMOUNT_RE = /^\d+(\.\d{1,2})?$/
const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'
// The pairs phase 1 allows (spec section 7); "account" is the linked
// trading account, the credit wallet never moves.
const PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['main', 'account'], ['account', 'main'], ['pamm', 'main'], ['social', 'main'],
]

export interface TransferOption {
  /** `wallet:<kind>` or `account:<id>`: the Select's option value. */
  value: string
  label: string
  ref: MoneyRef
  available: number | null
}

/** The side of a pair an option value stands for. */
function sideOf(value: string): string {
  return value.startsWith('account:') ? 'account' : value.slice('wallet:'.length)
}

export function pairAllowed(from: string, to: string): boolean {
  const a = sideOf(from)
  const b = sideOf(to)
  return PAIRS.some(([x, y]) => x === a && y === b)
}

export function transferOptions(s: InvestorSummary): TransferOption[] {
  const wallets: WalletKind[] = ['main', 'pamm', 'social']
  const opts: TransferOption[] = wallets.map((w) => ({
    value: `wallet:${w}`, label: walletLabel(w), ref: { kind: 'wallet', wallet: w },
    available: s.wallets[w].available,
  }))
  if (s.link_state === 'linked' && s.account) {
    opts.push({
      value: `account:${s.account.account_id}`, label: 'Trading account',
      ref: { kind: 'account', account_id: s.account.account_id }, available: s.account_available,
    })
  }
  return opts
}

function refLabel(r: MoneyRef): string {
  return r.kind === 'wallet' && r.wallet ? walletLabel(r.wallet) : 'Trading account'
}

export default function InvestorTransfer() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<PortalTransfer[]>([])
  const [form, setForm] = useState({ from: 'wallet:main', to: '', amount: '' })
  const [reviewing, setReviewing] = useState(false)
  const [cancelling, setCancelling] = useState<PortalTransfer | null>(null)
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
        orgApi<PortalTransfer[]>(orgId, 'investor/transfers'),
      ])
      setSummary(s); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your transfers'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  const options = summary ? transferOptions(summary) : []
  const fromOpt = options.find((o) => o.value === form.from) ?? options[0] ?? null
  // The To list follows the pair rules; a stale choice falls back to the
  // first allowed target, so switching From never leaves an illegal pair.
  const targets = fromOpt ? options.filter((o) => pairAllowed(fromOpt.value, o.value)) : []
  const toOpt = targets.find((o) => o.value === form.to) ?? targets[0] ?? null
  const available = fromOpt?.available ?? null

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setNotice(null)
    if (!AMOUNT_RE.test(form.amount.trim())) { setError(TWO_DECIMALS); return }
    if (!(Number(form.amount) > 0)) { setError('Enter an amount above zero'); return }
    if (!fromOpt || !toOpt) return
    setReviewing(true)
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const send = async (mpin: string) => {
    if (!fromOpt || !toOpt) return
    setBusy(true)
    try {
      const t = await orgApi<PortalTransfer>(orgId, 'investor/transfers', {
        method: 'POST',
        body: JSON.stringify({ source: fromOpt.ref, target: toOpt.ref, amount: form.amount.trim(), mpin }),
      }, { redirectOn401: false })
      setReviewing(false)
      setForm({ ...form, amount: '' })
      setNotice(t.status === 'done'
        ? 'Transfer done. Your wallets are updated.'
        : 'Request sent. An admin moves the money and marks it done.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!cancelling) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PortalTransfer>(orgId, `investor/transfers/${cancelling.id}/cancel`, { method: 'POST' })
      setNotice('Transfer cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the transfer'))
    } finally {
      setBusy(false)
      setCancelling(null)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Transfer"
        subtitle="Move money between your wallets and your trading account. Wallet-to-wallet moves complete at once; moves to or from the trading account are done by an admin."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      {summary && fromOpt && (
        <Card title="New transfer">
          <form onSubmit={review} noValidate className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="desk-label block mb-1">From</span>
                <Select aria-label="From" block value={fromOpt.value}
                        onChange={(e) => setForm({ ...form, from: e.target.value, to: '' })}>
                  {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
                <span className="block mt-1 text-xs text-ink-soft">
                  {available == null
                    ? 'Available: unknown while the account is offline; an admin will check it.'
                    : <>Available: <Money value={available} unit={unit} /></>}
                </span>
              </label>
              <label className="block">
                <span className="desk-label block mb-1">To</span>
                <Select aria-label="To" block value={toOpt?.value ?? ''} disabled={targets.length === 0}
                        onChange={(e) => setForm({ ...form, to: e.target.value })}>
                  {targets.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
                {targets.length === 0 && (
                  <span className="block mt-1 text-xs text-warn-deep">
                    Link a trading account to move wallet money into it; PAMM and Social wallets can still move to My wallet.
                  </span>
                )}
              </label>
            </div>
            <div className="flex gap-3 flex-wrap items-end">
              <label className="block w-40">
                <span className="desk-label block mb-1">Amount ({unit})</span>
                <Input aria-label={`Amount in ${unit}`} num value={form.amount}
                       onChange={(e) => setForm({ ...form, amount: e.target.value })} />
              </label>
              <Button variant="ghost" size="sm" disabled={available == null || available <= 0}
                      onClick={() => { if (available != null) setForm({ ...form, amount: available.toFixed(2) }) }}>
                Use max
              </Button>
            </div>
            <Button type="submit" disabled={busy || !toOpt}>Request transfer</Button>
          </form>
        </Card>
      )}

      <PinConfirmDialog
        open={reviewing}
        title={`Move ${money(form.amount, unit)} from ${fromOpt?.label ?? ''} to ${toOpt?.label ?? ''}?`}
        confirmLabel="Confirm transfer"
        busy={busy}
        onConfirm={send}
        onCancel={() => setReviewing(false)}
      >
        <p>
          {toOpt?.ref.kind === 'wallet' && fromOpt?.ref.kind === 'wallet'
            ? 'This completes at once.'
            : 'The amount is held until an admin moves it at the broker and marks the transfer done.'}
        </p>
        <dl className="grid grid-cols-2 gap-2">
          <div><dt className="desk-label">From</dt><dd className="text-ink">{fromOpt?.label}</dd></div>
          <div><dt className="desk-label">To</dt><dd className="text-ink">{toOpt?.label}</dd></div>
          <div><dt className="desk-label">Amount</dt><dd className="num text-ink">{money(form.amount, unit)}</dd></div>
        </dl>
      </PinConfirmDialog>

      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel transfer #${cancelling?.id ?? ''}?`}
        confirmLabel="Yes, cancel it"
        danger
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      >
        <p>The amount on hold returns to its wallet and nothing is moved.</p>
      </ConfirmDialog>

      <Card title="Your transfers" inset>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No transfers yet</li>}
          {rows.map((t) => (
            <li key={t.id} className="px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(t.created_at)}</span>
                <span className="text-ink">{`${refLabel(t.source)} → ${refLabel(t.target)}`}</span>
                <span className="font-semibold text-ink"><Money value={t.amount} unit={t.currency} /></span>
                <Badge tone={BADGE_TONE[statusTone(t.status, 'transfer')]}>{statusLabel(t.status, 'transfer')}</Badge>
                {t.status === 'requested' && (
                  <Button variant="ghost" tone="loss" size="sm" aria-label={`Cancel transfer ${t.id}`}
                          onClick={() => setCancelling(t)} disabled={busy}>
                    Cancel
                  </Button>
                )}
              </div>
              {t.note && <p className="text-xs text-ink-soft">{t.note}</p>}
              {t.decision_note && <p className="text-xs text-ink-soft">Admin: {t.decision_note}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
