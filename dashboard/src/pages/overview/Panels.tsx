import type { ReactNode } from 'react'
import type { Account, PositionData, RecentCopy, StateSnapshot } from '../../lib/types'
import Card from '../../components/Card'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import StatusDot from '../../components/StatusDot'
import { money, signed, formatWhen } from '../../lib/format'
import ExpandableText from './ExpandableText'

/** One running contract, flattened from the live state feed. */
export type ContractRow = { accountId: number; accountLabel: string; pos: PositionData }

export function accountName(a: Pick<Account, 'nickname' | 'trader_login'>): string {
  return a.nickname || `Account ${a.trader_login}`
}

/** The API keeps its role names ('master' | 'slave'); the desk says follower. */
export function roleWord(role: string): string {
  return role === 'master' ? 'Master' : 'Follower'
}

/** The one chip's tone for a copy's status, shared by the fills panel and the copy log. */
export function copyStatusTone(status: string): 'profit' | 'loss' | 'neutral' | 'warn' {
  if (status === 'active') return 'profit'
  if (status === 'failed') return 'loss'
  if (status === 'closed') return 'neutral'
  return 'warn'
}

/** 'active' -> 'Active': badges show words, not raw enum strings. */
function humanStatus(status: string): string {
  return status ? status.charAt(0).toUpperCase() + status.slice(1) : '—'
}

function PanelLink({ to, children }: { to: string; children: ReactNode }) {
  return <Button to={to} variant="ghost" size="sm">{children}</Button>
}

