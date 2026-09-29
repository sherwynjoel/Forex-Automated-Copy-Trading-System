import type { ComponentProps } from 'react'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import Menu from '../../components/Menu'
import StatusDot from '../../components/StatusDot'
import { formatWhen, money } from '../../lib/format'
import { accountName, isMt5 } from '../../lib/platform'
import type { Account } from '../../lib/types'
import { needsRegrant, type FlattenState } from './useAccountsPage'

type MenuItems = ComponentProps<typeof Menu>['items']
type DotTone = ComponentProps<typeof StatusDot>['tone']

// The API keeps its role names; the product says "follower".
const ACCOUNT_ROLE_LABEL: Record<string, string> = {
  master: 'Master',
  slave: 'Follower',
  ignored: 'Ignored',
}

export function accountRoleLabel(role: string): string {
  return ACCOUNT_ROLE_LABEL[role] ?? role
}

/** "MT5 · login 555 · XYZ Ltd" -- what an MT5 row prints under the login in
 *  place of the cTrader id. Before the first hello there is no login and no
 *  broker to print, and "login 0" would look like one. */
export function mt5Subtitle(link: Account['mt5']): string {
  const parts = ['MT5', link?.login ? `login ${link.login}` : 'no login yet']
  if (link?.broker) parts.push(link.broker)
  return parts.join(' · ')
}

function humanise(value: string): string {
  const words = value.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The copier's view of the link. MT5: connected (a report within 15 s),
 *  offline (with when it was last heard from), or never reported -- the EA
 *  is not installed yet. cTrader: the grant is active, or it is not. */
function connectionHealth(account: Account): { tone: DotTone; word: string } {
  if (isMt5(account)) {
    switch (account.connection_status) {
      case 'connected':
        return { tone: 'ok', word: 'Connected' }
      case 'offline':
        return { tone: 'warn', word: `Offline · last seen ${formatWhen(account.mt5?.last_seen_at)}` }
      default:
        return { tone: 'warn', word: 'Waiting for the terminal' }
    }
  }
  if (account.connection_status === 'active') return { tone: 'ok', word: 'Active' }
  return { tone: 'warn', word: humanise(account.connection_status) }
}

function FlattenStatus({ state, pending, onRetry }: {
  state: FlattenState | undefined
  pending: boolean
  onRetry: () => void
}) {
  if (state === 'busy') {
    return (
      <Button
        variant="secondary"
        size="sm"
        aria-disabled="true"
        className="min-w-[6.5rem] animate-pulse motion-reduce:animate-none"
      >
        Flattening…
      </Button>
    )
  }
  if (state === 'done') {
    return (
      <Button variant="secondary" tone="profit" size="sm" aria-disabled="true" className="min-w-[6.5rem]">
        Flattened ✓
      </Button>
    )
  }
  if (state === 'error') {
    return (
      <Button tone="loss" size="sm" className="min-w-[6.5rem]" onClick={onRetry} disabled={pending}>
        Failed — retry
      </Button>
    )
  }
  return null
}

export default function AccountRow({
  account, equity, canControl, pending, flatten,
  onDetails, onFlatten, onRegrant, onRotate, onDisconnect, onRemove,
}: {
  account: Account
  /** The engine's reading; absent means no reading, shown as a dash. */
  equity: number | null | undefined
  canControl: boolean
  pending: boolean
  flatten: FlattenState | undefined
  onDetails: (account: Account) => void
  onFlatten: (account: Account) => void
  onRegrant: () => void
  onRotate: (account: Account) => void
  onDisconnect: (account: Account) => void
  onRemove: (account: Account) => void
}) {
  const id = account.ctid_trader_account_id
  const onMt5 = isMt5(account)
  const health = connectionHealth(account)

  const items: MenuItems = [
    { key: 'flatten', label: 'Flatten', tone: 'loss', disabled: pending || flatten === 'busy', onSelect: () => onFlatten(account) },
  ]
  if (needsRegrant(account)) {
    items.push({ key: 'regrant', label: 'Re-grant access', disabled: pending, onSelect: onRegrant })
  }
  if (onMt5) {
    items.push({ key: 'rotate', label: 'Rotate key', disabled: pending, onSelect: () => onRotate(account) })
  }
  // MT5 removal is permanent; the master is never offered it.
  if (onMt5 && account.role !== 'master') {
    items.push({ key: 'remove', label: 'Remove', tone: 'loss', disabled: pending, onSelect: () => onRemove(account) })
  }
  if (!onMt5) {
    items.push({ key: 'disconnect', label: 'Disconnect', tone: 'loss', disabled: pending, onSelect: () => onDisconnect(account) })
  }

  return (
    <tr className={`border-b border-line last:border-0 align-top ${pending ? 'opacity-60' : ''}`}>
      <td data-label="Account" className="px-5 py-3">
        <div className="num text-ink">{onMt5 ? (account.mt5?.login ?? '—') : account.trader_login}</div>
        <div className="text-xs text-ink-faint">{onMt5 ? mt5Subtitle(account.mt5) : `cTID ${id}`}</div>
        <Badge tone={onMt5 ? 'brand' : 'neutral'} className="mt-1">{onMt5 ? 'MT5' : 'cTrader'}</Badge>
      </td>
      <td data-label="Nickname" className="px-3 py-3">
        <span className="text-ink">{account.nickname || '—'}</span>
      </td>
      <td data-label="Role" className="px-3 py-3">
        <Badge tone={account.role === 'master' ? 'brand' : 'neutral'}>{accountRoleLabel(account.role)}</Badge>
      </td>
      <td data-label="Env" className="px-3 py-3">
        {/* Red means danger only: Live is a neutral chip with a filled dot. */}
        <Badge tone="neutral" pill className="gap-1.5">
          {account.is_live && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ink" />}
          {account.is_live ? 'Live' : 'Demo'}
        </Badge>
      </td>
      <td data-label="Equity" className="num px-3 py-3 text-right whitespace-nowrap">
        {/* An account the engine has no reading for shows a dash. Rendering
            0.00 would read as an empty account, which is a different fact. */}
        <span className="text-ink">{money(equity)}</span>
      </td>
      <td data-label="Health" className="px-3 py-3">
        <div className="flex items-center gap-1.5 text-ink">
          <StatusDot tone={health.tone} />
          <span>{health.word}</span>
        </div>
        {account.status === 'degraded' && (
          // The error expands in place rather than hiding in a tooltip.
          <div className="mt-1 flex items-start gap-1.5 text-xs text-loss-deep">
            <StatusDot tone="degraded" />
            <span className="break-words">
              Degraded{account.last_error ? `: ${account.last_error}` : ''}
            </span>
          </div>
        )}
      </td>
      <td className="px-5 py-3">
        <div className="flex flex-wrap items-center justify-end gap-2">
          {canControl && (
            <FlattenStatus state={flatten} pending={pending} onRetry={() => onFlatten(account)} />
          )}
          <Button variant="secondary" size="sm" onClick={() => onDetails(account)} disabled={pending}>
            Details
          </Button>
          {canControl && <Menu label={`Actions for ${accountName(account)}`} items={items} />}
        </div>
      </td>
    </tr>
  )
}
