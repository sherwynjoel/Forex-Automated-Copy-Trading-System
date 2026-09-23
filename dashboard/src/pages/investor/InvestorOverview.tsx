import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money, signed } from '../../lib/format'
import { moneyOrDash, pillClass, statusLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import type {
  Analytics, InvestorDeposit, InvestorPositions, InvestorSummary, InvestorWithdrawal,
} from '../../lib/types'

const POLL_MS = 10000

type Activity = { key: string; when: string; what: string; amount: number; status: string }

export default function InvestorOverview() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [activity, setActivity] = useState<Activity[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setSummary(s)
      const [deps, wds] = await Promise.all([
        orgApi<InvestorDeposit[]>(orgId, 'investor/deposits'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      const rows: Activity[] = [
        ...deps.map((d) => ({ key: `d${d.id}`, when: d.created_at, what: `Deposit ${d.coin}`,
                              amount: d.amount, status: d.status })),
        ...wds.map((w) => ({ key: `w${w.id}`, when: w.created_at, what: 'Withdrawal',
                             amount: -w.amount, status: w.status })),
      ].sort((a, b) => (a.when < b.when ? 1 : -1)).slice(0, 8)
      setActivity(rows)
      if (s.link_state === 'linked') {
        const [p, a] = await Promise.all([
          orgApi<InvestorPositions>(orgId, 'investor/positions'),
          orgApi<Analytics>(orgId, 'investor/analytics?weeks=4'),
        ])
        setPositions(p); setAnalytics(a)
      } else {
        setPositions(null)
        setAnalytics(null)
      }
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your overview'))
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  if (!summary && !error) return <div className="text-center py-12 text-ink-faint">Loading...</div>

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="page-title">Overview</h1>
        {summary && <p className="text-sm text-ink-soft mt-1">{summary.org.name}</p>}
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      {summary?.link_state === 'unlinked' && (
        <section className="rounded-lg border border-line bg-card p-5 space-y-2">
          <h2 className="text-lg font-semibold text-ink">Your account is being set up</h2>
          <p className="text-sm text-ink-soft">
            Once your deposit is confirmed, an admin opens your trading account and links
            it here. You can file your deposit notice on the Deposit page now.
          </p>
        </section>
      )}

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Current equity" value={moneyOrDash(summary.equity)} tone="brand"
                    sub={summary.link_state === 'linked'
                      ? `${summary.equity_source} figure` : 'no account yet'} />
          <StatTile label="Profit" value={moneyOrDash(summary.profit)}
                    tone={summary.profit == null ? 'neutral' : summary.profit < 0 ? 'loss' : 'profit'}
                    sub="equity minus what you put in" />
          <StatTile label="Net deposits" value={money(summary.net_deposits)}
                    sub={`${money(summary.total_deposited)} in · ${money(summary.total_withdrawn)} out`} />
          <StatTile label="Pending withdrawals" value={money(summary.pending_withdrawn)}
                    sub={summary.available != null ? `${money(summary.available)} available` : undefined} />
        </div>
      )}

      {summary?.account && (
        <section className="rounded-lg border border-line bg-card p-5 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <div><span className="desk-label mr-2">Account</span>
            <span className="text-ink">{summary.account.nickname ?? summary.account.account_id}</span></div>
          <div><span className="desk-label mr-2">Platform</span>
            <span className="text-ink uppercase">{summary.account.platform}</span></div>
          <div><span className="desk-label mr-2">Connection</span>
            <span className={summary.account.connected ? 'text-profit' : 'text-warn-deep'}>
              {summary.account.connected ? 'connected' : 'terminal offline'}
            </span></div>
        </section>
      )}

      {positions && (
        <section className="rounded-lg border border-line bg-card overflow-hidden">
          <div className="px-5 pt-4 pb-3 flex items-baseline justify-between">
            <h2 className="desk-label">Open positions</h2>
            <span className="text-xs text-ink-soft">{positions.equity_source}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-5 py-2 font-semibold">Side</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Volume</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Entry</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Current</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">SL / TP</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Live P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.length === 0 && (
                  <tr><td colSpan={7} className="text-center py-8 text-ink-faint">No open positions</td></tr>
                )}
                {positions.positions.map((p) => (
                  <tr key={p.position_id} className="border-b border-line last:border-0">
                    <td data-label="Symbol" className="px-5 py-2.5 text-ink">{p.symbol ?? '—'}</td>
                    <td data-label="Side" className="px-5 py-2.5">{p.side}</td>
                    <td data-label="Volume" className="tnum px-5 py-2.5 text-right">{p.volume}</td>
                    <td data-label="Entry" className="tnum px-5 py-2.5 text-right">{p.entry_price ?? '—'}</td>
                    <td data-label="Current" className="tnum px-5 py-2.5 text-right">{p.current_price ?? '—'}</td>
                    <td data-label="SL / TP" className="tnum px-5 py-2.5 text-right">{p.stop_loss ?? '—'} / {p.take_profit ?? '—'}</td>
                    <td data-label="Live P&L" className={`tnum px-5 py-2.5 text-right ${(p.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>
                      {p.pnl_quote == null ? '—' : signed(p.pnl_quote)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {analytics && (
        <section className="rounded-lg border border-line bg-card p-5 space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 className="desk-label">Performance, last {analytics.weeks} weeks</h2>
            <span className="text-xs text-ink-soft">{analytics.closed_trades} closed trades</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatTile label="Net P&L" value={money(analytics.net_pnl)}
                      tone={analytics.net_pnl < 0 ? 'loss' : 'profit'} />
            <StatTile label="Win rate"
                      value={analytics.win_rate == null ? '—' : `${analytics.win_rate.toFixed(1)}%`}
                      sub={`${analytics.wins} won · ${analytics.losses} lost`} />
            <StatTile label="Max drawdown" value={money(analytics.max_drawdown)} tone="loss"
                      sub={`${analytics.max_drawdown_pct.toFixed(1)}% from peak`} />
            <StatTile label="Profit factor"
                      value={analytics.profit_factor == null ? '—' : analytics.profit_factor.toFixed(2)} />
          </div>
          {analytics.equity_curve.length > 1 && (
            <EquityCurve points={analytics.equity_curve} height={160} />
          )}
        </section>
      )}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3"><h2 className="desk-label">Recent activity</h2></div>
        <ul className="divide-y divide-line">
          {activity.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {activity.map((a) => (
            <li key={a.key} className="px-5 py-2.5 text-sm flex items-center gap-4">
              <span className="num text-ink-soft w-40 shrink-0">{formatWhen(a.when)}</span>
              <span className="text-ink flex-1">{a.what}</span>
              <span className={`tnum ${a.amount < 0 ? 'text-loss' : 'text-ink'}`}>{signed(a.amount)}</span>
              <span className={`desk-label px-2 py-0.5 rounded ${pillClass(a.status)}`}>{statusLabel(a.status)}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
