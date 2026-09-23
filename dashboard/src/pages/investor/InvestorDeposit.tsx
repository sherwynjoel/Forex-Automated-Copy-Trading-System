import { useCallback, useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { pillClass, statusLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import type { InvestorDeposit as Deposit, InvestorWallet } from '../../lib/types'

export default function InvestorDeposit() {
  const { orgId } = useOrg()
  const [wallet, setWallet] = useState<InvestorWallet | null | 'closed'>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [form, setForm] = useState({ amount: '', txid: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setDeposits(await orgApi<Deposit[]>(orgId, 'investor/deposits'))
    } catch (err) {
      setError(errorText(err, 'Could not load your deposits'))
    }
    try {
      const w = await orgApi<InvestorWallet>(orgId, 'investor/wallet')
      setWallet(w)
      setQr(await QRCode.toDataURL(w.address, { width: 192, margin: 1 }))
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('404')) setWallet('closed')
      else setError(errorText(err, 'Could not load the deposit address'))
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

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
      <header>
        <h1 className="page-title">Deposit</h1>
        <p className="text-sm text-ink-soft mt-1">
          Send crypto to the address below, then tell us the amount and the transaction ID.
        </p>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {wallet === 'closed' && (
        <section className="rounded-lg border border-line bg-card p-5">
          <p className="text-sm text-ink">Deposits are not open yet. Please check back later.</p>
        </section>
      )}

      {wallet !== null && wallet !== 'closed' && (
        <>
          <section className="rounded-lg border border-line bg-card p-5 grid gap-5 md:grid-cols-[192px_1fr]">
            {qr && <img src={qr} alt="QR code of the deposit address" width={192} height={192} />}
            <div className="space-y-3">
              <div>
                <div className="desk-label">Send only</div>
                <div className="text-lg font-semibold text-ink">{wallet.coin} on {wallet.network}</div>
              </div>
              <div>
                <div className="desk-label">Address</div>
                <div className="num text-sm text-ink break-all">{wallet.address}</div>
              </div>
              {wallet.memo && (
                <div>
                  <div className="desk-label">Memo / tag</div>
                  <div className="num text-sm text-ink">{wallet.memo}</div>
                </div>
              )}
              <p className="text-xs text-warn-deep bg-warn-wash rounded px-2 py-1">
                Sending any other coin or network to this address will lose the funds.
              </p>
            </div>
          </section>

          <form onSubmit={submit} className="rounded-lg border border-line bg-card p-5 space-y-4">
            <h2 className="desk-label">I have sent it</h2>
            <div className="flex gap-3 flex-wrap items-end">
              <label className="block w-40">
                <span className="desk-label block mb-1">Amount</span>
                <input aria-label="Amount" value={form.amount} required
                       onChange={(e) => setForm({ ...form, amount: e.target.value })}
                       className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
              </label>
              <label className="block flex-1 min-w-56">
                <span className="desk-label block mb-1">Transaction ID</span>
                <input aria-label="Transaction ID" value={form.txid} required
                       onChange={(e) => setForm({ ...form, txid: e.target.value })}
                       className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
              </label>
            </div>
            <label className="block">
              <span className="desk-label block mb-1">Note (optional)</span>
              <input aria-label="Note" value={form.note}
                     onChange={(e) => setForm({ ...form, note: e.target.value })}
                     className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
            <button type="submit" disabled={busy}
                    className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
              I have sent it
            </button>
          </form>
        </>
      )}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3 flex items-baseline justify-between">
          <h2 className="desk-label">Your deposit notices</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Filed</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Amount</th>
                <th className="desk-label px-5 py-2 font-semibold">Transaction</th>
                <th className="desk-label px-5 py-2 font-semibold">Status</th>
                <th className="desk-label px-5 py-2 font-semibold">Admin note</th>
              </tr>
            </thead>
            <tbody>
              {deposits.length === 0 && (
                <tr><td colSpan={5} className="text-center py-8 text-ink-faint">No notices yet</td></tr>
              )}
              {deposits.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0">
                  <td data-label="Filed" className="num px-5 py-2.5">{formatWhen(d.created_at)}</td>
                  <td data-label="Amount" className="tnum px-5 py-2.5 text-right">{money(d.amount)} {d.coin}</td>
                  <td data-label="Transaction" className="num px-5 py-2.5 break-all">{d.txid}</td>
                  <td data-label="Status" className="px-5 py-2.5">
                    <span className={`desk-label px-2 py-0.5 rounded ${pillClass(d.status)}`}>{statusLabel(d.status)}</span>
                  </td>
                  <td data-label="Admin note" className="px-5 py-2.5 text-ink-soft">{d.decision_note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
