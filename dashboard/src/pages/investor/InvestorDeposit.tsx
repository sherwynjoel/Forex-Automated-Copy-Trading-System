import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, BADGE_TONE, accountName, statusLabel, statusTone } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import Tabs from '../../components/Tabs'
import NextStep from './NextStep'
import type { InvestorSummary, PaymentMethod, PortalDeposit, UploadedFile } from '../../lib/types'

const COPIED_MS = 2000
const AMOUNT_RE = /^\d+(\.\d{1,2})?$/
const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'
const QUICK = [50, 100, 250, 500]
const KIND_LABEL = { crypto: 'Crypto', bank: 'Bank' } as const
type Kind = PaymentMethod['kind']
type Target = PortalDeposit['target']

// Bank details in the order the reference portal shows them; a key the
// admin left blank is skipped.
const BANK_FIELDS: { key: string; label: string }[] = [
  { key: 'bank_name', label: 'Bank name' },
  { key: 'holder', label: 'Account holder' },
  { key: 'account_number', label: 'Account number' },
  { key: 'code', label: 'SWIFT / IFSC code' },
  { key: 'bank_address', label: 'Bank address' },
  { key: 'country', label: 'Country' },
]

// `account` is the picked trading account's id as a string; '' = the first.
const EMPTY_FORM = { amount: '', reference: '', note: '', target: 'wallet' as Target, account: '' }

type Copied = { key: string; label: string; how: 'copied' | 'selected' }

