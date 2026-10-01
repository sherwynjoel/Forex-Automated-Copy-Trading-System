import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, money } from '../../lib/format'
import { ACCOUNT_CURRENCY } from '../../lib/investor'
import { useHiddenBalances } from '../../lib/hideBalances'
import Banner from '../../components/Banner'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import NextStep from './NextStep'
import type { AccountRequest, Analytics, InvestorPositions, InvestorSummary } from '../../lib/types'

const POLL_MS = 10000

/**
 * The 4-week performance snapshot. Reads the hide-balances flag itself, like
 * InvestorDashboard's WalletTiles and CashFlowChart, so a broadcast only
 * re-renders this card, not the page (which calls useOrg()). Net P&L and max
 * drawdown mask by hand (StatTile's `value` is a string); the equity curve --
 * a chart of money values -- is dropped entirely while hidden, not just
 * recolored, matching CashFlowChart's placeholder.
 */
function AnalyticsPanel({ analytics, unit }: { analytics: Analytics; unit: string }) {
  const [hidden] = useHiddenBalances()
  return (
    <Card title={`Performance, last ${analytics.weeks} weeks`}
          actions={<span className="text-xs text-ink-soft">{analytics.closed_trades} closed trades</span>}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {/* tone is computed from the real value, so it must go along with
              the figure it colors -- omitted while hidden, or the "••••"
              glyph would still read red/green and give the sign away. */}
          <StatTile label="Net P&L" value={hidden ? '••••' : money(analytics.net_pnl, unit)}
                    tone={hidden ? undefined : (analytics.net_pnl < 0 ? 'loss' : 'profit')} />
          <StatTile label="Win rate"
                    value={analytics.win_rate == null ? '—' : `${analytics.win_rate.toFixed(1)}%`}
                    sub={`${analytics.wins} won · ${analytics.losses} lost`} />
          <StatTile label="Max drawdown" value={hidden ? '••••' : money(analytics.max_drawdown, unit)}
                    tone={hidden ? undefined : 'loss'}
                    sub={`${analytics.max_drawdown_pct.toFixed(1)}% from peak`} />
          <StatTile label="Profit factor"
                    value={analytics.profit_factor == null ? '—' : analytics.profit_factor.toFixed(2)} />
        </div>
        {analytics.equity_curve.length > 1 && (
          <div className="inset p-3">
            {hidden
              ? <p className="py-8 text-center text-xs text-ink-faint">Chart hidden while balances are hidden</p>
              : <EquityCurve points={analytics.equity_curve} height={160} />}
          </div>
        )}
      </div>
    </Card>
  )
}

export default function InvestorAccount() {
  const { me, org, orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [login, setLogin] = useState<AccountRequest | null>(null)
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, requests] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<AccountRequest[]>(orgId, 'investor/account-requests'),
      ])
      setSummary(s)
      setLogin(requests.find((r) => r.status === 'fulfilled') ?? null)
      if (s.link_state === 'linked') {
        const [p, a] = await Promise.all([
          orgApi<InvestorPositions>(orgId, 'investor/positions'),
          orgApi<Analytics>(orgId, 'investor/analytics?weeks=4'),
        ])
        setPositions(p); setAnalytics(a)
      } else {
        setPositions(null); setAnalytics(null)
      }
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your account'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Account" />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <Card title="Profile">
        <dl className="grid gap-3 md:grid-cols-2 text-sm">
          <div><dt className="desk-label">Name</dt><dd className="text-ink">{me.user.display_name}</dd></div>
          <div><dt className="desk-label">Email</dt><dd className="text-ink">{me.user.email}</dd></div>
          <div><dt className="desk-label">Workspace</dt><dd className="text-ink">{org.name}</dd></div>
          <div><dt className="desk-label">Role</dt><dd className="text-ink">Investor</dd></div>
        </dl>
      </Card>

      {login && (
        <Card title="Your MT5 login">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-3 text-sm">
            <div><dt className="desk-label">Login</dt><dd className="num text-ink">{login.mt5_login}</dd></div>
            <div><dt className="desk-label">Server</dt><dd className="num text-ink">{login.mt5_server}</dd></div>
            <div><dt className="desk-label">Package</dt>
              <dd className="text-ink">{`${login.package_name} · 1:${login.leverage}`}</dd></div>
          </dl>
        </Card>
      )}

      {!loaded && <Loading lines={3} />}

      {summary && summary.link_state !== 'linked' && (
        <NextStep title="Your account is being set up">
          Your admin links your trading account; positions and performance appear here once it is linked.
        </NextStep>
      )}

      {summary?.account && (
        <Card title="Trading account">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="desk-label">Account</dt>
              <dd className="text-ink">{summary.account.nickname ?? summary.account.account_id}</dd></div>
            <div><dt className="desk-label">Platform</dt>
              <dd className="text-ink uppercase">{summary.account.platform}</dd></div>
            <div><dt className="desk-label">Connection</dt>
              <dd className={summary.account.connected ? 'text-profit' : 'text-warn-deep'}>
                {summary.account.connected ? 'connected' : 'terminal offline'}
              </dd></div>
            <div><dt className="desk-label">Open positions</dt>
              <dd className="num text-ink">{summary.open_positions}</dd></div>
            <div><dt className="desk-label">Equity</dt>
              <dd className="text-ink"><Money value={summary.equity} unit={unit} />
                <span className="text-xs text-ink-soft"> {summary.equity_source}</span></dd></div>
            <div><dt className="desk-label">Net funded</dt>
              <dd className="text-ink"><Money value={summary.net_funded} unit={unit} /></dd></div>
            <div><dt className="desk-label">Profit</dt>
              <dd className={summary.profit != null && summary.profit < 0 ? 'text-loss' : 'text-profit'}>
                <Money value={summary.profit} unit={unit} /></dd></div>
            <div><dt className="desk-label">Available to move</dt>
              <dd className="text-ink"><Money value={summary.account_available} unit={unit} /></dd></div>
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
                    <td data-label="Live P&L (quote currency)" className="px-4 py-2.5 text-right">
                      <Money value={p.pnl_quote} signed
                             className={(p.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {analytics && <AnalyticsPanel analytics={analytics} unit={unit} />}
    </div>
  )
}
