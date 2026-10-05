import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, accountName, pickAccount } from '../../lib/investor'
import { useHiddenBalances } from '../../lib/hideBalances'
import Banner from '../../components/Banner'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import NextStep from './NextStep'
import AccountSwitcher from './AccountSwitcher'
import type { Analytics, InvestorPositions, InvestorSummary } from '../../lib/types'

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
  const [params, setParams] = useSearchParams()
  const wanted = params.get('account')
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  // Bumped per load: a switch starts a new load while the previous
  // account's figures may still be in flight; only the newest may land.
  const seq = useRef(0)

  const refresh = useCallback(async () => {
    const mine = ++seq.current
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      const a = pickAccount(s.accounts, wanted)
      const [p, an] = a
        ? await Promise.all([
            orgApi<InvestorPositions>(orgId, `investor/positions?account_id=${a.account_id}`),
            orgApi<Analytics>(orgId, `investor/analytics?weeks=4&account_id=${a.account_id}`),
          ])
        : [null, null] as const
      if (mine !== seq.current) return
      setSummary(s); setPositions(p); setAnalytics(an)
      setError(null)
    } catch (err) {
      if (mine !== seq.current) return
      setError(errorText(err, 'Could not load your account'))
    }
    setLoaded(true)
  }, [orgId, wanted])

  // Switching accounts (wanted changes) drops the previous account's
  // positions and analytics before refresh's fetch lands, so its card
  // never sits under the new account's data for a frame; a plain poll on
  // the same account leaves them in place until the fresher figures arrive.
  useEffect(() => {
    setPositions(null)
    setAnalytics(null)
  }, [wanted])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  const account = summary ? pickAccount(summary.accounts, wanted) : null

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Account" />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      {summary && (
        <AccountSwitcher accounts={summary.accounts} value={account?.account_id ?? null}
                         onChange={(id) => setParams({ account: String(id) }, { replace: true })} />
      )}

      <Card title="Profile">
        <dl className="grid gap-3 md:grid-cols-2 text-sm">
          <div><dt className="desk-label">Name</dt><dd className="text-ink">{me.user.display_name}</dd></div>
          <div><dt className="desk-label">Email</dt><dd className="text-ink">{me.user.email}</dd></div>
          <div><dt className="desk-label">Workspace</dt><dd className="text-ink">{org.name}</dd></div>
          <div><dt className="desk-label">Role</dt><dd className="text-ink">Investor</dd></div>
        </dl>
      </Card>

      {account?.mt5_login != null && (
        <Card title="Your MT5 login">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 text-sm">
            <div><dt className="desk-label">Login</dt><dd className="num text-ink">{account.mt5_login}</dd></div>
            <div><dt className="desk-label">Server</dt><dd className="num text-ink">{account.mt5_server ?? '—'}</dd></div>
          </dl>
        </Card>
      )}

      {!loaded && <Loading lines={3} />}

      {summary && summary.accounts.length === 0 && (
        <NextStep title="Your account is being set up">
          Your admin links your trading account; positions and performance appear here once it is linked.
        </NextStep>
      )}

      {account && (
        // Titled "Trading account details", not "Trading account": the
        // AccountSwitcher control above is labelled exactly "Trading
        // account" (brief, Step 3), and getByLabelText also matches any
        // element whose aria-labelledby content equals the query text --
        // this Card's own <section aria-labelledby> would tie for that
        // name otherwise, making the switcher ambiguous to find and this
        // card impossible to rule out when it is hidden.
        <Card title="Trading account details">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="desk-label">Account</dt>
              <dd className="text-ink">{accountName(account)}</dd></div>
            <div><dt className="desk-label">Platform</dt>
              <dd className="text-ink uppercase">{account.platform}</dd></div>
            <div><dt className="desk-label">Connection</dt>
              <dd className={account.connected ? 'text-profit' : 'text-warn-deep'}>
                {account.connected ? 'connected' : 'terminal offline'}
              </dd></div>
            <div><dt className="desk-label">Open positions</dt>
              <dd className="num text-ink">{account.open_positions}</dd></div>
            <div><dt className="desk-label">Equity</dt>
              <dd className="text-ink"><Money value={account.equity} unit={unit} />
                <span className="text-xs text-ink-soft"> {account.equity_source}</span></dd></div>
            <div><dt className="desk-label">Net funded</dt>
              <dd className="text-ink"><Money value={account.net_funded} unit={unit} /></dd></div>
            <div><dt className="desk-label">Profit</dt>
              <dd className={account.profit != null && account.profit < 0 ? 'text-loss' : 'text-profit'}>
                <Money value={account.profit} unit={unit} /></dd></div>
            <div><dt className="desk-label">Available to move</dt>
              <dd className="text-ink"><Money value={account.account_available} unit={unit} /></dd></div>
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
