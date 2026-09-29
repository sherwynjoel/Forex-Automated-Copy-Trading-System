import { useState, useEffect, useCallback } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import type {
  Account, ApiState, OverviewStats, Settings, StateSnapshot,
} from '../lib/types'
import ConfirmDialog from '../components/ConfirmDialog'
import StatTile from '../components/StatTile'
import Banner from '../components/Banner'
import Button from '../components/Button'
import PageHeader from '../components/PageHeader'
import Loading from '../components/Loading'
import { money, signed, errorText } from '../lib/format'
import { actionBurst } from '../lib/refresh'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { useSharedSettings } from '../lib/settingsBus'
import { mergeTicksIntoSnapshot, TicksPayload } from '../lib/ticks'
import AttentionCard, { type AttentionItem } from './overview/AttentionCard'
import SetupChecklist from './overview/SetupChecklist'
import FleetGrid from './overview/FleetGrid'
import {
  ContractsPanel, CopyLogCard, FillsPanel, FleetStatusPanel, PortfolioPanel,
  accountName, type ContractRow,
} from './overview/Panels'

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
 * rendered at all and every follower tile silently omitted its equity,
 * balance and position count. `api<T>()` is an unchecked cast, so the wrong
 * type argument cost nothing at compile time, and Overview.test.tsx mocked a
 * bare `StateSnapshot` -- the wrong shape -- so the suite agreed with the bug.
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

type KpiPanel = 'portfolio' | 'accounts' | 'contracts' | 'fills'

/** How long a streamed margin call stays on the Attention card: the same
 *  30-minute window the desk strip's banner uses. */
const MARGIN_CALL_WINDOW_MS = 30 * 60_000

