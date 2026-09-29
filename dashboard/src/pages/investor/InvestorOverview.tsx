import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money, signed } from '../../lib/format'
import { ACCOUNT_CURRENCY, moneyOrDash, statusLabel, statusTone } from '../../lib/investor'
import Badge, { type BadgeTone } from '../../components/Badge'
import Banner from '../../components/Banner'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import NextStep from './NextStep'
import type {
  Analytics, InvestorDeposit, InvestorPositions, InvestorSummary, InvestorWithdrawal,
} from '../../lib/types'

const POLL_MS = 10000

// statusTone's four states, mapped onto the desk's one chip.
const BADGE_TONE: Record<ReturnType<typeof statusTone>, BadgeTone> = {
  ok: 'profit', warn: 'warn', bad: 'loss', quiet: 'neutral',
}

type Activity = {
  key: string; when: string; what: string; amount: number; unit: string; status: string
}

export default function InvestorOverview() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [activity, setActivity] = useState<Activity[]>([])
  // The ledger's coin: the newest deposit row's `coin`. The API only files
  // deposits in the wallet's coin (investor.py file_deposit), so that row
  // names the unit every ledger total is kept in. With no deposit yet,
  // the account-currency default.
  const [ledgerUnit, setLedgerUnit] = useState<string>(ACCOUNT_CURRENCY)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setSummary(s)
      const [deps, wds] = await Promise.all([
        orgApi<InvestorDeposit[]>(orgId, 'investor/deposits'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      const unit = deps[0]?.coin ?? ACCOUNT_CURRENCY
      setLedgerUnit(unit)
      const rows: Activity[] = [
        ...deps.map((d) => ({ key: `d${d.id}`, when: d.created_at, what: `Deposit ${d.coin}`,
                              amount: d.amount, unit: d.coin, status: d.status })),
        // Withdrawal rows carry no coin (investor.py _withdrawal_json); they
        // are paid in the ledger's coin.
        ...wds.map((w) => ({ key: `w${w.id}`, when: w.created_at, what: 'Withdrawal',
                             amount: -w.amount, unit, status: w.status })),
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

  return (
    <div className="space-y-6 max-w-6xl">
      <PageHeader title="Overview" subtitle={summary?.org.name} />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!summary && !error && <Loading lines={4} />}

      {summary?.link_state === 'unlinked' && (
        <NextStep title="Your account is being set up">
          {summary.wallet_configured
            ? 'Your admin links your trading account once your deposit is confirmed; you can file your deposit notice on the Deposit page now.'
            : "Your admin links your trading account; deposits open once the workspace's wallet is set."}
        </NextStep>
      )}

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Current equity" value={moneyOrDash(summary.equity, ACCOUNT_CURRENCY)} tone="brand"
                    sub={summary.link_state === 'linked'
                      ? `${summary.equity_source} figure` : 'no account yet'} />
          <StatTile label="Profit" value={moneyOrDash(summary.profit, ACCOUNT_CURRENCY)}
                    tone={summary.profit == null ? 'neutral' : summary.profit < 0 ? 'loss' : 'profit'}
                    sub="equity minus what you put in" />
          <StatTile label="Net deposits" value={money(summary.net_deposits, ledgerUnit)}
                    sub={`${money(summary.total_deposited, ledgerUnit)} in · ${money(summary.total_withdrawn, ledgerUnit)} out`} />
          <StatTile label="Pending withdrawals" value={money(summary.pending_withdrawn, ledgerUnit)}
                    sub={summary.available != null
                      ? `${money(summary.available, ACCOUNT_CURRENCY)} available` : undefined} />
        </div>
      )}

      {summary?.account && (
        <Card>
          <dl className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <div><dt className="desk-label inline mr-2">Account</dt>
              <dd className="inline text-ink">{summary.account.nickname ?? summary.account.account_id}</dd></div>
            <div><dt className="desk-label inline mr-2">Platform</dt>
              <dd className="inline text-ink uppercase">{summary.account.platform}</dd></div>
            <div><dt className="desk-label inline mr-2">Connection</dt>
              <dd className={`inline ${summary.account.connected ? 'text-profit' : 'text-warn-deep'}`}>
                {summary.account.connected ? 'connected' : 'terminal offline'}
              </dd></div>
          </dl>
        </Card>
      )}

      {positions && (
        <Card title="Open positions" inset
              actions={<span className="text-xs text-ink-soft">{positions.equity_source}</span>}>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-4 py-2 font-semibold">Side</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Volume</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Entry</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Current</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">SL / TP</th>
                  {/* pnl_quote is in each symbol's quote currency, which is
                      not always the account currency, so the header says so. */}
                  <th className="desk-label px-4 py-2 font-semibold text-right">Live P&L (quote currency)</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.length === 0 && (
                  <tr><td colSpan={7} className="text-center py-8 text-ink-faint">No open positions</td></tr>
                )}
                {positions.positions.map((p) => (
                  <tr key={p.position_id} className="border-b border-line last:border-0">
                    <td data-label="Symbol" className="px-4 py-2.5 text-ink">{p.symbol ?? '—'}</td>
                    <td data-label="Side" className="px-4 py-2.5">{p.side}</td>
                    <td data-label="Volume" className="tnum px-4 py-2.5 text-right">{p.volume}</td>
                    <td data-label="Entry" className="tnum px-4 py-2.5 text-right">{p.entry_price ?? '—'}</td>
                    <td data-label="Current" className="tnum px-4 py-2.5 text-right">{p.current_price ?? '—'}</td>
                    <td data-label="SL / TP" className="tnum px-4 py-2.5 text-right">{p.stop_loss ?? '—'} / {p.take_profit ?? '—'}</td>
                    <td data-label="Live P&L (quote currency)" className={`tnum px-4 py-2.5 text-right ${(p.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>
                      {p.pnl_quote == null ? '—' : signed(p.pnl_quote)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {analytics && (
        <Card title={`Performance, last ${analytics.weeks} weeks`}
              actions={<span className="text-xs text-ink-soft">{analytics.closed_trades} closed trades</span>}>
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatTile label="Net P&L" value={money(analytics.net_pnl, ACCOUNT_CURRENCY)}
                        tone={analytics.net_pnl < 0 ? 'loss' : 'profit'} />
              <StatTile label="Win rate"
                        value={analytics.win_rate == null ? '—' : `${analytics.win_rate.toFixed(1)}%`}
                        sub={`${analytics.wins} won · ${analytics.losses} lost`} />
              <StatTile label="Max drawdown" value={money(analytics.max_drawdown, ACCOUNT_CURRENCY)} tone="loss"
                        sub={`${analytics.max_drawdown_pct.toFixed(1)}% from peak`} />
              <StatTile label="Profit factor"
                        value={analytics.profit_factor == null ? '—' : analytics.profit_factor.toFixed(2)} />
            </div>
            {analytics.equity_curve.length > 1 && (
              <div className="inset p-3">
                <EquityCurve points={analytics.equity_curve} height={160} />
              </div>
            )}
          </div>
        </Card>
      )}

      <Card title="Recent activity" inset>
        <ul className="divide-y divide-line">
          {activity.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {activity.map((a) => (
            <li key={a.key} className="px-4 py-2.5 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
              {/* Fluid, not a fixed w-40: on a 360px phone the date takes its
                  own line and the rest wraps beneath it. */}
              <time dateTime={a.when} className="num text-ink-soft min-w-0 basis-full sm:basis-auto">
                {formatWhen(a.when)}
              </time>
              <span className="text-ink flex-1 min-w-0">{a.what}</span>
              <span className={`tnum ${a.amount < 0 ? 'text-loss' : 'text-ink'}`}>
                {`${signed(a.amount)} ${a.unit}`}
              </span>
              <Badge tone={BADGE_TONE[statusTone(a.status)]}>{statusLabel(a.status)}</Badge>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