/** Per-account balance/equity/open P&L, opened from the Master equity tile. */
export function PortfolioPanel({ orgId, accounts, state }: {
  orgId: number
  accounts: Account[]
  state: StateSnapshot
}) {
  return (
    <Card
      as="section"
      inset
      title="Portfolio breakdown · live"
      actions={<PanelLink to={`/org/${orgId}/accounts`}>Manage accounts</PanelLink>}
    >
      <div className="overflow-x-auto">
        <table className="stack-table w-full text-sm">
          <thead>
            <tr className="text-left border-b border-line">
              <th className="desk-label px-5 py-2 font-semibold">Account</th>
              <th className="desk-label px-3 py-2 font-semibold">Role</th>
              <th className="desk-label px-3 py-2 font-semibold text-right">Balance</th>
              <th className="desk-label px-3 py-2 font-semibold text-right">Equity</th>
              <th className="desk-label px-5 py-2 font-semibold text-right">Open P&L</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              const snap = state[String(a.ctid_trader_account_id)]
              return (
                <tr key={a.ctid_trader_account_id} className="border-b border-line last:border-0">
                  <td data-label="Account" className="px-5 py-2.5">{accountName(a)}</td>
                  <td data-label="Role" className="px-3 py-2.5">
                    <Badge tone={a.role === 'master' ? 'profit' : 'neutral'}>{roleWord(a.role)}</Badge>
                  </td>
                  <td data-label="Balance" className="tnum px-3 py-2.5 text-right">{money(snap?.balance)}</td>
                  <td data-label="Equity" className="tnum px-3 py-2.5 text-right">{money(snap?.equity)}</td>
                  <td data-label="Open P&L" className="tnum px-5 py-2.5 text-right">
                    <span className={(snap?.open_pnl ?? 0) < 0 ? 'text-loss' : 'text-profit'}>
                      {signed(snap?.open_pnl)}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/** Health and copying state per account, opened from the Followers copying tile. */
export function FleetStatusPanel({ orgId, accounts }: { orgId: number; accounts: Account[] }) {
  return (
    <Card
      as="section"
      inset
      title="Fleet status"
      actions={<PanelLink to={`/org/${orgId}/accounts`}>Manage accounts</PanelLink>}
    >
      <div className="overflow-x-auto">
        <table className="stack-table w-full text-sm">
          <thead>
            <tr className="text-left border-b border-line">
              <th className="desk-label px-5 py-2 font-semibold">Account</th>
              <th className="desk-label px-3 py-2 font-semibold">Role</th>
              <th className="desk-label px-3 py-2 font-semibold">Health</th>
              <th className="desk-label px-5 py-2 font-semibold">Copying</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              // Anything that is not an affirmatively healthy status
              // reads as degraded -- 'disconnected' must never show OK.
              const degraded = a.status !== 'ok' && a.status !== 'connected'
              return (
                <tr key={a.ctid_trader_account_id} className="border-b border-line last:border-0">
                  <td data-label="Account" className="px-5 py-2.5">{accountName(a)}</td>
                  <td data-label="Role" className="px-3 py-2.5">
                    <Badge tone={a.role === 'master' ? 'profit' : 'neutral'}>{roleWord(a.role)}</Badge>
                  </td>
                  <td data-label="Health" className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-1.5">
                      <StatusDot tone={degraded ? 'degraded' : 'ok'} />
                      {degraded ? 'Degraded' : 'OK'}
                    </span>
                  </td>
                  <td data-label="Copying" className="px-5 py-2.5 text-ink-soft">
                    {a.role === 'master' ? '—' : a.enabled ? 'Enabled' : 'Paused'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/** Today's copy fills, opened from the Today's P&L tile. */
export function FillsPanel({ orgId, fills }: { orgId: number; fills: RecentCopy[] }) {
  return (
    <Card
      as="section"
      inset
      title="Today's copy fills · live"
      actions={<PanelLink to={`/org/${orgId}/logs`}>Full event log</PanelLink>}
    >
      {fills.length === 0 ? (
        <p className="px-5 py-4 text-sm text-ink-faint">No copy fills yet today.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">When</th>
                <th className="desk-label px-3 py-2 font-semibold">Status</th>
                <th className="desk-label px-3 py-2 font-semibold">Follower</th>
                <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Fill price</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((copy, i) => (
                <tr
                  key={`${copy.slave_account_id}-${copy.master_position_id ?? copy.master_order_id}-${i}`}
                  className="border-b border-line last:border-0"
                >
                  <td data-label="When" className="tnum px-5 py-2.5 text-ink-soft">{formatWhen(copy.updated_at)}</td>
                  <td data-label="Status" className="px-3 py-2.5">
                    <Badge tone={copyStatusTone(copy.status)}>{humanStatus(copy.status)}</Badge>
                  </td>
                  <td data-label="Follower" className="px-3 py-2.5">
                    {copy.slave_nickname || <span className="tnum">{copy.slave_login}</span>}
                  </td>
                  <td data-label="Symbol" className="tnum px-3 py-2.5">{copy.symbol ?? '—'}</td>
                  <td data-label="Fill price" className="tnum px-5 py-2.5 text-right">{copy.fill_price ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/** Every running contract with its own protection, opened from the Open positions tile. */
export function ContractsPanel({ orgId, rows, canTrade, closingIds, onClose, onCloseAll }: {
  orgId: number
  rows: ContractRow[]
  canTrade: boolean
  closingIds: Set<number>
  onClose: (row: ContractRow) => void
  onCloseAll: () => void
}) {
  return (
    <Card
      as="section"
      inset
      title="Open contracts · live"
      actions={
        <div className="flex items-center gap-3">
          {canTrade && rows.length > 0 && (
            <Button variant="secondary" tone="loss" size="sm" onClick={onCloseAll}>
              Close all shown
            </Button>
          )}
          <PanelLink to={`/org/${orgId}/positions`}>Full positions view</PanelLink>
        </div>
      }
    >
      {rows.length === 0 ? (
        <p className="px-5 py-4 text-sm text-ink-faint">
          No open contracts right now. Fills appear here the moment they happen.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Account</th>
                <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-3 py-2 font-semibold">Side</th>
                <th className="desk-label px-3 py-2 font-semibold text-right">Entry</th>
                <th className="desk-label px-3 py-2 font-semibold text-right">Current</th>
                <th className="desk-label px-3 py-2 font-semibold text-right whitespace-nowrap">SL / TP</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Live P&L</th>
                {canTrade && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.accountId}-${row.pos.position_id}`} className="border-b border-line last:border-0">
                  <td data-label="Account" className="px-5 py-2.5">{row.accountLabel}</td>
                  <td data-label="Symbol" className="tnum px-3 py-2.5">{row.pos.symbol ?? row.pos.symbol_id}</td>
                  <td data-label="Side" className={`px-3 py-2.5 font-medium ${row.pos.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                    {row.pos.side}
                  </td>
                  <td data-label="Entry" className="tnum px-3 py-2.5 text-right">{row.pos.entry_price}</td>
                  <td
                    data-label="Current"
                    className={`tnum px-3 py-2.5 text-right font-medium ${
                      row.pos.current_price != null ? 'text-brand' : 'text-ink-faint'
                    }`}
                  >
                    {row.pos.current_price ?? '—'}
                  </td>
                  {/* Each row's OWN protection. A copy whose stop never
                      arrived is the row that matters here, and reading the
                      master's alone would hide exactly that -- so an
                      unprotected side shows a dash and is tinted, rather
                      than borrowing a number from somewhere else. */}
                  <td data-label="SL / TP" className="tnum px-3 py-2.5 text-right whitespace-nowrap">
                    <span className={row.pos.stop_loss == null ? 'text-warn' : 'text-ink-soft'}>
                      {row.pos.stop_loss ?? '—'}
                    </span>
                    <span className="text-ink-faint"> / </span>
                    <span className={row.pos.take_profit == null ? 'text-warn' : 'text-ink-soft'}>
                      {row.pos.take_profit ?? '—'}
                    </span>
                  </td>
                  <td data-label="Live P&L" className="tnum px-5 py-2.5 text-right">
                    <span className={(row.pos.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}>
                      {signed(row.pos.pnl_quote)}
                    </span>
                  </td>
                  {canTrade && (
                    <td className="px-3 py-2.5 text-right">
                      {closingIds.has(row.pos.position_id) ? (
                        <span className="px-3 py-1 text-xs font-semibold text-ink-faint animate-pulse motion-reduce:animate-none">
                          Closing…
                        </span>
                      ) : (
                        <Button variant="secondary" tone="loss" size="sm" onClick={() => onClose(row)}>
                          Close
                        </Button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/** The recent copies with an estimated live P&L, or the failure reason. */
export function CopyLogCard({ copies, masterPnlByPosition }: {
  copies: RecentCopy[]
  masterPnlByPosition: Map<number, { pnl: number | null; volume: number }>
}) {
  return (
    <Card as="section" inset title="Copy log">
      {copies.length === 0 ? (
        <p className="px-5 py-4 text-sm text-ink-faint">
          No copies yet. They appear here the moment the master trades.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Status</th>
                <th className="desk-label px-3 py-2 font-semibold">Master</th>
                <th className="desk-label px-3 py-2 font-semibold">Follower</th>
                <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">P&L</th>
              </tr>
            </thead>
            <tbody>
              {copies.map((copy, i) => {
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
                  <tr
                    key={`${copy.slave_account_id}-${copy.master_position_id}-${copy.master_order_id}-${i}`}
                    className="border-b border-line last:border-0"
                  >
                    <td data-label="Status" className="px-5 py-2.5">
                      <Badge tone={copyStatusTone(copy.status)}>{humanStatus(copy.status)}</Badge>
                    </td>
                    <td data-label="Master" className="tnum px-3 py-2.5 text-ink-soft">
                      {copy.master_position_id ?? copy.master_order_id ?? '—'}
                    </td>
                    <td data-label="Follower" className="px-3 py-2.5">
                      {copy.slave_nickname || <span className="tnum">{copy.slave_login}</span>}
                    </td>
                    <td data-label="Symbol" className="tnum px-3 py-2.5">{copy.symbol ?? '—'}</td>
                    <td data-label="P&L" className="tnum px-5 py-2.5 text-right">
                      {copy.status === 'failed' && copy.error ? (
                        <span className="inline-block max-w-56 text-left">
                          <ExpandableText text={copy.error} limit={24} className="text-xs text-loss" />
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
    </Card>
  )
}
