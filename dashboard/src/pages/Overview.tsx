import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import type { Account, ApiState, Settings, StateSnapshot } from '../lib/types'
import KillSwitch from '../components/KillSwitch'
import { useLiveRefresh } from '../hooks/useLiveRefresh'

/**
 * Fetch GET /api/state and return just its per-account block.
 *
 * `/api/state` is a verbatim pass-through of the copier's `get_state()`, so
 * its shape is `{accounts, master_positions, pending_orders, drift}` -- the
 * per-account balance/equity/positions live under `accounts`, keyed by
 * account id as a STRING (JSON has no integer keys).
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
async function loadAccountState(): Promise<StateSnapshot> {
  const state = await api<ApiState>('/api/state')
  return state.accounts ?? {}
}

export default function Overview() {
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
          api<Account[]>('/api/accounts'),
          api<Settings>('/api/settings'),
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
  }, [])

  // Poll state every 5 seconds
  useEffect(() => {
    const loadState = async () => {
      try {
        setState(await loadAccountState())
      } catch (err) {
        console.error('Failed to load state:', err)
      }
    }

    // Load immediately
    loadState()

    // Poll every 5 seconds
    const interval = setInterval(loadState, 5000)
    return () => clearInterval(interval)
  }, [])

  // Refetch immediately when a trade event streams in (5s poll is fallback)
  useLiveRefresh(async () => {
    try {
      setState(await loadAccountState())
    } catch (err) {
      console.error('Failed to load state:', err)
    }
  })

  const handlePauseResume = async (accountId: number, isPaused: boolean) => {
    try {
      const endpoint = isPaused ? '/api/control/resume' : '/api/control/pause'
      await api(endpoint, {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      })
      // Reload state after action
      setState(await loadAccountState())
    } catch (err) {
      console.error('Failed to update account status:', err)
    }
  }

  const handleSettingsUpdate = (newSettings: Settings) => {
    setSettings(newSettings)
  }

  if (loading) {
    return <div className="text-center py-12">Loading...</div>
  }

  if (error) {
    return <div className="text-red-600">Error: {error}</div>
  }

  // Find master account
  const masterAccount = accounts.find((a) => a.role === 'master')
  const slaveAccounts = accounts.filter((a) => a.role === 'slave')

  const masterState = masterAccount ? state[String(masterAccount.ctid_trader_account_id)] : undefined

  // Accounts (master or slave) whose cTrader-ID token refresh has failed. Once the
  // token expires, copying for these accounts silently stops, so this must be
  // impossible to miss on the dashboard - not just a row in the Logs table.
  const refreshFailedAccounts = accounts.filter((a) => a.connection_status === 'refresh_failed')

  return (
    <div className="space-y-6">
      {/* Token Refresh Failure Alert */}
      {refreshFailedAccounts.length > 0 && (
        <div
          data-testid="refresh-failed-banner"
          role="alert"
          className="bg-red-600 text-white p-4 rounded-lg shadow-md border-2 border-red-800 flex items-start gap-3"
        >
          <span className="text-2xl leading-none" aria-hidden="true">⚠️</span>
          <div>
            <p className="font-bold">
              Token refresh failed for account{refreshFailedAccounts.length > 1 ? 's' : ''}:{' '}
              {refreshFailedAccounts.map((a) => a.trader_login).join(', ')}
            </p>
            <p className="text-sm mt-1 text-red-50">
              Copying for these accounts will stop when the token expires. Reconnect via
              Accounts &rarr; Connect cTrader ID.
            </p>
          </div>
        </div>
      )}

      {/* Kill Switch and Status Bar */}
      <div className="bg-white p-6 rounded-lg shadow-md flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Copying Status</h2>
          <p className="text-sm text-gray-600 mt-1">
            {settings?.copying_enabled ? 'Actively copying trades' : 'Copying paused'}
          </p>
        </div>
        {settings && <KillSwitch settings={settings} onUpdate={handleSettingsUpdate} />}
      </div>

      {/* Master Card */}
      {masterAccount && masterState && (
        <div className="bg-gradient-to-br from-blue-50 to-blue-100 p-8 rounded-lg shadow-md border-2 border-blue-200">
          <h3 className="text-2xl font-bold text-gray-900 mb-6">Master Account ({masterAccount.trader_login})</h3>
          <div className="grid grid-cols-3 gap-6">
            <div className="bg-white p-4 rounded-lg shadow">
              <div className="text-sm text-gray-600 font-medium">Equity</div>
              <div className="text-3xl font-bold text-blue-600">${masterState.equity?.toFixed(2)}</div>
            </div>
            <div className="bg-white p-4 rounded-lg shadow">
              <div className="text-sm text-gray-600 font-medium">Balance</div>
              <div className="text-3xl font-bold text-gray-900">${masterState.balance?.toFixed(2)}</div>
            </div>
            <div className="bg-white p-4 rounded-lg shadow">
              <div className={`text-sm font-medium ${masterState.open_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                Open P&L
              </div>
              <div
                className={`text-3xl font-bold ${masterState.open_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}
              >
                ${masterState.open_pnl?.toFixed(2)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Slave Grid */}
      {slaveAccounts.length > 0 && (
        <div>
          <h3 className="text-xl font-bold text-gray-900 mb-4">Slave Accounts</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {slaveAccounts.map((slave) => {
              const slaveState = state[String(slave.ctid_trader_account_id)]
              const isPaused = !slave.enabled
              const isDegraded = slave.status === 'degraded'
              const isRefreshFailed = slave.connection_status === 'refresh_failed'

              // Status icon
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
                  className={`bg-white p-6 rounded-lg shadow-md hover:shadow-lg transition-shadow ${
                    isRefreshFailed ? 'border-2 border-orange-500' : ''
                  }`}
                >
                  {/* Header with status */}
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h4 className="font-semibold text-gray-900">Account {slave.trader_login}</h4>
                      <p className="text-sm text-gray-500">ID: {slave.ctid_trader_account_id}</p>
                    </div>
                    <div className="text-center">
                      <div className="text-3xl">{statusIcon}</div>
                      <p className="text-xs text-gray-600 font-medium">{statusLabel}</p>
                    </div>
                  </div>

                  {/* Reason for degraded status - the send-failure message from the backend */}
                  {isDegraded && slave.last_error && (
                    <p
                      data-testid="slave-last-error"
                      title={slave.last_error}
                      className="mb-4 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1 truncate"
                    >
                      {slave.last_error}
                    </p>
                  )}

                  {/* Refresh-failed marker - distinct from the degraded badge above */}
                  {isRefreshFailed && (
                    <div
                      data-testid="slave-refresh-failed-marker"
                      className="mb-4 px-3 py-2 bg-orange-100 border border-orange-400 text-orange-800 text-xs font-semibold rounded flex items-center gap-1"
                    >
                      <span aria-hidden="true">🔑</span> Token Refresh Failed - reconnect required
                    </div>
                  )}

                  {/* Stats */}
                  {slaveState && (
                    <div className="space-y-2 mb-4 text-sm">
                      <div className="flex justify-between">
                        <span className="text-gray-600">Equity:</span>
                        <span className="font-semibold text-gray-900">${slaveState.equity?.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-600">Balance:</span>
                        <span className="font-semibold text-gray-900">${slaveState.balance?.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-600">Open Positions:</span>
                        <span className="font-semibold text-gray-900">{slaveState.positions?.length || 0}</span>
                      </div>
                    </div>
                  )}

                  {/* Action Button */}
                  <button
                    onClick={() => handlePauseResume(slave.ctid_trader_account_id, isPaused)}
                    className={`w-full py-2 px-4 rounded font-medium text-white transition-colors ${
                      isPaused
                        ? 'bg-green-500 hover:bg-green-600'
                        : 'bg-orange-500 hover:bg-orange-600'
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
        <div className="text-center py-12 text-gray-500">No slave accounts configured</div>
      )}
    </div>
  )
}
