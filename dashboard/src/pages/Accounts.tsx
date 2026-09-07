import { useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import type {
  Account, AccountDetails, ApiState, CloseAllResult, Mt5AccountCreated, StateSnapshot,
  SymbolAliases,
} from '../lib/types'
import ConfirmDialog from '../components/ConfirmDialog'
import { money, formatWhen } from '../lib/format'
import { isMt5, accountName } from '../lib/platform'

// How long the green "Flattened ✓" confirmation stays on a row button.
const FLATTEN_DONE_MS = 5000

/** A key is shown exactly once. This is held only while its dialog is open
 *  and dropped the moment the dialog closes -- never stored anywhere else. */
interface KeyReveal {
  title: string
  key: string
  /** Present on creation; a rotation only replaces the key. */
  download_url: string | null
  install: string[]
}

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

/** "MT5 · login 555 · XYZ Ltd" -- what an MT5 row prints under the login in
 *  place of the cTrader id. Before the first hello there is no login and no
 *  broker to print, and "login 0" would look like one. */
function mt5Subtitle(link: Account['mt5']): string {
  const parts = ['MT5', link?.login ? `login ${link.login}` : 'no login yet']
  if (link?.broker) parts.push(link.broker)
  return parts.join(' · ')
}

export default function Accounts() {
  const { orgId, role } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [roleErrors, setRoleErrors] = useState<Record<number, string>>({})
  const [pendingRows, setPendingRows] = useState<Set<number>>(new Set())
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
  // Add MT5 account: nickname dialog → POST → one-time key dialog.
  const [addingMt5, setAddingMt5] = useState(false)
  const [mt5Nickname, setMt5Nickname] = useState('')
  const [keyReveal, setKeyReveal] = useState<KeyReveal | null>(null)
  const [copied, setCopied] = useState(false)
  const [rotating, setRotating] = useState<Account | null>(null)
  // Symbol mapping for the MT5 account whose drawer is open.
  const [aliases, setAliases] = useState<SymbolAliases | null>(null)
  const [aliasesError, setAliasesError] = useState<string | null>(null)
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>({})
  const [newAlias, setNewAlias] = useState({ canonical: '', broker_name: '' })

  // Live equity per account, keyed by account id. Held separately from the
  // accounts rows because it comes from the engine, not the database: the
  // accounts table has no balance column, and a stale number here would be
  // worse than none.
  const [equityByAccount, setEquityByAccount] = useState<StateSnapshot>({})

  const fetchEquity = async () => {
    try {
      const envelope = await orgApi<ApiState>(orgId, 'state')
      setEquityByAccount(envelope.accounts ?? {})
    } catch {
      // The engine being unreachable must not blank the accounts list --
      // this screen is how an operator disconnects or flattens an account,
      // and it has to work when the copier is the thing that is broken.
      setEquityByAccount({})
    }
  }

  const fetchAccounts = async () => {
    try {
      const data = await orgApi<Account[]>(orgId, 'accounts')
      setAccounts(data || [])
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
    fetchEquity()
  }, [orgId])

  // Refetch when the OAuth popup closes and focus returns to this window.
  useEffect(() => {
    const handleFocus = () => {
      fetchAccounts()
      fetchEquity()
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
    // Same tab, deliberately NOT a popup. The broker sends the browser back
    // to /api/oauth/callback, and that return trip is cross-site: the
    // session cookie is SameSite=Lax, which browsers withhold from a popup
    // navigated cross-site. The callback then saw an anonymous request and
    // answered "Not authenticated", so connecting an account was
    // impossible. A top-level navigation always carries the cookie, and
    // the callback already redirects to this same page with ?connected=1,
    // so nothing is lost by leaving it.
    window.location.assign(`/api/orgs/${orgId}/oauth/connect`)
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

  const handleAddMt5 = async () => {
    try {
      setBusy(true)
      const result = await orgApi<Mt5AccountCreated>(orgId, 'mt5/accounts', {
        method: 'POST',
        body: JSON.stringify({ nickname: mt5Nickname.trim() }),
      })
      setAddingMt5(false)
      setMt5Nickname('')
      setKeyReveal({
        title: 'MT5 account added — install the EA',
        key: result.key,
        download_url: result.download_url,
        install: result.install,
      })
      await fetchAccounts()
    } catch (err) {
      setAddingMt5(false)
      setError(`Could not add the MT5 account (${err instanceof Error ? err.message : 'unknown'})`)
    } finally {
      setBusy(false)
    }
  }

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Could not copy — select the key and copy it by hand')
    }
  }

  const closeKeyReveal = () => {
    // The key leaves memory here; it is never shown again.
    setKeyReveal(null)
    setCopied(false)
  }

  const handleRotateKey = async () => {
    if (!rotating) return
    const account = rotating
    try {
      setBusy(true)
      const result = await orgApi<{ key: string }>(
        orgId, `mt5/accounts/${account.ctid_trader_account_id}/key`, { method: 'POST' })
      setRotating(null)
      setKeyReveal({
        title: `New key for ${accountName(account)}`,
        key: result.key,
        download_url: null,
        install: [],
      })
    } catch (err) {
      setRotating(null)
      setError(`Could not rotate the key (${err instanceof Error ? err.message : 'unknown'})`)
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
      const stillOpen = summary.positions_remaining?.length ?? 0
      if (stillOpen > 0 || summary.error) {
        // Not 'done'. The row must not go green over an account that is
        // still carrying the position the operator asked to be rid of.
        setFlattenStatus((prev) => ({ ...prev, [accountId]: 'error' }))
        setError(
          `Account ${account.trader_login}: closed ${summary.positions_closed}, but ` +
          `${stillOpen} position${stillOpen === 1 ? '' : 's'} could not be closed. ` +
          `You are still exposed — close ${stillOpen === 1 ? 'it' : 'them'} in the platform.`)
        return
      }
      setNotice(
        `Closed ${summary.positions_closed} position${summary.positions_closed === 1 ? '' : 's'} ` +
        `and cancelled ${summary.orders_cancelled} order${summary.orders_cancelled === 1 ? '' : 's'} ` +
        `on account ${account.trader_login}. Verified flat.`)
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

  const loadAliases = async (accountId: number) => {
    try {
      setAliases(await orgApi<SymbolAliases>(orgId, `accounts/${accountId}/symbol-aliases`))
      setAliasesError(null)
    } catch (err) {
      setAliasesError(
        `Could not load the symbol mapping (${err instanceof Error ? err.message : 'unknown'})`)
    }
  }

  // One PUT per change, then a re-read: the server decides the auto/manual
  // tag, and an empty broker name removes the row.
  const saveAlias = async (accountId: number, canonical: string, brokerName: string) => {
    try {
      await orgApi(orgId, `accounts/${accountId}/symbol-aliases`, {
        method: 'PUT',
        body: JSON.stringify({ aliases: { [canonical]: brokerName } }),
      })
      await loadAliases(accountId)
    } catch (err) {
      setAliasesError(
        `Could not save the mapping for ${canonical} (${err instanceof Error ? err.message : 'unknown'})`)
    }
  }

  const handleAliasBlur = async (row: SymbolAliases['aliases'][number]) => {
    if (!detailsFor) return
    const draft = aliasDrafts[row.canonical]
    if (draft === undefined || draft.trim() === row.broker_name) return
    await saveAlias(detailsFor.ctid_trader_account_id, row.canonical, draft.trim())
    setAliasDrafts((prev) => {
      const next = { ...prev }
      delete next[row.canonical]
      return next
    })
  }

  const handleAddAlias = () => {
    if (!detailsFor) return
    // Canonical names are what master events carry: upper-case.
    const canonical = newAlias.canonical.trim().toUpperCase()
    const brokerName = newAlias.broker_name.trim()
    if (!canonical || !brokerName) return
    setNewAlias({ canonical: '', broker_name: '' })
    saveAlias(detailsFor.ctid_trader_account_id, canonical, brokerName)
  }

  const openDetails = async (account: Account) => {
    setDetailsFor(account)
    setDetails(null)
    setDetailsError(null)
    setAliases(null)
    setAliasesError(null)
    setAliasDrafts({})
    setNewAlias({ canonical: '', broker_name: '' })
    // The mapping lives in the api's database, so it loads even when the
    // copier is down; reading it needs the trader role.
    if (isMt5(account) && can(role, 'trade')) loadAliases(account.ctid_trader_account_id)
    try {
      setDetails(await orgApi<AccountDetails>(
        orgId, `accounts/${account.ctid_trader_account_id}/details`))
    } catch (err) {
      setDetailsError(
        `Could not fetch details: ${err instanceof Error ? err.message : 'unknown error'}. ` +
        (isMt5(account)
          ? 'The copier may be offline or the terminal has not connected yet.'
          : 'The copier may be offline or the account not yet authorized.'))
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
            One cTrader ID grant covers every account under it; an MT5 account
            connects through the MirrorFleet EA in its own terminal. Roles,
            nicknames, and cutoff dates apply per account.
          </p>
        </div>
        {can(role, 'control') && (
          <div className="flex flex-col gap-2 md:flex-row md:items-center shrink-0">
            <button
              onClick={() => setAddingMt5(true)}
              className="w-full md:w-auto px-4 py-2.5 text-sm font-semibold rounded border border-line-strong text-ink hover:border-ink transition-colors"
            >
              Add MT5 account
            </button>
            <button
              onClick={handleConnectOAuth}
              className="w-full md:w-auto px-4 py-2.5 bg-brand text-on-accent text-sm font-semibold rounded hover:bg-brand-deep transition-colors"
            >
              Connect cTrader ID
            </button>
          </div>
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
            Connect a cTrader ID to discover its trading accounts, or add an
            MT5 account and install the EA in its terminal.
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
                <th className="desk-label px-3 py-2.5 font-semibold text-right">Equity</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Role</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Enabled</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Cutoff</th>
                <th className="desk-label px-3 py-2.5 font-semibold">Connection</th>
                <th className="desk-label px-5 py-2.5 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const id = account.ctid_trader_account_id
                const isPending = pendingRows.has(id)
                const onMt5 = isMt5(account)
                return (
                  <tr key={id} className={`border-b border-line last:border-0 align-top ${isPending ? 'opacity-60' : ''}`}>
                    <td data-label="Account" className="px-5 py-3">
                      <div className="num text-ink">
                        {onMt5 ? (account.mt5?.login ?? '—') : account.trader_login}
                      </div>
                      <div className="text-xs text-ink-faint">
                        {onMt5 ? mt5Subtitle(account.mt5) : `cTID ${id}`}
                      </div>
                      <span className={`mt-1 inline-block text-xs font-semibold px-2 py-0.5 rounded ${
                        onMt5 ? 'bg-brand-wash text-ink' : 'bg-line text-ink-soft'
                      }`}>
                        {onMt5 ? 'MT5' : 'cTrader'}
                      </span>
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
                    <td data-label="Equity" className="num px-3 py-3 text-right whitespace-nowrap">
                      {/* An account the engine has no reading for shows a dash. Rendering
                          0.00 would read as an empty account, which is a different fact. */}
                      <span className="text-ink">
                        {money(equityByAccount[String(account.ctid_trader_account_id)]?.equity)}
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
                    <td data-label="Connection" className="px-3 py-3">
                      {onMt5 ? (
                        <Mt5ConnectionBadge account={account} />
                      ) : (
                        <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                          account.connection_status === 'active'
                            ? 'bg-profit-wash text-profit-deep'
                            : 'bg-warn-wash text-warn-deep'
                        }`}>
                          {account.connection_status === 'active' ? 'Active' : account.connection_status}
                        </span>
                      )}
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
                        {can(role, 'control') && !onMt5 && (
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
                              ? 'min-w-[6.5rem] px-2.5 py-1 text-xs font-semibold rounded border border-loss bg-loss text-on-accent hover:bg-loss-deep transition-colors disabled:opacity-50'
                              : 'min-w-[6.5rem] px-2.5 py-1 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors disabled:opacity-50'}
                          >
                            {flattenStatus[id] === 'error' ? 'Failed — retry' : 'Flatten'}
                          </button>
                        ))}
                        {can(role, 'control') && onMt5 && (
                          <button
                            onClick={() => setRotating(account)}
                            disabled={isPending}
                            className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors disabled:opacity-50"
                          >
                            Rotate key
                          </button>
                        )}
                        {can(role, 'control') && !onMt5 && (
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

      {/* Add MT5 account: the nickname is the only identifier the row has
          until its terminal connects, so it is required. */}
      <ConfirmDialog
        open={addingMt5}
        title="Add an MT5 account"
        confirmLabel="Create account"
        busy={busy}
        disabled={mt5Nickname.trim() === ''}
        onConfirm={handleAddMt5}
        onCancel={() => { setAddingMt5(false); setMt5Nickname('') }}
      >
        <p>
          The account starts as a disabled slave. You get a one-time key to
          paste into the MirrorFleet EA running in the account's own MT5
          terminal; the login, broker and symbols arrive when the EA first
          connects.
        </p>
        <div>
          <label className="desk-label block mb-1" htmlFor="mt5-nickname">Nickname</label>
          <input
            id="mt5-nickname"
            type="text"
            value={mt5Nickname}
            onChange={(e) => setMt5Nickname(e.target.value)}
            autoComplete="off"
            placeholder="e.g. VPS desk"
            className="w-full rounded border border-line-strong px-3 py-2 text-sm text-ink bg-card"
          />
        </div>
      </ConfirmDialog>

      {/* One-time key reveal. Confirm and cancel both just close it. */}
      <ConfirmDialog
        open={keyReveal != null}
        title={keyReveal?.title ?? ''}
        confirmLabel="I have copied it — close"
        onConfirm={closeKeyReveal}
        onCancel={closeKeyReveal}
      >
        <p>
          This key is shown <strong>once</strong>. Paste it into the EA's{' '}
          <span className="num">InpKey</span> input. If it is lost, rotate the
          key from the account's row — the old one stops working at once.
        </p>
        <div className="flex items-center gap-2">
          <code
            aria-label="MT5 key"
            className="num flex-1 break-all rounded border border-line-strong bg-paper px-3 py-2 text-xs text-ink"
          >
            {keyReveal?.key}
          </code>
          <button
            type="button"
            onClick={() => { if (keyReveal) copyKey(keyReveal.key) }}
            className="shrink-0 px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        {keyReveal?.download_url && (
          <a
            href={keyReveal.download_url}
            download
            className="inline-block text-sm font-medium text-brand hover:underline"
          >
            Download MirrorFleet.mq5
          </a>
        )}
        {keyReveal && keyReveal.install.length > 0 && (
          <ol className="list-decimal pl-5 space-y-1">
            {keyReveal.install.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        )}
      </ConfirmDialog>

      {/* Rotate key: the old key dies the moment the new one exists. */}
      <ConfirmDialog
        open={rotating != null}
        title={`Rotate the key for ${rotating ? accountName(rotating) : ''}`}
        confirmLabel="Rotate key"
        danger
        busy={busy}
        onConfirm={handleRotateKey}
        onCancel={() => setRotating(null)}
      >
        <p>
          The current key stops working the moment the new one exists, so the
          running EA disconnects until you paste the new key into its{' '}
          <span className="num">InpKey</span> input and restart it. Positions,
          symbol mapping and history are untouched.
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
                  {isMt5(detailsFor)
                    ? mt5Subtitle(detailsFor.mt5)
                    : `${detailsFor.trader_login} · cTID ${detailsFor.ctid_trader_account_id}`}
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
                    {isMt5(detailsFor)
                      ? 'The terminal reports only what MT5 exposes to an Expert Advisor — set a nickname for anything more.'
                      : 'The account holder\'s name and email are not exposed by the cTrader Open API — set a nickname instead.'}
                  </p>
                </section>

                <section>
                  <h3 className="desk-label mb-2">Copy settings</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Role" value={details.role ?? detailsFor.role} />
                    <DetailRow
                      label="Enabled"
                      value={(details.enabled ?? detailsFor.enabled) ? 'Yes' : 'No'}
                    />
                    <DetailRow label="Status" value={details.status ?? detailsFor.status} />
                  </dl>
                </section>

                {isMt5(detailsFor) ? (
                  <section>
                    <h3 className="desk-label mb-2">Terminal</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Broker" value={detailsFor.mt5?.broker ?? '—'} />
                      <DetailRow label="Server" value={detailsFor.mt5?.server ?? '—'} />
                      <DetailRow label="Currency" value={detailsFor.mt5?.currency ?? '—'} />
                      <DetailRow
                        label="Hedging"
                        value={detailsFor.mt5?.hedging == null
                          ? '—'
                          : detailsFor.mt5?.hedging ? 'Yes' : 'No — netting accounts are not supported'}
                      />
                      <DetailRow label="Trade mode" value={detailsFor.mt5?.trade_mode ?? '—'} />
                      <DetailRow label="EA version" value={detailsFor.mt5?.ea_version ?? '—'} mono />
                      <DetailRow label="Last seen" value={formatWhen(detailsFor.mt5?.last_seen_at)} mono />
                    </dl>
                    {/* The spec puts Rotate key here, beside the terminal it
                        cuts off; the row button opens the same dialog. */}
                    {can(role, 'control') && (
                      <button
                        type="button"
                        onClick={() => setRotating(detailsFor)}
                        className="mt-3 px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors"
                      >
                        Rotate key
                      </button>
                    )}
                  </section>
                ) : (
                  <section>
                    <h3 className="desk-label mb-2">OAuth grant</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Granted" value={formatDate(details.connection?.granted_at)} />
                      <DetailRow label="Token expires" value={formatDate(details.connection?.expires_at)} />
                      <DetailRow label="Grant status" value={details.connection?.status ?? '—'} />
                      <DetailRow label="Scope" value={details.connection?.scope ?? '—'} />
                    </dl>
                  </section>
                )}

                {isMt5(detailsFor) && (
                  <section>
                    <h3 className="desk-label mb-2">Symbol mapping</h3>
                    {!can(role, 'trade') ? (
                      <p className="text-sm text-ink-faint">Traders and admins can see the mapping.</p>
                    ) : aliasesError ? (
                      <p className="text-sm text-loss-deep">{aliasesError}</p>
                    ) : !aliases ? (
                      <p className="text-sm text-ink-faint">Loading the mapping…</p>
                    ) : (
                      <>
                        {aliases.aliases.length === 0 ? (
                          <p className="text-sm text-ink-faint">
                            Nothing mapped yet — the terminal sends its symbol list when
                            the EA first connects.
                          </p>
                        ) : (
                          <ul className="space-y-1.5 text-sm">
                            {aliases.aliases.map((row) => (
                              <li key={row.canonical} className="flex items-center justify-between gap-3">
                                <span className="num text-ink">{row.canonical}</span>
                                <span className="flex items-center gap-2">
                                  {can(role, 'control') ? (
                                    <input
                                      type="text"
                                      aria-label={`Broker symbol for ${row.canonical}`}
                                      list="mt5-broker-symbols"
                                      value={aliasDrafts[row.canonical] ?? row.broker_name}
                                      onChange={(e) =>
                                        setAliasDrafts((prev) => ({ ...prev, [row.canonical]: e.target.value }))}
                                      onBlur={() => handleAliasBlur(row)}
                                      className="num w-28 rounded border border-transparent hover:border-line-strong focus:border-line-strong px-2 py-1 text-sm bg-transparent text-right"
                                    />
                                  ) : (
                                    <span className="num text-ink">{row.broker_name}</span>
                                  )}
                                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                                    row.source === 'manual' ? 'bg-brand-wash text-ink' : 'bg-line text-ink-soft'
                                  }`}>
                                    {row.source}
                                  </span>
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                        {can(role, 'control') && (
                          <div className="mt-3 flex items-center gap-2">
                            <input
                              type="text"
                              aria-label="New canonical symbol"
                              placeholder="XAUUSD"
                              value={newAlias.canonical}
                              onChange={(e) => setNewAlias((prev) => ({ ...prev, canonical: e.target.value }))}
                              className="num w-24 rounded border border-line-strong px-2 py-1 text-sm bg-card"
                            />
                            <span className="text-ink-faint">→</span>
                            <input
                              type="text"
                              aria-label="New broker symbol"
                              placeholder="GOLD.r"
                              list="mt5-broker-symbols"
                              value={newAlias.broker_name}
                              onChange={(e) => setNewAlias((prev) => ({ ...prev, broker_name: e.target.value }))}
                              className="num w-28 rounded border border-line-strong px-2 py-1 text-sm bg-card"
                            />
                            <button
                              type="button"
                              onClick={handleAddAlias}
                              disabled={!newAlias.canonical.trim() || !newAlias.broker_name.trim()}
                              className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors disabled:opacity-50"
                            >
                              Add mapping
                            </button>
                          </div>
                        )}
                        <datalist id="mt5-broker-symbols">
                          {aliases.broker_symbols.map((name) => (
                            <option key={name} value={name} />
                          ))}
                        </datalist>
                        <p className="mt-2 text-xs text-ink-faint">
                          Clearing a broker name removes the mapping; a manual entry is
                          never overwritten by the auto-matcher.
                        </p>
                      </>
                    )}
                  </section>
                )}

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

/** The copier's view of the terminal, as the accounts row reports it:
 *  connected (a report within 15 s), offline (with when it was last heard
 *  from), or never reported -- the EA has not been installed yet. */
function Mt5ConnectionBadge({ account }: { account: Account }) {
  switch (account.connection_status) {
    case 'connected':
      return (
        <span className="text-xs font-medium px-2 py-0.5 rounded bg-profit-wash text-profit-deep">
          Connected
        </span>
      )
    case 'offline':
      return (
        <span className="text-xs font-medium px-2 py-0.5 rounded bg-warn-wash text-warn-deep">
          Offline · last seen {formatWhen(account.mt5?.last_seen_at)}
        </span>
      )
    default:
      return (
        <span className="text-xs font-medium px-2 py-0.5 rounded bg-line text-ink-soft">
          Waiting for the terminal
        </span>
      )
  }
}
