import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import type { Deal } from '../../lib/types'

const WEEK_MS = 7 * 24 * 3600 * 1000

function netOf(d: Deal): number | null {
  if (!d.close) return null
  return d.close.gross_profit + d.close.swap + d.close.commission
}

export default function InvestorHistory() {
  const { orgId } = useOrg()
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
      const r = await orgApi<{ deals: Deal[]; has_more: boolean }>(
        orgId, `investor/history/deals?from=${windowEnd - WEEK_MS}&to=${windowEnd}`)
      setDeals(r.deals.filter((d) => d.close != null))
    } catch (err) {
      setError(errorText(err, 'Could not load your history'))
    } finally {
      setLoading(false)
    }
  }, [orgId, windowEnd])

  useEffect(() => { load() }, [load])

  const total = deals.reduce((sum, d) => sum + (netOf(d) ?? 0), 0)

  const goEarlier = () => { setWindowEnd((t) => t - WEEK_MS); setAtNow(false) }
  const goLater = () => {
    const now = nowAnchorRef.current
    const next = windowEnd + WEEK_MS
    if (next >= now) { setWindowEnd(now); setAtNow(true) } else { setWindowEnd(next) }
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">History</h1>
          <p className="text-sm text-ink-soft mt-1">
            Closed trades on your account, one week at a time, straight from the broker.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Button variant="secondary" size="sm" onClick={goEarlier}>
            Earlier
          </Button>
          <span className="num text-ink-soft">
            {formatWhen(windowEnd - WEEK_MS)} – {formatWhen(windowEnd)}
          </span>
          <Button variant="secondary" size="sm" onClick={goLater} disabled={atNow}>
            Later
          </Button>
        </div>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3 flex items-baseline justify-between">
          <h2 className="desk-label">Closed trades</h2>
          <span className={`tnum text-sm font-semibold ${total < 0 ? 'text-loss' : 'text-profit'}`}>
            {money(total)} this week
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Closed</th>
                <th className="desk-label px-5 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-5 py-2 font-semibold">Side</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Lots</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Entry → Exit</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Gross</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Swap + fees</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Net</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={8} className="text-center py-8 text-ink-faint">Loading...</td></tr>}
              {!loading && deals.length === 0 && (
                <tr><td colSpan={8} className="text-center py-8 text-ink-faint">No closed trades in this week</td></tr>
              )}
              {!loading && deals.map((d) => {
                const net = netOf(d) ?? 0
                return (
                  <tr key={d.deal_id} className="border-b border-line last:border-0">
                    <td data-label="Closed" className="num px-5 py-2.5">{formatWhen(d.execution_timestamp)}</td>
                    <td data-label="Symbol" className="px-5 py-2.5 text-ink">{d.symbol ?? d.symbol_id}</td>
                    <td data-label="Side" className="px-5 py-2.5">{d.side}</td>
                    <td data-label="Lots" className="tnum px-5 py-2.5 text-right">{d.close?.closed_volume_lots ?? d.volume_lots ?? '—'}</td>
                    <td data-label="Entry → Exit" className="tnum px-5 py-2.5 text-right">{d.close?.entry_price} → {d.execution_price ?? '—'}</td>
                    <td data-label="Gross" className="tnum px-5 py-2.5 text-right">{money(d.close!.gross_profit)}</td>
                    <td data-label="Swap + fees" className="tnum px-5 py-2.5 text-right">{money(d.close!.swap + d.close!.commission)}</td>
                    <td data-label="Net" className={`tnum px-5 py-2.5 text-right font-semibold ${net < 0 ? 'text-loss' : 'text-profit'}`}>{money(net)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
