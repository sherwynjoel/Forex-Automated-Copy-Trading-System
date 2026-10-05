import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { ACCOUNT_CURRENCY, pickAccount } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import AccountSwitcher from './AccountSwitcher'
import type { AccountSummary, Deal, InvestorSummary } from '../../lib/types'

const WEEK_MS = 7 * 24 * 3600 * 1000

function netOf(d: Deal): number | null {
  if (!d.close) return null
  return d.close.gross_profit + d.close.swap + d.close.commission
}

export default function InvestorHistory() {
  const { orgId } = useOrg()
  const [params, setParams] = useSearchParams()
  const wanted = params.get('account')
  const [accounts, setAccounts] = useState<AccountSummary[]>([])
  // Fixed once at mount: this page has no "refresh" action, so "now" for
  // paging purposes is "when the page was opened", not a moving target.
  // Reading Date.now() again inside goLater (instead of against this
  // anchor) loses the race against real elapsed time -- by the time a
  // click handler runs, the live clock has already moved past whatever
  // "now" was captured when windowEnd was last set, so an exact
  // Earlier-then-Later round trip could never land back on "at now".
  const nowAnchorRef = useRef(Date.now())
  const [windowEnd, setWindowEnd] = useState(() => nowAnchorRef.current)
  const [atNow, setAtNow] = useState(true)
  const [deals, setDeals] = useState<Deal[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    setDeals([])
    try {
      // The summary names the accounts; with none the history call answers
      // 409 "no account linked yet", shown as today.
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setAccounts(s.accounts)
      const a = pickAccount(s.accounts, wanted)
      const r = await orgApi<{ deals: Deal[]; has_more: boolean }>(
        orgId, `investor/history/deals?from=${windowEnd - WEEK_MS}&to=${windowEnd}`
          + (a ? `&account_id=${a.account_id}` : ''))
      setDeals(r.deals.filter((d) => d.close != null))
    } catch (err) {
      setError(errorText(err, 'Could not load your history'))
    } finally {
      setLoading(false)
    }
  }, [orgId, windowEnd, wanted])

  useEffect(() => { load() }, [load])

  const total = deals.reduce((sum, d) => sum + (netOf(d) ?? 0), 0)

  const goEarlier = () => { setWindowEnd((t) => t - WEEK_MS); setAtNow(false) }
  const goLater = () => {
    const now = nowAnchorRef.current
    const next = windowEnd + WEEK_MS
    if (next >= now) { setWindowEnd(now); setAtNow(true) } else { setWindowEnd(next) }
  }

  // The window is a rolling seven days ending at windowEnd, not a calendar
  // week, so the copy says which seven days rather than "this week".
  const range = `${formatWhen(windowEnd - WEEK_MS)} – ${formatWhen(windowEnd)}`
  const windowLabel = atNow ? 'last 7 days' : range

  return (
    <div className="space-y-6 max-w-6xl">
      <PageHeader
        title="History"
        subtitle="Closed trades on your account, seven days at a time, straight from the broker."
        actions={
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Button variant="secondary" size="sm" onClick={goEarlier}>
              Earlier
            </Button>
            <span className="num text-ink-soft">{range}</span>
            <Button variant="secondary" size="sm" onClick={goLater} disabled={atNow}>
              Later
            </Button>
          </div>
        }
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <AccountSwitcher accounts={accounts} value={pickAccount(accounts, wanted)?.account_id ?? null}
                       onChange={(id) => setParams({ account: String(id) }, { replace: true })} />

      {/* Deal figures are in the account's deposit currency; the summary
          names none, so the labelled default applies (lib/investor.ts).
          Every figure is a Money so hide-balances masks it, and the
          profit/loss tone rides on the Money itself -- never on a wrapper,
          which would keep its colour (and so the sign) while hidden. */}
      <Card title="Closed trades" inset
            actions={
              <span className="tnum text-sm font-semibold">
                <Money value={total} unit={ACCOUNT_CURRENCY}
                       className={total < 0 ? 'text-loss' : 'text-profit'} />
                {` net, ${windowLabel}`}
              </span>
            }>
        {loading ? <Loading lines={3} /> : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Closed</th>
                  <th className="desk-label px-4 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-4 py-2 font-semibold">Side</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Lots</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Entry → Exit</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Gross</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Swap + fees</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Net</th>
                </tr>
              </thead>
              <tbody>
                {deals.length === 0 && (
                  <tr><td colSpan={8} className="text-center py-8 text-ink-faint">
                    {atNow ? 'No closed trades in the last 7 days' : `No closed trades between ${formatWhen(windowEnd - WEEK_MS)} and ${formatWhen(windowEnd)}`}
                  </td></tr>
                )}
                {deals.map((d) => {
                  const net = netOf(d) ?? 0
                  return (
                    <tr key={d.deal_id} className="border-b border-line last:border-0">
                      <td data-label="Closed" className="num px-4 py-2.5">{formatWhen(d.execution_timestamp)}</td>
                      <td data-label="Symbol" className="px-4 py-2.5 text-ink">{d.symbol ?? d.symbol_id}</td>
                      <td data-label="Side" className="px-4 py-2.5">{d.side}</td>
                      <td data-label="Lots" className="tnum px-4 py-2.5 text-right">{d.close?.closed_volume_lots ?? d.volume_lots ?? '—'}</td>
                      <td data-label="Entry → Exit" className="tnum px-4 py-2.5 text-right">{d.close?.entry_price} → {d.execution_price ?? '—'}</td>
                      <td data-label="Gross" className="tnum px-4 py-2.5 text-right"><Money value={d.close!.gross_profit} unit={ACCOUNT_CURRENCY} /></td>
                      <td data-label="Swap + fees" className="tnum px-4 py-2.5 text-right"><Money value={d.close!.swap + d.close!.commission} unit={ACCOUNT_CURRENCY} /></td>
                      <td data-label="Net" className="tnum px-4 py-2.5 text-right font-semibold">
                        <Money value={net} unit={ACCOUNT_CURRENCY} className={net < 0 ? 'text-loss' : 'text-profit'} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
