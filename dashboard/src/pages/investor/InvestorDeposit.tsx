import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { statusLabel, statusTone } from '../../lib/investor'
import Badge, { type BadgeTone } from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import NextStep from './NextStep'
import type { InvestorDeposit as Deposit, InvestorWallet } from '../../lib/types'

// statusTone's four states, mapped onto the desk's one chip.
const BADGE_TONE: Record<ReturnType<typeof statusTone>, BadgeTone> = {
  ok: 'profit', warn: 'warn', bad: 'loss', quiet: 'neutral',
}

const COPIED_MS = 2000

export default function InvestorDeposit() {
  const { orgId } = useOrg()
  const [wallet, setWallet] = useState<InvestorWallet | null | 'closed'>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [form, setForm] = useState({ amount: '', txid: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [copy, setCopy] = useState<'idle' | 'copied' | 'selected'>('idle')
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  const addressRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      setDeposits(await orgApi<Deposit[]>(orgId, 'investor/deposits'))
    } catch (err) {
      setError(errorText(err, 'Could not load your deposits'))
    }
    let w: InvestorWallet | null = null
    try {
      w = await orgApi<InvestorWallet>(orgId, 'investor/wallet')
      setWallet(w)
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('404')) setWallet('closed')
      else setError(errorText(err, 'Could not load the deposit address'))
    }
    setLoaded(true)
    if (!w) return
    try {
      // Loaded on demand so the QR encoder stays out of the main bundle.
      const { toDataURL } = await import('qrcode')
      setQr(await toDataURL(w.address, { width: 192, margin: 1 }))
    } catch {
      // The address and the Copy button still work without the picture.
      setQr(null)
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (copy !== 'copied') return
    const id = window.setTimeout(() => setCopy('idle'), COPIED_MS)
    return () => window.clearTimeout(id)
  }, [copy])

  // Fallback when there is no clipboard API (plain http, some in-app
  // browsers) or the browser refuses it: select the address so the
  // investor's own copy command takes it.
  const selectAddress = () => {
    const node = addressRef.current
    const selection = window.getSelection()
    if (!node || !selection) return
    const range = document.createRange()
    range.selectNodeContents(node)
    selection.removeAllRanges()
    selection.addRange(range)
    setCopy('selected')
  }

  const copyAddress = async (address: string) => {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(address)
        setCopy('copied')
        return
      } catch {
        // Permission refused: fall through to selecting the text.
      }
    }
    selectAddress()
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (wallet === null || wallet === 'closed') return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<Deposit>(orgId, 'investor/deposits', {
        method: 'POST',
        body: JSON.stringify({ amount: form.amount, coin: wallet.coin, txid: form.txid,
                               note: form.note }),
      })
      setForm({ amount: '', txid: '', note: '' })
      setNotice('Notice filed. An admin will confirm it once the transfer is seen.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not file the notice'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <PageHeader
        title="Deposit"
        subtitle="Send crypto to the address below, then tell us the amount and the transaction ID."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {!loaded && <Loading lines={4} />}

      {wallet === 'closed' && (
        <NextStep title="Deposits are not open yet">
          Your admin links your trading account; deposits open once the workspace's wallet is set.
        </NextStep>
      )}

      {wallet !== null && wallet !== 'closed' && (
        <>
          <Card>
            <div className="grid gap-5 md:grid-cols-[192px_1fr]">
              {qr && (
                <img src={qr} alt="QR code of the deposit address" width={192} height={192}
                     className="rounded-inset border border-line bg-card" />
              )}
              <div className="space-y-3 min-w-0">
                <div>
                  <div className="desk-label">Send only</div>
                  <div className="text-lg font-semibold text-ink">{wallet.coin} on {wallet.network}</div>
                </div>
                <div>
                  <div className="desk-label">Address</div>
                  <div className="flex flex-wrap items-start gap-3">
                    <div ref={addressRef} className="num text-sm text-ink break-all min-w-0 flex-1">
                      {wallet.address}
                    </div>
                    <Button variant="secondary" size="sm" onClick={() => copyAddress(wallet.address)}>
                      {copy === 'copied' ? 'Copied' : 'Copy address'}
                    </Button>
                  </div>
                  {/* Always mounted so screen readers hear the change. */}
                  <p role="status" className="text-xs text-ink-soft mt-1 min-h-4">
                    {copy === 'copied' ? 'Address copied to the clipboard.'
                      : copy === 'selected' ? 'Address selected. Copy it with Ctrl+C, or long-press on a phone.'
                      : ''}
                  </p>
                </div>
                {wallet.memo && (
                  <div>
                    <div className="desk-label">Memo / tag</div>
                    <div className="num text-sm text-ink">{wallet.memo}</div>
                  </div>
                )}
                <Banner kind="warn" announce={false}>
                  Sending any other coin or network to this address will lose the funds.
                </Banner>
              </div>
            </div>
          </Card>

          <Card title="Tell us about your transfer">
            <form onSubmit={submit} className="space-y-4">
              <div className="flex gap-3 flex-wrap items-end">
                <label className="block w-40">
                  <span className="desk-label block mb-1">Amount ({wallet.coin})</span>
                  <Input aria-label={`Amount in ${wallet.coin}`} num value={form.amount} required
                         onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                </label>
                <label className="block flex-1 min-w-56">
                  <span className="desk-label block mb-1">Transaction ID</span>
                  <Input aria-label="Transaction ID" num value={form.txid} required
                         onChange={(e) => setForm({ ...form, txid: e.target.value })} />
                </label>
              </div>
              <label className="block">
                <span className="desk-label block mb-1">Note (optional)</span>
                <Input aria-label="Note" value={form.note}
                       onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </label>
              <Button type="submit" disabled={busy}>
                I have sent it
              </Button>
            </form>
          </Card>
        </>
      )}

      <Card title="Your deposit notices" inset>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-4 py-2 font-semibold">Filed</th>
                <th className="desk-label px-4 py-2 font-semibold text-right">Amount</th>
                <th className="desk-label px-4 py-2 font-semibold">Transaction</th>
                <th className="desk-label px-4 py-2 font-semibold">Status</th>
                <th className="desk-label px-4 py-2 font-semibold">Admin note</th>
              </tr>
            </thead>
            <tbody>
              {deposits.length === 0 && (
                <tr><td colSpan={5} className="text-center py-8 text-ink-faint">No notices yet</td></tr>
              )}
              {deposits.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0">
                  <td data-label="Filed" className="num px-4 py-2.5">{formatWhen(d.created_at)}</td>
                  <td data-label="Amount" className="tnum px-4 py-2.5 text-right">{money(d.amount, d.coin)}</td>
                  <td data-label="Transaction" className="num px-4 py-2.5 break-all">{d.txid}</td>
                  <td data-label="Status" className="px-4 py-2.5">
                    <Badge tone={BADGE_TONE[statusTone(d.status)]}>{statusLabel(d.status)}</Badge>
                  </td>
                  <td data-label="Admin note" className="px-4 py-2.5 text-ink-soft">{d.decision_note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
