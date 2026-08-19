import { useState, useEffect, useCallback } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import type {
  Account, Analytics, ApiState, OverviewStats, Settings, StateSnapshot,
} from '../lib/types'
import KillSwitch from '../components/KillSwitch'
import StatTile from '../components/StatTile'
import StatusDot from '../components/StatusDot'
import Banner from '../components/Banner'
import { Sparkline, WinRateMeter, TradeBar } from '../components/charts'
import { money, signed, errorText } from '../lib/format'
import { useLiveRefresh } from '../hooks/useLiveRefresh'

/**
 * Fetch GET orgs/{orgId}/state once and hand back both the envelope and its
 * per-account block.
 *
 * `orgs/{orgId}/state` is a verbatim pass-through of the copier's
 * `get_state()` for that org -- `{accounts, master_positions,
 * pending_orders, drift}` -- with per-account balance/equity/positions
 * under `accounts`, keyed by account id as a STRING (JSON has no integer
 * keys).
 *
 * This screen used to do `api<StateSnapshot>('/api/state')` and index the
 * result directly, i.e. it read the envelope as if it were the account map:
 * every `state[String(accountId)]` was `undefined`, so the master card never
 * rendered at all and every slave tile silently omitted its equity, balance
 * and position count. `api<T>()` is an unchecked cast, so the wrong type
 * argument cost nothing at compile time, and Overview.test.tsx mocked a bare
 * `StateSnapshot` -- the wrong shape -- so the suite agreed with the bug.
 *
 * Typing the fetch as `ApiState` and projecting explicitly here is what makes
 * a future shape change a type error rather than a blank screen; the tests
 * now mock the real envelope and assert the numbers actually render.
 */
async function loadState(
  orgId: number,
): Promise<{ accounts: StateSnapshot; envelope: ApiState }> {
  const envelope = await orgApi<ApiState>(orgId, 'state')
  return { accounts: envelope.accounts ?? {}, envelope }
}

// Shared desk primitives: one tile, one banner, one formatter set app-wide.

