import { useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import type { Account, AccountDetails, CloseAllResult } from '../lib/types'
import ConfirmDialog from '../components/ConfirmDialog'

// How long the green "Flattened ✓" confirmation stays on a row button.
const FLATTEN_DONE_MS = 5000

function formatDate(iso: string | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function formatTimestamp(ms: number | null | undefined): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

export default function Accounts() {
  const { orgId, role } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [roleErrors, setRoleErrors] = useState<Record<number, string>>({})
  const [multiplierErrors, setMultiplierErrors] = useState<Record<number, string>>({})
  const [pendingRows, setPendingRows] = useState<Set<number>>(new Set())
  const [multiplierDrafts, setMultiplierDrafts] = useState<Record<number, string>>({})
  const [nicknameDrafts, setNicknameDrafts] = useState<Record<number, string>>({})
  const [cutoffDrafts, setCutoffDrafts] = useState<Record<number, string>>({})
  const [disconnecting, setDisconnecting] = useState<Account | null>(null)
  const [flattening, setFlattening] = useState<Account | null>(null)
  // Master promotion re-shapes the whole fleet; it always confirms first.
  const [promoting, setPromoting] = useState<Account | null>(null)
  const [flattenStatus, setFlattenStatus] = useState<Record<number, 'busy' | 'done' | 'error'>>({})
  const [detailsFor, setDetailsFor] = useState<Account | null>(null)
  const [details, setDetails] = useState<AccountDetails | null>(null)
  const [detailsError, setDetailsError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const fetchAccounts = async () => {
    try {
      const data = await orgApi<Account[]>(orgId, 'accounts')
      setAccounts(data || [])
      setMultiplierDrafts({})
      setNicknameDrafts({})
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load accounts')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchAccounts()
  }, [orgId])

  // Refetch when the OAuth popup closes and focus returns to this window.
  useEffect(() => {
    const handleFocus = () => {
      fetchAccounts()
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [orgId])

  const withPending = async (accountId: number, action: () => Promise<void>) => {
    setPendingRows((prev) => new Set([...prev, accountId]))
    try {
      await action()
    } finally {
      setPendingRows((prev) => {
        const next = new Set(prev)
        next.delete(accountId)
        return next
      })
    }
  }

  const handleConnectOAuth = () => {
    window.open(`/api/orgs/${orgId}/oauth/connect`, 'ctrader-oauth', 'width=520,height=680')
  }

  const handleRoleChange = (accountId: number, newRole: string) =>
    withPending(accountId, async () => {
      try {
        setRoleErrors((prev) => ({ ...prev, [accountId]: '' }))
        await orgApi(orgId, `accounts/${accountId}`, {
          method: 'PATCH',
          body: JSON.stringify({ role: newRole }),
        })
        await fetchAccounts()
      } catch (err) {
        const errorCode = err instanceof Error ? err.message : 'Unknown error'
        setRoleErrors((prev) => ({
          ...prev,
          [accountId]: errorCode === '409'
            ? 'A master already exists'
            : `Failed to update role (${errorCode})`,
        }))
      }
    })

  const handleEnabledToggle = (accountId: number, currentEnabled: boolean) =>
    withPending(accountId, async () => {
      try {
        await orgApi(orgId, `accounts/${accountId}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: !currentEnabled }),
        })
        await fetchAccounts()
      } catch (err) {
        setError(`Failed to update enabled status (${err instanceof Error ? err.message : 'unknown'})`)
      }
    })

  const handleMultiplierBlur = (accountId: number) => {
    const draft = multiplierDrafts[accountId]
    if (draft === undefined) return
    const multiplier = parseFloat(draft)
    if (isNaN(multiplier) || multiplier <= 0) {
      setMultiplierErrors((prev) => ({ ...prev, [accountId]: 'Multiplier must be greater than 0' }))
      setMultiplierDrafts((prev) => {
        const next = { ...prev }
        delete next[accountId]
        return next
      })
      return
    }
    withPending(accountId, async () => {
      try {
        setMultiplierErrors((prev) => ({ ...prev, [accountId]: '' }))
        await orgApi(orgId, `accounts/${accountId}`, {
          method: 'PATCH',
          body: JSON.stringify({ multiplier }),
        })
        await fetchAccounts()
      } catch (err) {
        const errorCode = err instanceof Error ? err.message : 'Unknown error'
        setMultiplierErrors((prev) => ({
          ...prev, [accountId]: `Failed to update multiplier (${errorCode})`,
        }))
        setMultiplierDrafts((prev) => {
          const next = { ...prev }
          delete next[accountId]
          return next
        })
      }
    })
  }

  const handleNicknameBlur = (account: Account) => {
    const draft = nicknameDrafts[account.ctid_trader_account_id]
    if (draft === undefined || draft === (account.nickname ?? '')) return
    withPending(account.ctid_trader_account_id, async () => {
      try {
        await orgApi(orgId, `accounts/${account.ctid_trader_account_id}`, {
          method: 'PATCH',
          body: JSON.stringify({ nickname: draft }),
        })
        await fetchAccounts()
      } catch (err) {
        setError(`Failed to update nickname (${err instanceof Error ? err.message : 'unknown'})`)
      }
    })
  }

  const handleCutoffBlur = (account: Account) => {
    const draft = cutoffDrafts[account.ctid_trader_account_id]
    if (draft === undefined || draft === (account.cutoff_date ?? '')) return
    withPending(account.ctid_trader_account_id, async () => {
      try {
        // An empty value clears the cutoff (and with it the reminder).
        await orgApi(orgId, `accounts/${account.ctid_trader_account_id}`, {
          method: 'PATCH',
          body: JSON.stringify({ cutoff_date: draft }),
        })
        await fetchAccounts()
      } catch (err) {
        setError(`Failed to update cutoff date (${err instanceof Error ? err.message : 'unknown'})`)
      }
    })
  }

  const handleDisconnect = async () => {
    if (!disconnecting) return
    const accountId = disconnecting.ctid_trader_account_id
    try {
      setBusy(true)
      const result = await orgApi<{ accounts_removed: number }>(
        orgId, `accounts/${accountId}/connection`, { method: 'DELETE' })
      setNotice(
        `Disconnected. ${result.accounts_removed} account${result.accounts_removed === 1 ? '' : 's'} ` +
        'removed. The token stays revocable at ctrader.com.')
      setDisconnecting(null)
      await fetchAccounts()
    } catch (err) {
      setDisconnecting(null)
      setError(err instanceof Error ? err.message : 'Failed to disconnect account')
    } finally {
      setBusy(false)
    }
  }

  const handleFlatten = async () => {
    if (!flattening) return
    const account = flattening
    const accountId = account.ctid_trader_account_id
    // Close the dialog right away; progress and outcome live on the row button
    // so it is always clear WHICH account is being flattened.
    setFlattening(null)
    setError(null)
    setFlattenStatus((prev) => ({ ...prev, [accountId]: 'busy' }))
    try {
      const result = await orgApi<CloseAllResult>(orgId, 'control/close-all', {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      })
      const summary = result.accounts[0]
      setNotice(
        `Closed ${summary.positions_closed} position${summary.positions_closed === 1 ? '' : 's'} ` +
        `and cancelled ${summary.orders_cancelled} order${summary.orders_cancelled === 1 ? '' : 's'} ` +
        `on account ${account.trader_login}.`)
      setFlattenStatus((prev) => ({ ...prev, [accountId]: 'done' }))
      window.setTimeout(() => {
        setFlattenStatus((prev) => {
          if (prev[accountId] !== 'done') return prev
          const next = { ...prev }
          delete next[accountId]
          return next
        })
      }, FLATTEN_DONE_MS)
    } catch (err) {
      setFlattenStatus((prev) => ({ ...prev, [accountId]: 'error' }))
      setError(`Flatten failed on account ${account.trader_login}: ` +
        `${err instanceof Error ? err.message : 'unknown error'}`)
    }
  }

  const openDetails = async (account: Account) => {
    setDetailsFor(account)
    setDetails(null)
    setDetailsError(null)
    try {
      setDetails(await orgApi<AccountDetails>(
        orgId, `accounts/${account.ctid_trader_account_id}/details`))
    } catch (err) {
      setDetailsError(
        `Could not fetch details: ${err instanceof Error ? err.message : 'unknown error'}. ` +
        'The copier may be offline or the account not yet authorized.')
    }
  }

  if (isLoading) {
    return <div className="text-ink-faint">Loading accounts…</div>
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="page-title">Accounts</h1>
          <p className="text-sm text-ink-soft mt-1">
            One cTrader ID grant covers every account under it. Roles,
            multipliers, nicknames, and cutoff dates apply per account.
          </p>
        </div>
        {can(role, 'control') && (
          <button
            onClick={handleConnectOAuth}
            className="w-full md:w-auto shrink-0 px-4 py-2.5 bg-brand text-white text-sm font-semibold rounded hover:bg-brand-deep transition-colors"
          >
            Connect cTrader ID
          </button>
        )}
      </header>

      {/* Permanently mounted so screen readers reliably announce new notices. */}
      <div
        role="status"
        className={notice
          ? 'rounded border border-line bg-brand-wash px-4 py-3 text-sm text-ink flex justify-between items-center'
          : 'sr-only'}
      >
        {notice && (
          <>
            <span>{notice}</span>
            <button onClick={() => setNotice(null)} className="text-xs font-medium text-ink-soft hover:text-ink">
              Dismiss
            </button>
          </>
        )}
      </div>
      {error && (
        <div role="alert" className="rounded border border-loss/30 bg-loss-wash px-4 py-3 text-sm text-loss-deep">
          {error}
        </div>
      )}

      {accounts.length === 0 ? (
        <div className="bg-card border border-line rounded-lg px-6 py-12 text-center">
          <p className="text-ink-soft">No accounts connected yet.</p>
          <p className="text-sm text-ink-faint mt-1">
            Connect a cTrader ID to discover its trading accounts.
          </p>
        </div>
      ) : (
        <div className="bg-card rounded-lg border border-line overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2.5 font-semibold">Account</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Nickname</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Env</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Role</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Multiplier</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Enabled</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Cutoff</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Grant</th>
                <th className="desk-label px-5 py-2.5 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const id = account.ctid_trader_account_id
                const isPending = pendingRows.has(id)
                return (
                  <tr key={id} className={`border-b border-line last:border-0 align-top ${isPending ? 'opacity-60' : ''}`}>
                    <td data-label="Account" className="px-5 py-3">
                      <div className="num text-ink">{account.trader_login}</div>
                      <div className="text-xs text-ink-faint">cTID {id}</div>
                      {account.status === 'degraded' && (
                        <div
                          className="mt-1 text-xs text-loss-deep bg-loss-wash rounded px-1.5 py-0.5 max-w-44 truncate"
                          title={account.last_error ?? undefined}
                        >
                          degraded{account.last_error ? `: ${account.last_error}` : ''}
                        </div>
                      )}
                    </td>
                    <td data-label="Nickname" className="px-3 py-3">
                      {can(role, 'control') ? (
                        <input
                          type="text"
                          aria-label={`Nickname for account ${account.trader_login}`}
                          placeholder="Add a name"
                          value={nicknameDrafts[id] ?? account.nickname ?? ''}
                          onChange={(e) =>
                            setNicknameDrafts((prev) => ({ ...prev, [id]: e.target.value }))}
                          onBlur={() => handleNicknameBlur(account)}
                          disabled={isPending}
                          className="w-32 rounded border border-transparent hover:border-line-strong focus:border-line-strong px-2 py-1 text-sm bg-transparent"
                        />
                      ) : (
                        <span className="text-ink">{account.nickname || '—'}</span>
                      )}
                    </td>
                    <td data-label="Env" className="px-3 py-3">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                        account.is_live ? 'bg-loss-wash text-loss-deep' : 'bg-line text-ink-soft'
                      }`}>
                        {account.is_live ? 'Live' : 'Demo'}
                      </span>
                    </td>
                    <td data-label="Role" className="px-3 py-3">
                      {can(role, 'control') ? (
                        <select
                          aria-label={`Role for account ${account.trader_login}`}
                          value={account.role}
                          onChange={(e) => {
                            if (e.target.value === 'master') setPromoting(account)
                            else handleRoleChange(id, e.target.value)
                          }}
                          disabled={isPending}
                          className="rounded border border-line-strong px-2 py-1 text-sm bg-card disabled:opacity-50"
                        >
                          <option value="master">Master</option>
                          <option value="slave">Slave</option>
                          <option value="ignored">Ignored</option>
                        </select>
                      ) : (
                        <span className="text-ink">{account.role}</span>
                      )}
                      {roleErrors[id] && (
                        <div className="text-loss text-xs mt-1">{roleErrors[id]}</div>
                      )}
                    </td>
                    <td data-label="Multiplier" className="px-3 py-3">
                      {account.role === 'slave' ? (
                        can(role, 'control') ? (
                          <div>
                            <input
                              type="number"
                              step="0.01"
                              min="0.01"
                              aria-label={`Multiplier for account ${account.trader_login}`}
                              value={multiplierDrafts[id] ?? String(account.multiplier)}
                              onChange={(e) =>
                                setMultiplierDrafts((prev) => ({ ...prev, [id]: e.target.value }))}
                              onBlur={() => handleMultiplierBlur(id)}
                              disabled={isPending}
                              className="num w-20 rounded border border-line-strong px-2 py-1 text-sm disabled:opacity-50"
                            />
                            {multiplierErrors[id] && (
                              <div className="text-loss text-xs mt-1 max-w-36">{multiplierErrors[id]}</div>
                            )}
                          </div>
                        ) : (
                          <span className="num text-ink">{account.multiplier}</span>
                        )
                      ) : (
                        <span className="text-ink-faint">—</span>
                      )}
                    </td>
                    <td data-label="Enabled" className="px-3 py-3">
                      {can(role, 'control') ? (
                        <label className="relative inline-flex items-center cursor-pointer has-[:disabled]:cursor-default align-middle">
                          <input
                            type="checkbox"
                            aria-label={`Copying enabled for account ${account.trader_login}`}
                            checked={account.enabled}
                            onChange={() => handleEnabledToggle(id, account.enabled)}
                            disabled={isPending}
                            className="peer sr-only"
                          />
                          <span
                            aria-hidden="true"
                            className="h-5 w-9 rounded-full bg-ink-faint transition-colors peer-checked:bg-brand peer-disabled:opacity-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand"
                          />
                          <span
                            aria-hidden="true"
                            className="pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-card shadow-sm transition-transform peer-checked:translate-x-4"
                          />
                        </label>
                      ) : (
                        <span className="text-ink">{account.enabled ? 'Yes' : 'No'}</span>
                      )}
                    </td>
                    <td data-label="Cutoff" className="px-3 py-3">
                      {can(role, 'control') ? (
                        <input
                          type="date"
                          aria-label={`Cutoff date for account ${account.trader_login}`}
                          value={cutoffDrafts[id] ?? account.cutoff_date ?? ''}
                          onChange={(e) =>
                            setCutoffDrafts((prev) => ({ ...prev, [id]: e.target.value }))}
                          onBlur={() => handleCutoffBlur(account)}
                          disabled={isPending}
                          className="num rounded border border-transparent hover:border-line-strong focus:border-line-strong px-2 py-1 text-sm bg-transparent"
                        />
                      ) : (
                        <span className="num text-ink">{account.cutoff_date || '—'}</span>
                      )}
                    </td>
                    <td data-label="Grant" className="px-3 py-3">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                        account.connection_status === 'active'
                          ? 'bg-profit-wash text-profit-deep'
                          : 'bg-warn-wash text-warn-deep'
                      }`}>
                        {account.connection_status === 'active' ? 'Active' : account.connection_status}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex justify-end gap-2 flex-wrap">
                        <button
                          onClick={() => openDetails(account)}
                          disabled={isPending}
                          className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors disabled:opacity-50"
                        >
                          Details
                        </button>
                        {can(role, 'control') && (
                          <button
                            onClick={handleConnectOAuth}
                            disabled={isPending}
                            className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors disabled:opacity-50"
                          >
                            Re-grant access
                          </button>
                        )}
                        {can(role, 'control') && (flattenStatus[id] === 'busy' ? (
                          <button
                            aria-disabled="true"
                            className="min-w-[6.5rem] px-2.5 py-1 text-xs font-semibold rounded border border-line-strong text-ink-soft animate-pulse motion-reduce:animate-none"
                          >
                            Flattening…
                          </button>
                        ) : flattenStatus[id] === 'done' ? (
                          <button
                            aria-disabled="true"
                            className="min-w-[6.5rem] px-2.5 py-1 text-xs font-semibold rounded border border-profit bg-profit-wash text-profit-deep"
                          >
                            Flattened ✓
                          </button>
                        ) : (
                          <button
                            onClick={() => {
                              setFlattenStatus((prev) => {
                                const next = { ...prev }
                                delete next[id]
                                return next
                              })
                              setFlattening(account)
                            }}
                            disabled={isPending}
                            className={flattenStatus[id] === 'error'
                              ? 'min-w-[6.5rem] px-2.5 py-1 text-xs font-semibold rounded border border-loss bg-loss text-white hover:bg-loss-deep transition-colors disabled:opacity-50'
                              : 'min-w-[6.5rem] px-2.5 py-1 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-white transition-colors disabled:opacity-50'}
                          >
                            {flattenStatus[id] === 'error' ? 'Failed — retry' : 'Flatten'}
                          </button>
                        ))}
                        {can(role, 'control') && (
                          <button
                            onClick={() => setDisconnecting(account)}
                            disabled={isPending}
                            className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-loss hover:border-loss transition-colors disabled:opacity-50"
                          >
                            Disconnect
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Disconnect confirmation */}
      <ConfirmDialog
        open={disconnecting != null}
        title={`Disconnect account ${disconnecting?.trader_login ?? ''}`}
        confirmLabel="Disconnect grant"
        danger
        busy={busy}
        onConfirm={handleDisconnect}
        onCancel={() => setDisconnecting(null)}
      >
        <p>
          This removes the cTrader ID grant behind this account — and with it{' '}
          <strong>every account under that same grant</strong>. Open positions
          are not touched; the copier just stops seeing these accounts.
        </p>
        <p>You can reconnect any time with Connect cTrader ID.</p>
      </ConfirmDialog>

      {/* Master promotion */}
      <ConfirmDialog
        open={promoting != null}
        title={`Make account ${promoting?.trader_login ?? ''} the master`}
        confirmLabel="Make it the master"
        onConfirm={() => {
          if (promoting) {
            handleRoleChange(promoting.ctid_trader_account_id, 'master')
          }
          setPromoting(null)
        }}
        onCancel={() => setPromoting(null)}
      >
        <p>
          Every other account in this workspace becomes a slave — including
          the current master and any Ignored accounts — and every enabled
          slave then copies account {promoting?.trader_login}'s trades.
        </p>
      </ConfirmDialog>

      {/* Per-account kill switch */}
      <ConfirmDialog
        open={flattening != null}
        title={`Flatten account ${flattening?.trader_login ?? ''}`}
        confirmLabel="Close everything here"
        danger
        onConfirm={handleFlatten}
        onCancel={() => setFlattening(null)}
      >
        <p>
          Every open position in this account is closed at market and every
          working order cancelled. Other accounts are untouched
          {flattening?.is_live ? ' — and this is a live account' : ''}.
        </p>
      </ConfirmDialog>

      {/* Details drawer */}
      {detailsFor && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={() => setDetailsFor(null)}>
          <aside
            className="w-full max-w-md h-full bg-card border-l border-line overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
            role="complementary"
            aria-label={`Details for account ${detailsFor.trader_login}`}
          >
            <div className="px-6 py-5 border-b border-line flex items-start justify-between">
              <div>
                <h2 className="font-display text-xl text-ink">
                  {detailsFor.nickname || `Account ${detailsFor.trader_login}`}
                </h2>
                <p className="num text-sm text-ink-soft mt-0.5">
                  {detailsFor.trader_login} · cTID {detailsFor.ctid_trader_account_id}
                </p>
              </div>
              <button
                onClick={() => setDetailsFor(null)}
                aria-label="Close details"
                className="text-ink-soft hover:text-ink text-xl leading-none"
              >
                ×
              </button>
            </div>

            {detailsError ? (
              <p className="px-6 py-4 text-sm text-loss-deep">{detailsError}</p>
            ) : !details ? (
              <p className="px-6 py-4 text-sm text-ink-faint">Fetching from the broker…</p>
            ) : (
              <div className="px-6 py-4 space-y-6">
                <section>
                  <h3 className="desk-label mb-2">Broker profile</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Broker" value={details.broker_name ?? '—'} />
                    <DetailRow
                      label="Balance"
                      value={details.balance != null
                        ? `${details.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${details.deposit_currency ?? ''}`
                        : '—'}
                      mono
                    />
                    <DetailRow label="Currency" value={details.deposit_currency ?? '—'} />
                    <DetailRow
                      label="Leverage"
                      value={details.leverage != null ? `1:${details.leverage}` : '—'}
                      mono
                    />
                    <DetailRow
                      label="Max leverage"
                      value={details.max_leverage != null ? `1:${details.max_leverage}` : '—'}
                      mono
                    />
                    <DetailRow label="Account type" value={details.account_type ?? '—'} />
                    <DetailRow label="Access" value={details.access_rights ?? '—'} />
                    <DetailRow
                      label="Swap-free"
                      value={details.swap_free == null ? '—' : details.swap_free ? 'Yes' : 'No'}
                    />
                    <DetailRow
                      label="Registered"
                      value={formatTimestamp(details.registration_timestamp)}
                    />
                  </dl>
                  <p className="mt-3 text-xs text-ink-faint">
                    The account holder's name and email are not exposed by the
                    cTrader Open API — set a nickname instead.
                  </p>
                </section>

                <section>
                  <h3 className="desk-label mb-2">Copy settings</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Role" value={details.role ?? detailsFor.role} />
                    <DetailRow
                      label="Multiplier"
                      value={String(details.multiplier ?? detailsFor.multiplier)}
                      mono
                    />
                    <DetailRow
                      label="Enabled"
                      value={(details.enabled ?? detailsFor.enabled) ? 'Yes' : 'No'}
                    />
                    <DetailRow label="Status" value={details.status ?? detailsFor.status} />
                  </dl>
                </section>

                <section>
                  <h3 className="desk-label mb-2">OAuth grant</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Granted" value={formatDate(details.connection?.granted_at)} />
                    <DetailRow label="Token expires" value={formatDate(details.connection?.expires_at)} />
                    <DetailRow label="Grant status" value={details.connection?.status ?? '—'} />
                    <DetailRow label="Scope" value={details.connection?.scope ?? '—'} />
                  </dl>
                </section>

                <section>
                  <h3 className="desk-label mb-2">
                    Open positions ({details.open_positions.length})
                  </h3>
                  {details.open_positions.length === 0 ? (
                    <p className="text-sm text-ink-faint">None.</p>
                  ) : (
                    <ul className="space-y-1 text-sm">
                      {details.open_positions.map((pos) => (
                        <li key={pos.position_id} className="flex justify-between">
                          <span className="num">{pos.symbol ?? pos.symbol_id}</span>
                          <span className={pos.side === 'BUY' ? 'text-profit' : 'text-loss'}>
                            {pos.side} <span className="num">{pos.volume_lots ?? pos.volume}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-soft">{label}</dt>
      <dd className={`text-ink text-right ${mono ? 'num' : ''}`}>{value}</dd>
    </div>
  )
}
