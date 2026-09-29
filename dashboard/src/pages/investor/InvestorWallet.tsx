import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { ACCOUNT_CURRENCY, WALLETS, entryLabel, walletLabel } from '../../lib/investor'
import { useHiddenBalances } from '../../lib/hideBalances'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import type { InvestorSummary, WalletEntriesPage, WalletEntry } from '../../lib/types'

const ACTIONS = [
  { slug: 'deposit', title: 'Deposit', text: 'Bank transfer or crypto, with a receipt.' },
  { slug: 'withdraw', title: 'Withdraw', text: 'To a payout account an admin approved.' },
  { slug: 'transfer', title: 'Transfer', text: 'Between your wallets and your trading account.' },
  { slug: 'transactions', title: 'Transactions', text: 'Every movement, with a CSV download.' },
]

/**
 * The Balance-share bar for one wallet row. A bar's fill and its percentage
 * both encode the proportion of money a wallet holds, so -- like every
 * other chart of money values in the product (see InvestorDashboard's
 * CashFlowChart) -- it is dropped entirely, not just recolored, whenever
 * balances are hidden; a bare "hidden" placeholder takes its place. Reads
 * the hide-balances flag itself, in a component of its own, so a broadcast
 * only ever re-renders this row's cell, not the page (which calls useOrg()).
 */
function ShareCell({ pct }: { pct: number }) {
  const [hidden] = useHiddenBalances()
  if (hidden) {
    return <span role="img" aria-label="Hidden amount" className="text-xs text-ink-faint">••••</span>
  }
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 rounded-full bg-line overflow-hidden" aria-hidden="true">
        <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-xs text-ink-soft">{`${pct.toFixed(0)}%`}</span>
    </div>
  )
}

export default function InvestorWallet() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [entries, setEntries] = useState<WalletEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, page] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<WalletEntriesPage>(orgId, 'investor/wallet-entries?limit=20'),
      ])
      setSummary(s); setEntries(page.entries)
    } catch (err) {
      setError(errorText(err, 'Could not load your wallet'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  // Share of the money you hold: negative balances (after an adjustment)
  // do not shrink the others' share. Guarded against non-finite figures so
  // a malformed response cannot throw while rendering the bars.
  const total = summary
    ? WALLETS.reduce((s, w) => {
        const b = summary.wallets[w].balance
        return s + (Number.isFinite(b) ? Math.max(0, b) : 0)
      }, 0)
    : 0

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Wallet" subtitle="Your four wallets and what each one holds." />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!loaded && <Loading lines={4} />}

      {summary && (
        <Card title="Your wallets" inset>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm" aria-label="Your wallets">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Wallet</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Balance</th>
                  <th className="desk-label px-4 py-2 font-semibold">Share</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">On hold</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Available</th>
                </tr>
              </thead>
              <tbody>
                {WALLETS.map((w) => {
                  const f = summary.wallets[w]
                  const b = Number.isFinite(f.balance) ? Math.max(0, f.balance) : 0
                  const pct = total > 0 ? (b / total) * 100 : 0
                  return (
                    <tr key={w} className="border-b border-line last:border-0">
                      <td data-label="Wallet" className="px-4 py-2.5 text-ink font-semibold">{walletLabel(w)}</td>
                      <td data-label="Balance" className="px-4 py-2.5 text-right"><Money value={f.balance} unit={unit} /></td>
                      <td data-label="Share" className="px-4 py-2.5"><ShareCell pct={pct} /></td>
                      <td data-label="On hold" className="px-4 py-2.5 text-right"><Money value={f.on_hold} unit={unit} /></td>
                      <td data-label="Available" className="px-4 py-2.5 text-right"><Money value={f.available} unit={unit} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ACTIONS.map((a) => (
          <Card key={a.slug} title={a.title}>
            <p className="text-sm text-ink-soft mb-3">{a.text}</p>
            <Button variant="secondary" size="sm" to={`${base}/${a.slug}`}>{a.title}</Button>
          </Card>
        ))}
      </div>

      <Card title="Recent entries" inset
            actions={<Button variant="ghost" size="sm" to={`${base}/transactions`}>View all</Button>}>
        <ul className="divide-y divide-line">
          {entries.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {entries.map((e) => (
            <li key={e.id} className="px-4 py-2.5 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
              <time dateTime={e.created_at} className="num text-ink-soft min-w-0 basis-full sm:basis-auto">
                {formatWhen(e.created_at)}
              </time>
              <span className="text-ink flex-1 min-w-0">{entryLabel(e)}</span>
              <span className="text-xs text-ink-soft">{walletLabel(e.wallet)}</span>
              <Money value={e.amount} unit={e.currency} signed
                     className={e.amount < 0 ? 'text-loss' : 'text-profit'} />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