export default function Overview() {
  const { orgId } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [state, setState] = useState<StateSnapshot>({})
  const [envelope, setEnvelope] = useState<ApiState | null>(null)
  const [stats, setStats] = useState<OverviewStats | null>(null)
  const [copierPerf, setCopierPerf] = useState<Analytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionError, setActionError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Load accounts and settings on mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true)
        setError(null)
        const [accs, sett] = await Promise.all([
          orgApi<Account[]>(orgId, 'accounts'),
          orgApi<Settings>(orgId, 'settings'),
        ])
        setAccounts(accs)
        setSettings(sett)
      } catch (err) {
        setError(errorText(err, 'Failed to load the overview'))
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [orgId])

  const refreshState = useCallback(async () => {
    try {
      const { accounts: snapshot, envelope: env } = await loadState(orgId)
      setState(snapshot)
      setEnvelope(env)
    } catch (err) {
      console.error('Failed to load state:', err)
    }
    // The DB-side stats and copier performance are passengers: if they
    // fail (older api, copier offline) the live sections still render.
    try {
      setStats(await orgApi<OverviewStats>(orgId, 'overview'))
    } catch {
      /* stats sections show placeholders */
    }
  }, [orgId])

  useEffect(() => {
    refreshState()
    const interval = setInterval(refreshState, 5000)
    return () => clearInterval(interval)
  }, [refreshState])

  // Refetch immediately when a trade event streams in (5s poll is fallback)
  useLiveRefresh(refreshState, orgId)

  // This desk's copier performance = ITS master's realized results (that is
  // what its slaves mirror). Fetched once per mount; heavier than /state.
  useEffect(() => {
    const master = accounts.find((a) => a.role === 'master')
    if (!master) return
    let cancelled = false
    orgApi<Analytics>(
      orgId, `accounts/${master.ctid_trader_account_id}/analytics?weeks=4`)
      .then((a) => { if (!cancelled) setCopierPerf(a) })
      .catch(() => { /* strip shows placeholders */ })
    return () => { cancelled = true }
  }, [orgId, accounts])

  const handlePauseResume = async (accountId: number, isPaused: boolean) => {
    try {
      setActionError(null)
      const endpoint = isPaused ? 'control/resume' : 'control/pause'
      await orgApi(orgId, endpoint, {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      })
      await refreshState()
    } catch (err) {
      setActionError(
        `${isPaused ? 'Resume' : 'Pause'} failed: ${errorText(err, 'the copier did not respond')}`)
    }
  }

  const handleSettingsUpdate = (newSettings: Settings) => {
    setSettings(newSettings)
  }

  if (loading) {
    return <div className="text-center py-12 text-ink-faint">Loading...</div>
  }

  if (error) {
    return <Banner kind="error">{error}</Banner>
  }

  const masterAccount = accounts.find((a) => a.role === 'master')
  const slaveAccounts = accounts.filter((a) => a.role === 'slave')
  const masterState = masterAccount ? state[String(masterAccount.ctid_trader_account_id)] : undefined

  // Portfolio aggregates across this ORG's accounts -- `state` is the
  // per-account block of GET orgs/{orgId}/state, which the copier already
  // restricts to the org's own accounts.
  const accountStates = Object.values(state)
  const equityValues = accountStates.map((s) => s.equity).filter((v): v is number => v != null)
  const portfolioValue = equityValues.length
    ? equityValues.reduce((a, b) => a + b, 0)
    : null
  const totalOpenPnl = accountStates.reduce((a, s) => a + (s.open_pnl ?? 0), 0)
  const openTrades = accountStates.reduce((a, s) => a + (s.positions?.length ?? 0), 0)

  const yesterdayEquity = stats?.yesterday?.total_equity ?? null
  const vsYesterday = (portfolioValue != null && yesterdayEquity)
    ? (portfolioValue - yesterdayEquity) / yesterdayEquity
    : null

  // Accounts (master or slave) whose cTrader-ID token refresh has failed. Once the
  // token expires, copying for these accounts silently stops, so this must be
  // impossible to miss on the dashboard - not just a row in the Logs table.
  const refreshFailedAccounts = accounts.filter((a) => a.connection_status === 'refresh_failed')

  // Live P&L per master position, for estimating each active copy's P&L.
  const masterPnlByPosition = new Map<number, { pnl: number | null; volume: number }>()
  for (const pos of envelope?.master_positions ?? []) {
    masterPnlByPosition.set(pos.position_id, { pnl: pos.pnl_quote ?? null, volume: pos.volume })
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="page-title">Overview</h1>
      </header>

      {actionError && (
        <Banner kind="error" onDismiss={() => setActionError(null)}>{actionError}</Banner>
      )}

      {/* Token Refresh Failure Alert */}
      {refreshFailedAccounts.length > 0 && (
        <div
          data-testid="refresh-failed-banner"
          role="alert"
          className="bg-loss text-white p-4 rounded-lg flex items-start gap-3"
        >
          <svg aria-hidden="true" viewBox="0 0 20 20" className="h-6 w-6 shrink-0 mt-0.5">
            <path d="M10 2 1.5 17h17L10 2z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
            <rect x="9.25" y="8" width="1.5" height="4.5" rx="0.75" fill="currentColor" />
            <circle cx="10" cy="14.6" r="0.9" fill="currentColor" />
          </svg>
          <div>
            <p className="font-bold">
              Token refresh failed for account{refreshFailedAccounts.length > 1 ? 's' : ''}:{' '}
              {refreshFailedAccounts.map((a) => a.trader_login).join(', ')}
            </p>
            <p className="text-sm mt-1 text-white">
              Copying for these accounts will stop when the token expires. Reconnect via
              Accounts &rarr; Connect cTrader ID.
            </p>
          </div>
        </div>
      )}

      {/* Portfolio stat row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile
          label="Portfolio value"
          value={money(portfolioValue)}
          tone="brand"
          sub={vsYesterday != null ? (
            <span className={vsYesterday < 0 ? 'text-loss' : 'text-profit'}>
              {signed(vsYesterday * 100)}% vs yesterday
            </span>
          ) : 'vs yesterday: no snapshot yet'}
        />
        <StatTile
          label="Accounts connected"
          value={String(stats?.accounts_connected ?? accounts.length)}
          sub={`${stats?.masters ?? (masterAccount ? 1 : 0)} master · ${stats?.active_slaves ?? slaveAccounts.length} active slaves`}
        />
        <StatTile
          label="Open P&L"
          value={signed(totalOpenPnl)}
          tone={totalOpenPnl < 0 ? 'loss' : 'profit'}
          sub={`${openTrades} open trade${openTrades === 1 ? '' : 's'}`}
        />
        <StatTile
          label="Copied today"
          value={String(stats?.copied_today ?? '—')}
          sub={stats && stats.degraded > 0
            ? <span className="text-loss">{stats.degraded} degraded</span>
            : 'copy fills since midnight'}
        />
      </div>

      {/* Copier performance strip (master's realized results, last 4 weeks) */}
      <section className="rounded-lg border border-line bg-card p-4">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="desk-label">Copier performance · last 4 weeks</h2>
          {copierPerf && (
            <span className="text-xs text-ink-faint">
              {copierPerf.closed_trades} trades taken · {copierPerf.wins} won · {copierPerf.losses} lost
            </span>
          )}
        </div>
        {!copierPerf ? (
          <p className="text-sm text-ink-faint">
            Performance loads once the copier has served deal history.
          </p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <div className="desk-label">Net P&L</div>
              <div className={`num text-xl mt-0.5 ${copierPerf.net_pnl < 0 ? 'text-loss' : 'text-profit'}`}>
                {signed(copierPerf.net_pnl)}
              </div>
              <div data-chart="perf-sparkline" className="mt-2">
                <Sparkline
                  points={copierPerf.equity_curve}
                  label={`Balance trend across ${copierPerf.closed_trades} closed trades`}
                />
              </div>
            </div>
            <div>
              <div className="desk-label">Win rate</div>
              <div className="num text-xl mt-0.5 text-ink">
                {copierPerf.win_rate != null ? `${(copierPerf.win_rate * 100).toFixed(1)}%` : '—'}
              </div>
              <WinRateMeter wins={copierPerf.wins} losses={copierPerf.losses} />
            </div>
            <div>
              <div className="desk-label">Best trade</div>
              <div className="num text-xl mt-0.5 text-profit">{signed(copierPerf.best_trade)}</div>
              <TradeBar
                value={copierPerf.best_trade}
                max={Math.max(Math.abs(copierPerf.best_trade ?? 0), Math.abs(copierPerf.worst_trade ?? 0))}
                tone="profit"
              />
            </div>
            <div>
              <div className="desk-label">Worst trade</div>
              <div className="num text-xl mt-0.5 text-loss">{signed(copierPerf.worst_trade)}</div>
              <TradeBar
                value={copierPerf.worst_trade}
                max={Math.max(Math.abs(copierPerf.best_trade ?? 0), Math.abs(copierPerf.worst_trade ?? 0))}
                tone="loss"
              />
            </div>
          </div>
        )}
      </section>

      {/* Kill Switch and Status Bar */}
      <div className="bg-card p-6 rounded-lg border border-line flex items-center justify-between flex-wrap gap-4">
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">Copying Status</h2>
          <p className="text-sm text-ink-soft mt-1">
            {settings?.copying_enabled ? 'Actively copying trades' : 'Copying paused'}
          </p>
        </div>
        {settings && <KillSwitch settings={settings} onUpdate={handleSettingsUpdate} />}
      </div>

      {/* Copy log */}
      <section className="rounded-lg border border-line bg-card">
        <h2 className="desk-label px-5 pt-4 pb-3">Copy log</h2>
        {!stats || stats.recent_copies.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-ink-faint">
            No copies yet. They appear here the moment the master trades.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Status</th>
                  <th className="desk-label px-3 py-2 font-semibold">Master</th>
                  <th className="desk-label px-3 py-2 font-semibold">Slave</th>
                  <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {stats.recent_copies.map((copy, i) => {
                  const master = copy.master_position_id != null
                    ? masterPnlByPosition.get(copy.master_position_id)
                    : undefined
                  // Estimate the copy's live P&L from the master position's,
                  // scaled by the volume ratio. Only possible while both
                  // sides are open; otherwise show the failure or a dash.
                  let pnl: number | null = null
                  if (copy.status === 'active' && master?.pnl != null && master.volume > 0
                      && copy.slave_volume != null) {
                    pnl = master.pnl * (copy.slave_volume / master.volume)
                  }
                  return (
                    <tr key={`${copy.slave_account_id}-${copy.master_position_id}-${copy.master_order_id}-${i}`}
                        className="border-b border-line last:border-0">
                      <td data-label="Status" className="px-5 py-2.5">
                        <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                          copy.status === 'active' ? 'bg-profit-wash text-profit-deep'
                          : copy.status === 'failed' ? 'bg-loss-wash text-loss-deep'
                          : copy.status === 'closed' ? 'bg-line text-ink-soft'
                          : 'bg-warn-wash text-warn-deep'
                        }`}>
                          {copy.status}
                        </span>
                      </td>
                      <td data-label="Master" className="tnum px-3 py-2.5 text-ink-soft">
                        {copy.master_position_id ?? copy.master_order_id ?? '—'}
                      </td>
                      <td data-label="Slave" className="px-3 py-2.5">
                        {copy.slave_nickname || <span className="tnum">{copy.slave_login}</span>}
                      </td>
                      <td data-label="Symbol" className="tnum px-3 py-2.5">{copy.symbol ?? '—'}</td>
                      <td data-label="P&L" className="tnum px-5 py-2.5 text-right">
                        {copy.status === 'failed' && copy.error ? (
                          <span className="text-xs text-loss" title={copy.error}>
                            {copy.error.length > 24 ? copy.error.slice(0, 24) + '…' : copy.error}
                          </span>
                        ) : pnl != null ? (
                          <span className={pnl < 0 ? 'text-loss' : 'text-profit'}>{signed(pnl)}</span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Master Card */}
      {masterAccount && masterState && (
        <div className="bg-card p-6 rounded-lg border border-line">
          <div className="flex items-baseline justify-between mb-5">
            <h3 className="font-display text-xl font-semibold text-ink">
              Master Account ({masterAccount.trader_login})
            </h3>
            {masterAccount.nickname && (
              <span className="text-sm text-ink-soft">{masterAccount.nickname}</span>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="rounded border border-line bg-paper p-4">
              <div className="desk-label">Equity</div>
              <div className="num text-2xl font-semibold mt-1 text-brand">{money(masterState.equity)}</div>
            </div>
            <div className="rounded border border-line bg-paper p-4">
              <div className="desk-label">Balance</div>
              <div className="num text-2xl font-semibold mt-1 text-ink">{money(masterState.balance)}</div>
            </div>
            <div className="rounded border border-line bg-paper p-4">
              <div className={`desk-label ${masterState.open_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                Open P&L
              </div>
              <div className={`num text-2xl font-semibold mt-1 ${masterState.open_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {signed(masterState.open_pnl)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Slave Grid */}
      {slaveAccounts.length > 0 && (
        <div>
          <h3 className="font-display text-xl font-semibold text-ink mb-4">Slave Accounts</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {slaveAccounts.map((slave) => {
              const slaveState = state[String(slave.ctid_trader_account_id)]
              const isPaused = !slave.enabled
              const isDegraded = slave.status === 'degraded'
              const isRefreshFailed = slave.connection_status === 'refresh_failed'

              let statusTone: 'ok' | 'paused' | 'degraded' = 'ok'
              let statusLabel = 'OK'
              if (isPaused) {
                statusTone = 'paused'
                statusLabel = 'Paused'
              } else if (isDegraded) {
                statusTone = 'degraded'
                statusLabel = 'Degraded'
              }

              return (
                <div
                  key={slave.ctid_trader_account_id}
                  data-testid="slave-tile"
                  className={`bg-card p-5 rounded-lg border transition-colors hover:border-line-strong ${
                    isRefreshFailed ? 'border-warn' : 'border-line'
                  }`}
                >
                  {/* Header with status */}
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h4 className="font-semibold text-ink">
                        {slave.nickname || `Account ${slave.trader_login}`}
                      </h4>
                      <p className="num text-xs text-ink-faint mt-0.5">
                        {slave.nickname ? `${slave.trader_login} · ` : ''}ID: {slave.ctid_trader_account_id}
                      </p>
                    </div>
                    <div className="flex flex-col items-center gap-1">
                      <StatusDot tone={statusTone} />
                      <p className="desk-label">{statusLabel}</p>
                    </div>
                  </div>

                  {/* Reason for degraded status - the send-failure message from the backend */}
                  {isDegraded && slave.last_error && (
                    <p
                      data-testid="slave-last-error"
                      title={slave.last_error}
                      className="mb-4 text-xs text-loss-deep bg-loss-wash border border-loss/20 rounded px-2 py-1 truncate"
                    >
                      {slave.last_error}
                    </p>
                  )}

                  {/* Refresh-failed marker - distinct from the degraded badge above */}
                  {isRefreshFailed && (
                    <div
                      data-testid="slave-refresh-failed-marker"
                      className="mb-4 px-3 py-2 bg-warn-wash border border-warn/40 text-warn-deep text-xs font-semibold rounded flex items-center gap-1.5"
                    >
                      <StatusDot tone="warn" /> Token refresh failed — reconnect required
                    </div>
                  )}

                  {/* Stats */}
                  {slaveState && (
                    <div className="space-y-1.5 mb-4 text-sm">
                      <div className="flex justify-between">
                        <span className="text-ink-soft">Equity:</span>
                        <span className="num text-ink">{money(slaveState.equity)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-ink-soft">Balance:</span>
                        <span className="num text-ink">{money(slaveState.balance)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-ink-soft">Open Positions:</span>
                        <span className="num text-ink">{slaveState.positions?.length || 0}</span>
                      </div>
                    </div>
                  )}

                  {/* Action Button */}
                  <button
                    onClick={() => handlePauseResume(slave.ctid_trader_account_id, isPaused)}
                    className={`w-full py-2 px-4 rounded text-sm font-semibold transition-colors ${
                      isPaused
                        ? 'bg-profit text-white hover:bg-profit-deep'
                        : 'border border-brand text-brand hover:bg-brand-wash hover:text-brand-deep'
                    }`}
                  >
                    {isPaused ? 'Resume' : 'Pause'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {slaveAccounts.length === 0 && (
        <div className="text-center py-12 text-ink-faint">No slave accounts configured</div>
      )}
    </div>
  )
}
