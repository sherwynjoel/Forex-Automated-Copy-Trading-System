import type { Account, AccountStateData, StateSnapshot } from '../../lib/types'
import Card from '../../components/Card'
import Button from '../../components/Button'
import StatusDot from '../../components/StatusDot'
import { money, signed } from '../../lib/format'
import ExpandableText from './ExpandableText'
import { accountName } from './Panels'
import { accountHealth, type HealthLevel } from './health'

const HEALTH_DOT: Record<HealthLevel, 'ok' | 'paused' | 'warn' | 'degraded'> = {
  ok: 'ok', paused: 'paused', offline: 'warn', degraded: 'degraded', disconnected: 'warn',
}

/** The master card: identity, health, balance and open P&L. Its equity is
 *  the KPI row's job, so it is not repeated here. */
function MasterCard({ orgId, master, snap }: {
  orgId: number
  master: Account
  snap: AccountStateData
}) {
  // The same reading as the Attention card, so the two never disagree.
  const health = accountHealth(master)
  const pnlTone = snap.open_pnl >= 0 ? 'text-profit' : 'text-loss'
  return (
    <Card
      as="section"
      title={`Master account (${master.trader_login})`}
      actions={master.nickname ? <span className="text-sm text-ink-soft">{master.nickname}</span> : undefined}
    >
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-1.5 text-ink">
          <StatusDot tone={HEALTH_DOT[health.level]} />
          {health.label}
        </span>
        <span className="num text-xs text-ink-faint">ID: {master.ctid_trader_account_id}</span>
      </p>
      <dl className="inset mt-4 grid grid-cols-2 gap-4 p-4">
        <div>
          <dt className="text-xs text-ink-soft">Balance</dt>
          <dd className="num mt-1 text-xl font-semibold text-ink">{money(snap.balance)}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-soft">Open P&L</dt>
          <dd className={`num mt-1 text-xl font-semibold ${pnlTone}`}>{signed(snap.open_pnl)}</dd>
        </div>
      </dl>
      <div className="mt-4 flex justify-end">
        <Button to={`/org/${orgId}/positions`} variant="ghost" size="sm">View positions</Button>
      </div>
    </Card>
  )
}

function FollowerTile({ orgId, follower, snap, onPauseResume }: {
  orgId: number
  follower: Account
  snap: AccountStateData | undefined
  onPauseResume: (accountId: number, isPaused: boolean) => void
}) {
  const isPaused = !follower.enabled
  const isDegraded = follower.status === 'degraded'
  const isRefreshFailed = follower.connection_status === 'refresh_failed'
  // An MT5 terminal that has stopped reporting: copies queue and market
  // opens expire after 30 s, so it is the same class of problem as a
  // failed token refresh.
  const isOffline = follower.connection_status === 'offline'

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
    <div data-testid="slave-tile">
      <div data-testid={`slave-tile-${follower.ctid_trader_account_id}`} className="h-full">
        <Card
          as="article"
          className="h-full"
          title={accountName(follower)}
          actions={
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-soft">
              <StatusDot tone={statusTone} />
              {statusLabel}
            </span>
          }
        >
          <p className="num text-xs text-ink-faint">
            {follower.nickname ? `${follower.trader_login} · ` : ''}ID: {follower.ctid_trader_account_id}
          </p>

          {/* Reason for degraded status - the send-failure message from the backend */}
          {isDegraded && follower.last_error && (
            <div className="mt-3 rounded-inset border border-loss/20 bg-loss-wash px-2 py-1 text-xs text-loss-deep">
              <ExpandableText text={follower.last_error} limit={40} testId="slave-last-error" />
            </div>
          )}

          {/* Connection markers - distinct from the degraded status above */}
          {isRefreshFailed && (
            <div
              data-testid="slave-refresh-failed-marker"
              className="mt-3 flex items-center gap-1.5 rounded-inset border border-warn/40 bg-warn-wash px-3 py-2 text-xs font-semibold text-warn-deep"
            >
              <StatusDot tone="warn" /> Token refresh failed — reconnect required
            </div>
          )}
          {isOffline && (
            <div
              data-testid="slave-offline-marker"
              className="mt-3 flex items-center gap-1.5 rounded-inset border border-warn/40 bg-warn-wash px-3 py-2 text-xs font-semibold text-warn-deep"
            >
              <StatusDot tone="warn" /> Terminal offline — copies wait until the EA reports again
            </div>
          )}

          {snap && (
            <dl className="inset mt-3 space-y-1.5 p-3 text-sm">
              <div className="flex justify-between">
                <dt className="text-ink-soft">Equity</dt>
                <dd className="num text-ink">{money(snap.equity)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Balance</dt>
                <dd className="num text-ink">{money(snap.balance)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Open positions</dt>
                <dd className="num text-ink">{snap.positions?.length || 0}</dd>
              </div>
            </dl>
          )}

          <div className="mt-4 flex items-center gap-2">
            <Button
              className="flex-1"
              variant={isPaused ? 'primary' : 'secondary'}
              tone={isPaused ? 'profit' : 'brand'}
              onClick={() => onPauseResume(follower.ctid_trader_account_id, isPaused)}
            >
              {isPaused ? 'Resume' : 'Pause'}
            </Button>
            <Button to={`/org/${orgId}/accounts`} variant="ghost" size="sm">
              View<span className="sr-only"> {accountName(follower)}</span>
            </Button>
          </div>
        </Card>
      </div>
    </div>
  )
}

export default function FleetGrid({ orgId, master, masterState, followers, state, onPauseResume }: {
  orgId: number
  master: Account | undefined
  masterState: AccountStateData | undefined
  followers: Account[]
  state: StateSnapshot
  onPauseResume: (accountId: number, isPaused: boolean) => void
}) {
  return (
    <div className="space-y-6">
      {master && masterState && <MasterCard orgId={orgId} master={master} snap={masterState} />}

      {followers.length > 0 && (
        <section aria-labelledby="followers-heading" className="space-y-3">
          <h2 id="followers-heading" className="text-lg font-semibold text-ink">Followers</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {followers.map((f) => (
              <FollowerTile
                key={f.ctid_trader_account_id}
                orgId={orgId}
                follower={f}
                snap={state[String(f.ctid_trader_account_id)]}
                onPauseResume={onPauseResume}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