export default function Overview() {
  const { orgId, role, org } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [ownSettings, setSettings] = useState<Settings | null>(null)
  // The desk strip owns the kill switch; what it last fetched or saved wins
  // over this page's own load, so the copying line never goes stale.
  const sharedSettings = useSharedSettings()
  const settings = sharedSettings ?? ownSettings
  const [state, setState] = useState<StateSnapshot>({})
  const [envelope, setEnvelope] = useState<ApiState | null>(null)
  const [stats, setStats] = useState<OverviewStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  // Why the last live-state read failed; null once a read succeeds.
  const [stateError, setStateError] = useState<string | null>(null)
  const [marginCall, setMarginCall] = useState<{ accountId: number | null; at: number } | null>(null)
  const [expandedKpi, setExpandedKpi] = useState<KpiPanel | null>(null)
  const toggleKpi = (panel: KpiPanel) =>
    setExpandedKpi((cur) => (cur === panel ? null : panel))

  const [closingContract, setClosingContract] = useState<ContractRow | null>(null)
  const [closingIds, setClosingIds] = useState<Set<number>>(new Set())
  const [closingAll, setClosingAll] = useState(false)
  const [closeBusy, setCloseBusy] = useState(false)

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
      setStateError(null)
    } catch (err) {
      console.error('Failed to load state:', err)
      // A 5xx here is also how an unreachable copier shows up.
      setStateError(errorText(err, 'the copier did not answer'))
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

  // Refetch immediately when a trade event streams in (5s poll is
  // fallback); quotes ticks fold into the snapshot in place instead. A
  // margin call arrives on the same socket as a 'risk' event (the copier's
  // _on_margin_call) and is kept for the Attention card.
  useLiveRefresh(refreshState, orgId, (evt) => {
    if (evt?.category === 'risk' && evt.payload?.action === 'margin_call') {
      setMarginCall({ accountId: evt.account_id ?? null, at: Date.now() })
      return
    }
    if (evt?.category !== 'quotes') return
    const ticks = (evt.payload as TicksPayload | undefined)?.accounts
    if (!ticks) return
    setState((prev) => mergeTicksIntoSnapshot(prev, ticks))
    setEnvelope((prev) => {
      if (!prev) return prev
      const base = prev.accounts ?? {}
      const merged = mergeTicksIntoSnapshot(base, ticks)
      return merged === base ? prev : { ...prev, accounts: merged }
    })
  })

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

  const closeOne = async (row: ContractRow) => {
    await orgApi(orgId, 'positions/close', {
      method: 'POST',
      body: JSON.stringify({ account_id: row.accountId, position_id: row.pos.position_id }),
    })
  }

  const submitCloseContract = async () => {
    if (!closingContract) return
    const positionId = closingContract.pos.position_id
    setClosingIds((prev) => new Set([...prev, positionId]))
    try {
      setCloseBusy(true)
      setActionError(null)
      await closeOne(closingContract)
    } catch (err) {
      setClosingIds((prev) => {
        const next = new Set(prev)
        next.delete(positionId)
        return next
      })
      setActionError(`Close failed: ${errorText(err, 'the copier did not respond')}`)
    } finally {
      setClosingContract(null)
      setCloseBusy(false)
      await refreshState()
      actionBurst(refreshState)
    }
  }

  const submitCloseAllContracts = async (rows: ContractRow[]) => {
    try {
      setCloseBusy(true)
      setActionError(null)
      const failures: string[] = []
      // Sequential on purpose: master closes replicate to follower copies,
      // so a copy may already be gone by the time its own close is
      // attempted -- treat those errors as per-row outcomes, not a batch abort.
      for (const row of rows) {
        try {
          await closeOne(row)
        } catch (err) {
          failures.push(`${row.pos.symbol ?? row.pos.position_id}: ${errorText(err, 'failed')}`)
        }
      }
      if (failures.length) {
        setActionError(`Some contracts did not close: ${failures.join(' · ')}`)
      }
    } finally {
      setClosingAll(false)
      setCloseBusy(false)
      await refreshState()
      actionBurst(refreshState)
    }
  }

  const header = <PageHeader title="Overview" subtitle={org.name} />

  if (loading) {
    return (
      <div className="space-y-8 max-w-6xl">
        {header}
        <Loading lines={6} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-8 max-w-6xl">
        {header}
        <Banner kind="error">{error}</Banner>
      </div>
    )
  }

  const masterAccount = accounts.find((a) => a.role === 'master')
  const followers = accounts.filter((a) => a.role === 'slave')
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

  /**
   * Today's P&L across the fleet: the change in equity since yesterday,
   * summed over ONLY the accounts present on both days.
   *
   * Subtracting yesterday's total from today's total looks equivalent and
   * is not. The two sums cover whatever accounts happened to exist at each
   * moment, so an account added or removed in between lands in the answer
   * at its full balance. One org went 18 accounts -> 21 -> 10 across three
   * days as a broker disabled them and they were reconnected, and the tile
   * duly reported -275,112.83 of "P&L" -- entirely account churn, with no
   * trade behind any of it.
   *
   * Matching per account is the only honest reading: an account that was
   * not here yesterday has no yesterday to be compared against.
   */
  const yesterdayByAccount = stats?.yesterday?.equity_by_account ?? null
  const totalPnl = ((): { value: number; accounts: number; skipped: number } | null => {
    if (!yesterdayByAccount) return null
    let value = 0
    let counted = 0
    let skipped = 0
    for (const [id, snap] of Object.entries(state)) {
      const before = yesterdayByAccount[id]
      const now = snap.equity
      if (before == null || now == null) { skipped += 1; continue }
      value += now - before
      counted += 1
    }
    // Nothing comparable is not the same as no change.
    return counted > 0 ? { value, accounts: counted, skipped } : null
  })()

  // Accounts (master or follower) whose cTrader-ID token refresh has failed.
  // Once the token expires, copying for these accounts silently stops, so
  // this must be impossible to miss - not just a row in the Logs table.
  const refreshFailedAccounts = accounts.filter((a) => a.connection_status === 'refresh_failed')

  // Today's copy fills from the stats feed, newest first.
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0)
  const todaysFills = (stats?.recent_copies ?? [])
    .filter((c) => Date.parse(c.updated_at) >= midnight.getTime())
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))

  // Every running contract across the fleet, flattened from the live state
  // feed -- refreshed by the same 5s poll / websocket as the KPI row.
  const openContracts: ContractRow[] = accounts.flatMap((a) => {
    const snap = state[String(a.ctid_trader_account_id)]
    if (!snap?.positions?.length) return []
    const accountLabel = `${accountName(a)}${a.role === 'master' ? ' · master' : ''}`
    return snap.positions.map((pos) => ({
      accountId: a.ctid_trader_account_id,
      accountLabel,
      pos,
    }))
  })

  // Live P&L per master position, for estimating each active copy's P&L.
  const masterPnlByPosition = new Map<number, { pnl: number | null; volume: number }>()
  for (const pos of envelope?.master_positions ?? []) {
    masterPnlByPosition.set(pos.position_id, { pnl: pos.pnl_quote ?? null, volume: pos.volume })
  }

  // ---- Attention: only live problems, each with its action ----
  const accountsPath = `/org/${orgId}/accounts`
  const positionsPath = `/org/${orgId}/positions`
  const openAccounts = (
    <Button to={accountsPath} variant="secondary" size="sm">Open Accounts</Button>
  )
  const attention: AttentionItem[] = []
  if (stateError) {
    attention.push({
      key: 'state',
      tone: 'degraded',
      testId: 'attention-state-error',
      message: `Live figures stopped refreshing (${stateError}), so the numbers on this page may be stale.`,
      action: (
        <Button variant="secondary" size="sm" onClick={() => { void refreshState() }}>Retry</Button>
      ),
    })
  }
  if (marginCall && Date.now() - marginCall.at < MARGIN_CALL_WINDOW_MS) {
    const hit = accounts.find((a) => a.ctid_trader_account_id === marginCall.accountId)
    const who = hit ? accountName(hit)
      : marginCall.accountId != null ? `account ${marginCall.accountId}` : 'an account'
    attention.push({
      key: 'margin-call',
      tone: 'degraded',
      testId: 'attention-margin-call',
      message: `Margin call on ${who}: the broker may start force-closing positions, so reduce exposure or add funds now.`,
      action: (
        <div className="flex items-center gap-2">
          <Button to={positionsPath} variant="secondary" tone="loss" size="sm">Open Positions</Button>
          <Button variant="ghost" tone="neutral" size="sm" onClick={() => setMarginCall(null)}>Dismiss</Button>
        </div>
      ),
    })
  }
  if (refreshFailedAccounts.length > 0) {
    attention.push({
      key: 'token',
      tone: 'degraded',
      testId: 'refresh-failed-banner',
      message: (
        <>
          Token refresh failed for account{refreshFailedAccounts.length > 1 ? 's' : ''}:{' '}
          <span className="num">{refreshFailedAccounts.map((a) => a.trader_login).join(', ')}</span>.
          Copying for these accounts will stop when the token expires; reconnect via
          Accounts → Connect cTrader ID.
        </>
      ),
      action: openAccounts,
    })
  }
  // One row per account, its most severe problem only: an offline MT5
  // terminal is also marked degraded by the copier ("terminal offline
  // since ..."), and saying both would list one fault twice.
  for (const a of accounts) {
    const id = a.ctid_trader_account_id
    const isMaster = a.role === 'master'
    if (a.connection_status === 'offline') {
      attention.push({
        key: `offline-${id}`,
        tone: 'warn',
        message: `${accountName(a)}'s terminal is offline, so copies wait until the EA reports again.`,
        action: openAccounts,
      })
    } else if (a.status === 'degraded') {
      attention.push({
        key: `degraded-${id}`,
        tone: 'degraded',
        // A degraded master (authorization failure, broker refusal) stops
        // every copy, not just one follower's.
        message: isMaster
          ? 'The master account is degraded — nothing is being copied.'
          : `${accountName(a)} is degraded: copies to it are failing.`,
        action: openAccounts,
      })
    } else if (!isMaster && a.enabled && a.status === 'disconnected') {
      attention.push({
        key: `disconnected-${id}`,
        tone: 'warn',
        message: `${accountName(a)} is not connected to its broker, so it receives no copies.`,
        action: openAccounts,
      })
    }
  }
  const calmState = settings == null ? 'copier settings not loaded'
    : !settings.copying_enabled ? 'copying paused'
    : settings.dry_run ? 'dry run, copies are simulated'
    : 'copying live'

  const kpiView = (panel: KpiPanel, label: string) => (
    <Button
      variant="ghost"
      size="sm"
      className="self-start"
      aria-expanded={expandedKpi === panel}
      aria-controls={expandedKpi === panel ? `kpi-panel-${panel}` : undefined}
      onClick={() => toggleKpi(panel)}
    >
      {label}
    </Button>
  )

  const activeFollowers = stats?.active_slaves ?? followers.length
  const connectedCount = stats?.accounts_connected ?? accounts.length
  const masterCount = stats?.masters ?? (masterAccount ? 1 : 0)

  return (
    <div className="space-y-8 max-w-6xl">
      {header}

      {actionError && (
        <Banner kind="error" onDismiss={() => setActionError(null)}>{actionError}</Banner>
      )}

      {accounts.length === 0 ? (
        <SetupChecklist orgId={orgId} accounts={accounts} settings={settings} />
      ) : (
        <>
          <section aria-label="Key figures" className="space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Master equity"
                  value={money(masterState?.equity)}
                  tone="brand"
                  sub={
                    <>
                      Fleet <span className="num">{money(portfolioValue)}</span>
                      {vsYesterday != null ? (
                        <>
                          {' · '}
                          <span className={vsYesterday < 0 ? 'text-loss' : 'text-profit'}>
                            {signed(vsYesterday * 100)}% vs yesterday
                          </span>
                        </>
                      ) : ' · vs yesterday: no snapshot yet'}
                    </>
                  }
                />
                {kpiView('portfolio', 'View all accounts')}
              </div>
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Today's P&L"
                  value={totalPnl == null ? '—' : signed(totalPnl.value)}
                  tone={totalPnl == null ? undefined : (totalPnl.value < 0 ? 'loss' : 'profit')}
                  sub={stats && stats.degraded > 0
                    // A degraded account has silently stopped copying. That
                    // signal used to live on "Copied today" and must not vanish.
                    ? <span className="text-loss">{stats.degraded} degraded</span>
                    : totalPnl == null
                      ? 'needs a full day of history'
                      // Say what was counted. A total that quietly omits
                      // accounts is how the -275,112.83 went unquestioned.
                      : `${totalPnl.accounts} account${totalPnl.accounts === 1 ? '' : 's'} since yesterday`
                        + (totalPnl.skipped > 0 ? ` · ${totalPnl.skipped} too new` : '')}
                />
                {kpiView('fills', "View today's fills")}
              </div>
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Open positions"
                  value={String(openTrades)}
                  sub={
                    <>
                      <span className={`num ${totalOpenPnl < 0 ? 'text-loss' : 'text-profit'}`}>
                        {signed(totalOpenPnl)}
                      </span>
                      {' open P&L'}
                    </>
                  }
                />
                {kpiView('contracts', 'View open positions')}
              </div>
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Followers copying"
                  value={String(activeFollowers)}
                  sub={`${connectedCount} account${connectedCount === 1 ? '' : 's'} connected · ${masterCount} master`}
                />
                {kpiView('accounts', 'View fleet health')}
              </div>
            </div>

            {expandedKpi === 'portfolio' && (
              <div id="kpi-panel-portfolio">
                <PortfolioPanel orgId={orgId} accounts={accounts} state={state} />
              </div>
            )}
            {expandedKpi === 'accounts' && (
              <div id="kpi-panel-accounts">
                <FleetStatusPanel orgId={orgId} accounts={accounts} />
              </div>
            )}
            {expandedKpi === 'fills' && (
              <div id="kpi-panel-fills">
                <FillsPanel orgId={orgId} fills={todaysFills} />
              </div>
            )}
            {expandedKpi === 'contracts' && (
              <div id="kpi-panel-contracts">
                <ContractsPanel
                  orgId={orgId}
                  rows={openContracts}
                  canTrade={can(role, 'trade')}
                  closingIds={closingIds}
                  onClose={setClosingContract}
                  onCloseAll={() => setClosingAll(true)}
                />
              </div>
            )}
          </section>

          <AttentionCard items={attention} calmState={calmState} />

          <FleetGrid
            orgId={orgId}
            master={masterAccount}
            masterState={masterState}
            followers={followers}
            state={state}
            onPauseResume={handlePauseResume}
          />

          {followers.length === 0 && (
            <SetupChecklist orgId={orgId} accounts={accounts} settings={settings} />
          )}

          <CopyLogCard copies={stats?.recent_copies ?? []} masterPnlByPosition={masterPnlByPosition} />
        </>
      )}

      {/* Close one contract */}
      <ConfirmDialog
        open={closingContract != null}
        title={`Close position ${closingContract?.pos.position_id ?? ''}`}
        confirmLabel="Close position"
        danger
        busy={closeBusy}
        onConfirm={submitCloseContract}
        onCancel={() => setClosingContract(null)}
      >
        <p>
          {closingContract?.pos.side}{' '}
          <span className="num">{closingContract?.pos.symbol ?? closingContract?.pos.symbol_id}</span>{' '}
          on {closingContract?.accountLabel} closes at market.
          {closingContract?.accountLabel.includes('master') &&
            ' Closing a master position also closes its copies on every follower.'}
        </p>
      </ConfirmDialog>

      {/* Close every listed contract */}
      <ConfirmDialog
        open={closingAll}
        title="Close every open contract"
        confirmLabel={`Close ${openContracts.length} contract${openContracts.length === 1 ? '' : 's'}`}
        danger
        busy={closeBusy}
        onConfirm={() => submitCloseAllContracts(openContracts)}
        onCancel={() => setClosingAll(false)}
      >
        <p>
          Every contract listed here closes at market — master positions first
          replicate their close to their follower copies. Copying itself stays
          running; this does not pause the copier.
        </p>
      </ConfirmDialog>
    </div>
  )
}
