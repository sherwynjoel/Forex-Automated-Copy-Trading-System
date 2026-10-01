import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { WALLETS, entryLabel, walletLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import Tabs from '../../components/Tabs'
import type { WalletEntriesPage, WalletEntry } from '../../lib/types'

const PAGE = 50
const KINDS: WalletEntry['kind'][] = ['deposit', 'withdrawal', 'transfer', 'adjustment', 'bonus', 'commission', 'fee']
// Which investor page shows the request a ledger row settled.
const REF_PAGE: Record<string, string> = { deposits: 'deposit', withdrawals: 'withdraw', transfers: 'transfer' }

export interface EntriesFilter {
  wallet: string   // 'all' or a WalletKind
  kind: string     // '' or a kind
  from: string     // '' or YYYY-MM-DD
  to: string
  before: number | null
}

/** The wallet-entries tail with only the filters that are set. */
export function entriesQuery(f: EntriesFilter): string {
  const p = new URLSearchParams()
  p.set('limit', String(PAGE))
  if (f.wallet !== 'all') p.set('wallet', f.wallet)
  if (f.kind) p.set('kind', f.kind)
  if (f.from) p.set('from', f.from)
  if (f.to) p.set('to', f.to)
  if (f.before != null) p.set('before', String(f.before))
  return `investor/wallet-entries?${p.toString()}`
}

/** transactions.csv from the rows on screen: RFC 4180 quoting, CRLF, a
 *  plain two-decimal number for the amount so spreadsheets read it as one.
 *  Any other cell a spreadsheet would run as a formula gets a leading '. */
export function toCsv(rows: WalletEntry[]): string {
  const esc = (v: string) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  const AMOUNT_COL = 3
  const inert = (v: string, i: number) => (i !== AMOUNT_COL && /^[=+\-@\t\r]/.test(v) ? `'${v}` : v)
  const lines = [['Date', 'Wallet', 'Kind', 'Amount', 'Reference', 'Note'].join(',')]
  for (const e of rows) {
    lines.push([
      e.created_at, walletLabel(e.wallet), e.kind, e.amount.toFixed(2),
      e.ref_table && e.ref_id != null ? `${e.ref_table}/${e.ref_id}` : '',
      e.note ?? '',
    ].map((v, i) => esc(inert(v, i))).join(','))
  }
  return lines.join('\r\n') + '\r\n'
}

function kindLabel(k: string): string {
  return k.charAt(0).toUpperCase() + k.slice(1)
}

const NO_FILTER = { from: '', to: '', kind: '' }

export default function InvestorTransactions() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [wallet, setWallet] = useState('all')
  const [draft, setDraft] = useState(NO_FILTER)
  const [applied, setApplied] = useState(NO_FILTER)
  const [rows, setRows] = useState<WalletEntry[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  // Bumped on every load(): a tab click, Apply or Clear can fire a fresh
  // request while an earlier one (that tab's Load more, say) is still in
  // flight. If the earlier one's answer lands after the newer request
  // started, it is a stale filter's data and must not overwrite what the
  // current filter actually returned.
  const requestSeq = useRef(0)

  const load = useCallback(async (before: number | null) => {
    const seq = ++requestSeq.current
    setLoading(true)
    try {
      const page = await orgApi<WalletEntriesPage>(orgId, entriesQuery({ wallet, ...applied, before }))
      if (seq !== requestSeq.current) return
      setRows((r) => (before == null ? page.entries : [...r, ...page.entries]))
      setHasMore(page.has_more)
      setNextBefore(page.next_before)
      setError(null)
    } catch (err) {
      if (seq !== requestSeq.current) return
      setError(errorText(err, 'Could not load your transactions'))
    } finally {
      if (seq === requestSeq.current) { setLoading(false); setLoaded(true) }
    }
  }, [orgId, wallet, applied])

  useEffect(() => { load(null) }, [load])

  const apply = (e: React.FormEvent) => { e.preventDefault(); setApplied(draft) }
  const clear = () => { setDraft(NO_FILTER); setApplied(NO_FILTER) }

  const download = () => {
    const blob = new Blob([toCsv(rows)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'transactions.csv'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const tabs = [{ key: 'all', label: 'All' }, ...WALLETS.map((w) => ({ key: w, label: walletLabel(w) }))]

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Transactions"
        subtitle="Every movement on your wallets: deposits, withdrawals, transfers and adjustments."
        actions={<Button variant="secondary" size="sm" onClick={download} disabled={rows.length === 0}>Download CSV</Button>}
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <Card>
        <Tabs items={tabs} value={wallet} onChange={setWallet} label="Wallet" idBase="wallet" />
        <div id="wallet-panel" role="tabpanel" aria-labelledby={`wallet-tab-${wallet}`} className="mt-4 space-y-4">
          <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="desk-label block mb-1">From date</span>
              <Input aria-label="From date" type="date" value={draft.from}
                     onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">To date</span>
              <Input aria-label="To date" type="date" value={draft.to}
                     onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Kind</span>
              <Select aria-label="Kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
                <option value="">All kinds</option>
                {KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
              </Select>
            </label>
            <Button type="submit" variant="secondary" size="sm">Apply</Button>
            <Button variant="ghost" size="sm" onClick={clear}>Clear</Button>
          </form>

          {!loaded ? <Loading lines={4} /> : (
            <div className="inset overflow-x-auto">
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr className="text-left border-b border-line">
                    <th className="desk-label px-4 py-2 font-semibold">Date</th>
                    <th className="desk-label px-4 py-2 font-semibold">Wallet</th>
                    <th className="desk-label px-4 py-2 font-semibold">Kind</th>
                    <th className="desk-label px-4 py-2 font-semibold text-right">Amount</th>
                    <th className="desk-label px-4 py-2 font-semibold">Reference</th>
                    <th className="desk-label px-4 py-2 font-semibold">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 && (
                    <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No transactions yet</td></tr>
                  )}
                  {rows.map((e) => {
                    const page = e.ref_table ? REF_PAGE[e.ref_table] : undefined
                    return (
                      <tr key={e.id} className="border-b border-line last:border-0">
                        <td data-label="Date" className="num px-4 py-2.5">{formatWhen(e.created_at)}</td>
                        <td data-label="Wallet" className="px-4 py-2.5 text-ink">{walletLabel(e.wallet)}</td>
                        <td data-label="Kind" className="px-4 py-2.5">{kindLabel(e.kind)}</td>
                        <td data-label="Amount" className="px-4 py-2.5 text-right">
                          <Money value={e.amount} unit={e.currency} signed
                                 className={e.amount < 0 ? 'text-loss' : 'text-profit'} />
                        </td>
                        <td data-label="Reference" className="px-4 py-2.5">
                          {page
                            ? <Button variant="ghost" size="sm" to={`${base}/${page}`}>{entryLabel(e)}</Button>
                            : <span className="text-ink-soft">{entryLabel(e)}</span>}
                        </td>
                        <td data-label="Note" className="px-4 py-2.5 text-ink-soft">{e.note ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {hasMore && (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={() => load(nextBefore)} busy={loading}>Load more</Button>
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}
