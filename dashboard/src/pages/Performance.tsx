import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import type { Account, Analytics } from '../lib/types'
import { EquityCurve, PnlBars } from '../components/charts'
import StatTile from '../components/StatTile'
import { signed } from '../lib/format'

function accountLabel(account: Account): string {
  const name = account.nickname || `Account ${account.trader_login}`
  return `${name} · ${account.trader_login} · ${account.is_live ? 'Live' : 'Demo'}`
}


/**
 * The performance dashboard: everything is derived from broker deal history
 * via /api/orgs/{orgId}/accounts/{id}/analytics -- realized numbers only,
 * which is the honest basis for "how is this account actually doing".
 *
 * Read-only, so every role including `viewer` gets it; the org's account
 * list is already scoped by the server, so the picker can only ever offer
 * accounts this desk owns.
 */
export default function Performance() {
  const { orgId } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  const [weeks, setWeeks] = useState(4)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const accs = await orgApi<Account[]>(orgId, 'accounts')
        setAccounts(accs)
        const master = accs.find((a) => a.role === 'master')
        setAccountId(master?.ctid_trader_account_id ?? accs[0]?.ctid_trader_account_id ?? null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load accounts')
      }
    }
    load()
  }, [orgId])

  const loadAnalytics = useCallback(async () => {
    if (accountId == null) return
    try {
      setLoading(true)
      setError(null)
      setAnalytics(await orgApi<Analytics>(
        orgId, `accounts/${accountId}/analytics?weeks=${weeks}`))
    } catch (err) {
      setError(
        `Could not load analytics: ${err instanceof Error ? err.message : 'unknown error'}. ` +
        'The copier may be offline.')
    } finally {
      setLoading(false)
    }
  }, [orgId, accountId, weeks])

  useEffect(() => {
    loadAnalytics()
  }, [loadAnalytics])

  const a = analytics

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="page-title">Performance</h1>
        <p className="text-sm text-ink-soft mt-1">
          Realized results from the broker's own deal history — closed trades
          only; open positions live on Overview.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor="perf-account" className="desk-label block mb-1">Account</label>
          <select
            id="perf-account"
            value={accountId ?? ''}
            onChange={(e) => setAccountId(Number(e.target.value))}
            className="rounded border border-line-strong px-3 py-2 text-sm bg-card min-w-64"
          >
            {accounts.map((acc) => (
              <option key={acc.ctid_trader_account_id} value={acc.ctid_trader_account_id}>
                {accountLabel(acc)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="perf-range" className="desk-label block mb-1">Range</label>
          <select
            id="perf-range"
            value={weeks}
            onChange={(e) => setWeeks(Number(e.target.value))}
            className="rounded border border-line-strong px-3 py-2 text-sm bg-card"
          >
            {/* 12 weeks is the API's ceiling (routes/insights.py:
                MAX_ANALYTICS_WEEKS): each week is one sequential broker
                request on the wire every org shares, so the range control
                stops where the budget does. */}
            <option value={4}>4 weeks</option>
            <option value={8}>8 weeks</option>
            <option value={12}>12 weeks</option>
          </select>
        </div>
        {a?.truncated && (
          <p className="text-xs font-medium text-warn-deep">
            Some weeks had more fills than one request returns; totals may be
            slightly under-counted.
          </p>
        )}
      </div>

      {error && (
        <div role="alert" className="rounded border border-loss/30 bg-loss-wash px-4 py-3 text-sm text-loss-deep">
          {error}
        </div>
      )}

      {loading && !a ? (
        <p className="text-sm text-ink-faint py-8">Crunching deal history…</p>
      ) : !a ? null : a.closed_trades === 0 ? (
        <div className="rounded-lg border border-line bg-card px-6 py-12 text-center">
          <p className="text-ink-soft">No closed trades in this range.</p>
          <p className="text-sm text-ink-faint mt-1">
            Widen the range, or pick another account.
          </p>
        </div>
      ) : (
        <>
          {/* Headline stats */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatTile
              label="Net P&L"
              value={signed(a.net_pnl)}
              tone={a.net_pnl >= 0 ? 'profit' : 'loss'}
              sub={`${a.closed_trades} closed trades`}
            />
            <StatTile
              label="Win rate"
              value={a.win_rate != null ? `${(a.win_rate * 100).toFixed(1)}%` : '—'}
              sub={`${a.wins}W · ${a.losses}L`}
            />
            <StatTile
              label="Profit factor"
              value={a.profit_factor != null ? a.profit_factor.toFixed(2) : '—'}
              sub="gross wins ÷ gross losses"
            />
            <StatTile
              label="Best trade"
              value={signed(a.best_trade)}
              tone="profit"
            />
            <StatTile
              label="Worst trade"
              value={signed(a.worst_trade)}
              tone="loss"
            />
            <StatTile
              label="Max drawdown"
              value={signed(-Math.abs(a.max_drawdown))}
              tone="loss"
              sub={`${(a.max_drawdown_pct * 100).toFixed(1)}% from peak`}
            />
          </div>

          {/* Equity curve */}
          <section className="rounded-lg border border-line bg-card p-5">
            <h2 className="desk-label mb-3">Balance after each closed trade</h2>
            {a.equity_curve.length > 1 ? (
              <EquityCurve points={a.equity_curve} />
            ) : (
              <p className="text-sm text-ink-faint py-6">
                Not enough closed trades yet to draw a curve.
              </p>
            )}
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 *:min-w-0">
            {/* Weekly P&L */}
            <section className="rounded-lg border border-line bg-card p-5">
              <h2 className="desk-label mb-3">Weekly gross P&L</h2>
              {a.weekly.length > 0 ? (
                <PnlBars buckets={a.weekly} />
              ) : (
                <p className="text-sm text-ink-faint py-6">No weekly data.</p>
              )}
            </section>

            {/* Per-symbol */}
            <section className="rounded-lg border border-line bg-card">
              <h2 className="desk-label px-5 pt-5 pb-3">By symbol</h2>
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr className="text-left border-b border-line">
                    <th className="desk-label px-5 py-2 font-semibold">Symbol</th>
                    <th className="desk-label px-3 py-2 font-semibold text-right">Trades</th>
                    <th className="desk-label px-5 py-2 font-semibold text-right">Gross P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {a.per_symbol.map((row) => (
                    <tr key={row.symbol} className="border-b border-line last:border-0">
                      <td data-label="Symbol" className="num px-5 py-2.5">{row.symbol}</td>
                      <td data-label="Trades" className="num px-3 py-2.5 text-right">{row.trades}</td>
                      <td data-label="Gross P&L" className={`num px-5 py-2.5 text-right font-medium ${
                        row.gross_pnl >= 0 ? 'text-profit' : 'text-loss'
                      }`}>
                        {signed(row.gross_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>

          <p className="text-xs text-ink-faint">
            Net P&L includes swap and commissions; gross figures are price P&L
            only. Averages: win {signed(a.avg_win)} · loss {signed(a.avg_loss)}.
          </p>
        </>
      )}
    </div>
  )
}
