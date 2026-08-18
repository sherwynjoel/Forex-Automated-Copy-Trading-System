import { useState, useEffect } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import type { Account, ApiState, Settings, StateSnapshot } from '../lib/types'
import KillSwitch from '../components/KillSwitch'
import { useLiveRefresh } from '../hooks/useLiveRefresh'

/**
 * Fetch GET orgs/{orgId}/state and return just its per-account block.
 *
 * `orgs/{orgId}/state` is a verbatim pass-through of the copier's
 * `get_state()`, so its shape is `{accounts, master_positions,
 * pending_orders, drift}` -- the per-account balance/equity/positions live
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
async function loadAccountState(orgId: number): Promise<StateSnapshot> {
  const state = await orgApi<ApiState>(orgId, 'state')
  return state.accounts ?? {}
}

export default function Overview() {
  const { orgId } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [state, setState] = useState<StateSnapshot>({})
  const [loading, setLoading] = useState(true)
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
        setError(String(err))
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [orgId])

  // Poll state every 5 seconds
  useEffect(() => {
    const loadState = async () => {
      try {
        setState(await loadAccountState(orgId))
      } catch (err) {
        console.error('Failed to load state:', err)
      }
    }

    loadState()
    const interval = setInterval(loadState, 5000)
    return () => clearInterval(interval)
  }, [orgId])

  // Refetch immediately when a trade event streams in (5s poll is fallback)
  useLiveRefresh(async () => {
    try {
      setState(await loadAccountState(orgId))
    } catch (err) {
      console.error('Failed to load state:', err)
    }
  }, orgId)

  const handlePauseResume = async (accountId: number, isPaused: boolean) => {
    try {
      const endpoint = isPaused ? 'control/resume' : 'control/pause'
      await orgApi(orgId, endpoint, {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      })
      setState(await loadAccountState(orgId))
    } catch (err) {
      console.error('Failed to update account status:', err)
    }
  }

  const handleSettingsUpdate = (newSettings: Settings) => {
    setSettings(newSettings)
  }

  if (loading) {
    return <div className="text-center py-12 text-ink-faint">Loading...</div>
  }

  if (error) {
    return <div className="text-loss">Error: {error}</div>
  }

  const masterAccount = accounts.find((a) => a.role === 'master')
  const slaveAccounts = accounts.filter((a) => a.role === 'slave')

  const masterState = masterAccount ? state[String(masterAccount.ctid_trader_account_id)] : undefined

  // Accounts (master or slave) whose cTrader-ID token refresh has failed. Once the
  // token expires, copying for these accounts silently stops, so this must be
  // impossible to miss on the dashboard - not just a row in the Logs table.
  const refreshFailedAccounts = accounts.filter((a) => a.connection_status === 'refresh_failed')

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Token Refresh Failure Alert */}
      {refreshFailedAccounts.length > 0 && (
        <div
          data-testid="refresh-failed-banner"
          role="alert"
          className="bg-loss text-white p-4 rounded-lg flex items-start gap-3"
        >
          <span className="text-2xl leading-none" aria-hidden="true">⚠️</span>
          <div>
            <p className="font-bold">
              Token refresh failed for account{refreshFailedAccounts.length > 1 ? 's' : ''}:{' '}
              {refreshFailedAccounts.map((a) => a.trader_login).join(', ')}
            </p>
            <p className="text-sm mt-1 text-white/80">
              Copying for these accounts will stop when the token expires. Reconnect via
              Accounts &rarr; Connect cTrader ID.
            </p>
          </div>
        </div>
      )}

      {/* Kill Switch and Status Bar */}
      <div className="bg-card p-6 rounded-lg border border-line flex items-center justify-between flex-wrap gap-4">
        <div>
          <h2 className="font-display text-lg text-ink">Copying Status</h2>
          <p className="text-sm text-ink-soft mt-1">
            {settings?.copying_enabled ? 'Actively copying trades' : 'Copying paused'}
          </p>
        </div>
        {settings && <KillSwitch settings={settings} onUpdate={handleSettingsUpdate} />}
      </div>

      {/* Master Card */}
      {masterAccount && masterState && (
        <div className="bg-card p-6 rounded-lg border border-line">
          <div className="flex items-baseline justify-between mb-5">
            <h3 className="font-display text-xl text-ink">
              Master Account ({masterAccount.trader_login})
            </h3>
            {masterAccount.nickname && (
              <span className="text-sm text-ink-soft">{masterAccount.nickname}</span>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="rounded border border-line bg-paper p-4">
              <div className="desk-label">Equity</div>
              <div className="num text-2xl mt-1 text-brand">${masterState.equity?.toFixed(2)}</div>
            </div>
            <div className="rounded border border-line bg-paper p-4">
              <div className="desk-label">Balance</div>
              <div className="num text-2xl mt-1 text-ink">${masterState.balance?.toFixed(2)}</div>
            </div>
            <div className="rounded border border-line bg-paper p-4">
              <div className={`desk-label ${masterState.open_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                Open P&L
              </div>
              <div className={`num text-2xl mt-1 ${masterState.open_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                ${masterState.open_pnl?.toFixed(2)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Slave Grid */}
      {slaveAccounts.length > 0 && (
        <div>
          <h3 className="font-display text-xl text-ink mb-4">Slave Accounts</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {slaveAccounts.map((slave) => {
              const slaveState = state[String(slave.ctid_trader_account_id)]
              const isPaused = !slave.enabled
              const isDegraded = slave.status === 'degraded'
              const isRefreshFailed = slave.connection_status === 'refresh_failed'

              let statusIcon = '🟢'
              let statusLabel = 'OK'
              if (isPaused) {
                statusIcon = '⏸'
                statusLabel = 'Paused'
              } else if (isDegraded) {
                statusIcon = '🔴'
                statusLabel = 'Degraded'
              }

              return (
                <div
                  key={slave.ctid_trader_account_id}
                  data-testid="slave-tile"
                  className={`bg-card p-5 rounded-lg border transition-shadow hover:shadow-md ${
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
                    <div className="text-center">
                      <div className="text-2xl">{statusIcon}</div>
                      <p className="desk-label mt-0.5">{statusLabel}</p>
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
                      className="mb-4 px-3 py-2 bg-warn-wash border border-warn/40 text-warn text-xs font-semibold rounded flex items-center gap-1"
                    >
                      <span aria-hidden="true">🔑</span> Token Refresh Failed - reconnect required
                    </div>
                  )}

                  {/* Stats */}
                  {slaveState && (
                    <div className="space-y-1.5 mb-4 text-sm">
                      <div className="flex justify-between">
                        <span className="text-ink-soft">Equity:</span>
                        <span className="num text-ink">${slaveState.equity?.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-ink-soft">Balance:</span>
                        <span className="num text-ink">${slaveState.balance?.toFixed(2)}</span>
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
                    className={`w-full py-2 px-4 rounded text-sm font-semibold text-white transition-colors ${
                      isPaused
                        ? 'bg-profit hover:bg-brand-deep'
                        : 'bg-warn hover:opacity-90'
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