export default function InvestorDeposit() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [deposits, setDeposits] = useState<PortalDeposit[]>([])
  const [kind, setKind] = useState<Kind>('crypto')
  const [methodId, setMethodId] = useState<number | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [receipt, setReceipt] = useState<File | null>(null)
  const [receiptError, setReceiptError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [copied, setCopied] = useState<Copied | null>(null)
  const [cancelling, setCancelling] = useState<PortalDeposit | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  const valueRefs = useRef<Record<string, HTMLElement | null>>({})

  const refresh = useCallback(async () => {
    try {
      const [s, m, d] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<PaymentMethod[]>(orgId, 'investor/payment-methods'),
        orgApi<PortalDeposit[]>(orgId, 'investor/deposits'),
      ])
      setSummary(s); setMethods(m); setDeposits(d)
    } catch (err) {
      setError(errorText(err, 'Could not load your deposits'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const kinds = (['crypto', 'bank'] as Kind[]).filter((k) => methods.some((m) => m.kind === k))
  const activeKind: Kind | null = kinds.includes(kind) ? kind : (kinds[0] ?? null)
  const ofKind = methods.filter((m) => m.kind === activeKind)
  const selected = ofKind.find((m) => m.id === methodId) ?? ofKind[0] ?? null
  const address = selected?.kind === 'crypto' ? selected.details.address : null
  const accounts = summary?.accounts ?? []
  const linked = accounts.length > 0
  const accountId = form.account ? Number(form.account) : (accounts[0]?.account_id ?? null)
  const unit = selected?.currency ?? summary?.currency ?? ACCOUNT_CURRENCY
  const refLabel = selected?.kind === 'bank' ? 'Bank transaction ID' : 'Transaction hash'

  useEffect(() => {
    let alive = true
    if (!address) { setQr(null); return }
    void (async () => {
      try {
        // Loaded on demand so the QR encoder stays out of the main bundle.
        const { toDataURL } = await import('qrcode')
        const url = await toDataURL(address, { width: 192, margin: 1 })
        if (alive) setQr(url)
      } catch {
        // The address and the Copy button still work without the picture.
        if (alive) setQr(null)
      }
    })()
    return () => { alive = false }
  }, [address])

  useEffect(() => {
    if (copied?.how !== 'copied') return
    const id = window.setTimeout(() => setCopied(null), COPIED_MS)
    return () => window.clearTimeout(id)
  }, [copied])

  // Fallback when there is no clipboard API (plain http, some in-app
  // browsers) or the browser refuses it: select the value so the
  // investor's own copy command takes it.
  const selectValue = (key: string, label: string) => {
    const node = valueRefs.current[key]
    const selection = window.getSelection()
    if (!node || !selection) return
    const range = document.createRange()
    range.selectNodeContents(node)
    selection.removeAllRanges()
    selection.addRange(range)
    setCopied({ key, label, how: 'selected' })
  }

  const copyValue = async (key: string, label: string, text: string) => {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        setCopied({ key, label, how: 'copied' })
        return
      } catch {
        // Permission refused: fall through to selecting the text.
      }
    }
    selectValue(key, label)
  }

  // Switching kind or method drops any validation message the previous
  // choice earned (a bank-only "receipt required" would otherwise survive
  // onto an optional receipt on the next method); a success notice from a
  // completed submission is left alone.
  const chooseKind = (k: string) => {
    setKind(k as Kind); setMethodId(null); setCopied(null); setReceiptError(null); setError(null)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selected) return
    setError(null); setNotice(null); setReceiptError(null)
    // Digits with at most two decimals, so the row shows exactly the number
    // that was posted ("1000.005" would read as 1,000.01).
    if (!AMOUNT_RE.test(form.amount.trim())) { setError(TWO_DECIMALS); return }
    if (!(Number(form.amount) > 0)) { setError('Enter an amount above zero'); return }
    if (!form.reference.trim()) { setError(`${refLabel} is required`); return }
    if (selected.kind === 'bank' && !receipt) { setReceiptError('A receipt is required for bank deposits'); return }
    setBusy(true)
    try {
      let receipt_file_id: number | null = null
      if (receipt) {
        const fd = new FormData()
        fd.append('purpose', 'deposit_receipt')
        fd.append('file', receipt)
        receipt_file_id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      }
      await orgApi<PortalDeposit>(orgId, 'investor/deposits', {
        method: 'POST',
        body: JSON.stringify({
          method_id: selected.id,
          amount: form.amount.trim(),
          reference: form.reference.trim(),
          receipt_file_id,
          target: form.target,
          target_account_id: form.target === 'account' ? accountId : null,
          note: form.note.trim() || null,
        }),
      })
      setForm(EMPTY_FORM)
      setReceipt(null)
      setNotice('Notice filed. An admin will confirm it once the transfer is seen.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not file the notice'))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!cancelling) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PortalDeposit>(orgId, `investor/deposits/${cancelling.id}/cancel`, { method: 'POST' })
      setNotice('Notice cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the notice'))
    } finally {
      setBusy(false)
      setCancelling(null)
    }
  }

  const copyStatus = !copied ? ''
    : copied.how === 'copied' ? `${copied.label} copied to the clipboard.`
    : `${copied.label} selected. Copy it with Ctrl+C, or long-press on a phone.`

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Deposit"
        subtitle="Send the money to one of the workspace's payment methods, then file a notice so an admin can confirm it."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={4} />}

      {summary && !summary.deposits_open && (
        <NextStep title="Deposits are not open yet">
          Your admin has not added a payment method yet. Deposits open as soon as one is enabled; you can save a payout account meanwhile.
        </NextStep>
      )}

      {activeKind && selected && (
        <>
          <Card title="Choose a payment method">
            <Tabs items={kinds.map((k) => ({ key: k, label: KIND_LABEL[k] }))} value={activeKind}
                  onChange={chooseKind} label="Payment method kind" idBase="deposit-kind" />
            <div id="deposit-kind-panel" role="tabpanel" aria-labelledby={`deposit-kind-tab-${activeKind}`}
                 className="mt-4 grid gap-4 md:grid-cols-[240px_1fr]">
              <ul className="space-y-2" aria-label="Payment methods">
                {ofKind.map((m) => {
                  const on = m.id === selected.id
                  return (
                    <li key={m.id}>
                      <Button variant={on ? 'primary' : 'secondary'} block aria-pressed={on}
                              className="justify-start text-left"
                              onClick={() => { setMethodId(m.id); setCopied(null); setReceiptError(null); setError(null) }}>
                        <span className="flex flex-col items-start">
                          <span>{m.label}</span>
                          <span className="text-xs font-normal">
                            {m.min_amount > 0 ? `Min ${money(m.min_amount, m.currency)}` : 'No minimum'}
                            {' · '}
                            {m.fee_pct > 0 ? `Fee ${m.fee_pct}%` : 'No fee'}
                          </span>
                        </span>
                      </Button>
                    </li>
                  )
                })}
              </ul>

              <div className="inset p-4 space-y-3 min-w-0">
                {selected.kind === 'crypto' ? (
                  <div className="grid gap-5 sm:grid-cols-[192px_1fr]">
                    {qr && (
                      <img src={qr} alt="QR code of the deposit address" width={192} height={192}
                           className="rounded-inset border border-line bg-card" />
                    )}
                    <div className="space-y-3 min-w-0">
                      <div>
                        <div className="desk-label">Send only</div>
                        <div className="text-lg font-semibold text-ink">
                          {selected.details.coin} on {selected.details.network}
                        </div>
                      </div>
                      <div>
                        <div className="desk-label">Address</div>
                        <div className="flex flex-wrap items-start gap-3">
                          <div ref={(n) => { valueRefs.current.address = n }}
                               className="num text-sm text-ink break-all min-w-0 flex-1">
                            {selected.details.address}
                          </div>
                          <Button variant="secondary" size="sm"
                                  onClick={() => copyValue('address', 'Address', selected.details.address)}>
                            {copied?.key === 'address' && copied.how === 'copied' ? 'Copied' : 'Copy address'}
                          </Button>
                        </div>
                      </div>
                      {selected.details.memo && (
                        <div>
                          <div className="desk-label">Memo / tag</div>
                          <div className="num text-sm text-ink">{selected.details.memo}</div>
                        </div>
                      )}
                      <Banner kind="warn" announce={false}>
                        Sending any other coin or network to this address will lose the funds.
                      </Banner>
                    </div>
                  </div>
                ) : (
                  <dl className="grid gap-3 sm:grid-cols-2">
                    {BANK_FIELDS.filter((f) => selected.details[f.key]).map((f) => (
                      <div key={f.key} className="min-w-0">
                        <dt className="desk-label">{f.label}</dt>
                        <dd className="flex flex-wrap items-start gap-2">
                          <span ref={(n) => { valueRefs.current[f.key] = n }}
                                className="num text-sm text-ink break-all min-w-0 flex-1">
                            {selected.details[f.key]}
                          </span>
                          <Button variant="secondary" size="sm" aria-label={`Copy ${f.label}`}
                                  onClick={() => copyValue(f.key, f.label, selected.details[f.key])}>
                            {copied?.key === f.key && copied.how === 'copied' ? 'Copied' : 'Copy'}
                          </Button>
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
                {selected.instructions && <p className="text-sm text-ink-soft">{selected.instructions}</p>}
                {/* Always mounted so screen readers hear the change. */}
                <p role="status" className="text-xs text-ink-soft min-h-4">{copyStatus}</p>
              </div>
            </div>
          </Card>

          <Card title="File a deposit notice">
            <form onSubmit={submit} noValidate className="space-y-4">
              <div className="flex gap-3 flex-wrap items-end">
                <label className="block w-40">
                  <span className="desk-label block mb-1">Amount ({unit})</span>
                  <Input aria-label={`Amount in ${unit}`} num value={form.amount}
                         onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                </label>
                <div className="flex flex-wrap gap-1" aria-label="Quick amounts">
                  {QUICK.map((n) => (
                    <Button key={n} variant="ghost" size="sm" onClick={() => setForm({ ...form, amount: String(n) })}>
                      {n}
                    </Button>
                  ))}
                  <Button variant="ghost" size="sm"
                          onClick={() => setForm({ ...form, amount: selected.min_amount.toFixed(2) })}>
                    Min
                  </Button>
                </div>
              </div>
              <label className="block">
                <span className="desk-label block mb-1">{refLabel}</span>
                <Input aria-label={refLabel} num value={form.reference}
                       onChange={(e) => setForm({ ...form, reference: e.target.value })} />
              </label>
              <FileInput id="receipt" label={selected.kind === 'bank' ? 'Receipt' : 'Receipt (optional)'}
                         accept={RECEIPT_ACCEPT} maxBytes={MAX_UPLOAD_BYTES} value={receipt}
                         onChange={(f) => { setReceipt(f); setReceiptError(null) }}
                         required={selected.kind === 'bank'} hint="JPEG, PNG, WebP or PDF, up to 5 MB"
                         error={receiptError} disabled={busy} />
              <fieldset>
                <legend className="desk-label mb-1">Deposit to</legend>
                <div className="flex flex-wrap gap-4 text-sm">
                  <label className="flex items-center gap-2 text-ink">
                    <input type="radio" name="target" value="wallet" className="accent-brand"
                           checked={form.target === 'wallet'} onChange={() => setForm({ ...form, target: 'wallet' })} />
                    My wallet
                  </label>
                  {linked && (
                    <label className="flex items-center gap-2 text-ink">
                      <input type="radio" name="target" value="account" className="accent-brand"
                             checked={form.target === 'account'} onChange={() => setForm({ ...form, target: 'account' })} />
                      Trading account
                    </label>
                  )}
                </div>
              </fieldset>
              {form.target === 'account' && accounts.length > 1 && (
                <label className="block">
                  <span className="desk-label block mb-1">Which trading account</span>
                  <Select aria-label="Which trading account" block value={accountId ?? ''}
                          onChange={(e) => setForm({ ...form, account: e.target.value })}>
                    {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{accountName(a)}</option>)}
                  </Select>
                </label>
              )}
              <label className="block">
                <span className="desk-label block mb-1">Note (optional)</span>
                <Input aria-label="Note" value={form.note}
                       onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </label>
              <Button type="submit" disabled={busy}>File deposit notice</Button>
            </form>
          </Card>
        </>
      )}

      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel deposit notice #${cancelling?.id ?? ''}?`}
        confirmLabel="Yes, cancel it"
        danger
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      >
        <p>The notice is withdrawn and the admin will not confirm it. Money you already sent stays where it is; file a new notice if it arrives.</p>
      </ConfirmDialog>

      <Card title="Your deposit notices" inset>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-4 py-2 font-semibold">Filed</th>
                <th className="desk-label px-4 py-2 font-semibold">Method</th>
                <th className="desk-label px-4 py-2 font-semibold text-right">Amount</th>
                <th className="desk-label px-4 py-2 font-semibold text-right">Credited</th>
                <th className="desk-label px-4 py-2 font-semibold">Reference</th>
                <th className="desk-label px-4 py-2 font-semibold">Receipt</th>
                <th className="desk-label px-4 py-2 font-semibold">Status</th>
                <th className="px-4 py-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {deposits.length === 0 && (
                <tr><td colSpan={8} className="text-center py-8 text-ink-faint">No notices yet</td></tr>
              )}
              {deposits.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0">
                  <td data-label="Filed" className="num px-4 py-2.5">{formatWhen(d.created_at)}</td>
                  <td data-label="Method" className="px-4 py-2.5 text-ink">{d.method_label}</td>
                  <td data-label="Amount" className="px-4 py-2.5 text-right">
                    <Money value={d.amount} unit={d.currency} />
                  </td>
                  <td data-label="Credited" className="px-4 py-2.5 text-right">
                    <Money value={d.credited_amount} unit={d.currency} />
                  </td>
                  <td data-label="Reference" className="num px-4 py-2.5 break-all">{d.reference}</td>
                  <td data-label="Receipt" className="px-4 py-2.5">
                    {d.receipt_file_id != null ? (
                      <a href={`/api/orgs/${orgId}/investor/files/${d.receipt_file_id}`} target="_blank" rel="noopener"
                         className="text-brand underline underline-offset-2 hover:text-brand-deep">
                        Open
                      </a>
                    ) : '—'}
                  </td>
                  <td data-label="Status" className="px-4 py-2.5">
                    <Badge tone={BADGE_TONE[statusTone(d.status)]}>{statusLabel(d.status)}</Badge>
                    {d.decision_note && <div className="text-xs text-ink-soft mt-1">Admin: {d.decision_note}</div>}
                  </td>
                  <td data-label="Actions" className="px-4 py-2.5 text-right">
                    {d.status === 'pending' && (
                      <Button variant="ghost" tone="loss" size="sm" aria-label={`Cancel deposit ${d.id}`}
                              onClick={() => setCancelling(d)} disabled={busy}>
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
