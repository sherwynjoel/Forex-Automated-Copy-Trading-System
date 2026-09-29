import { useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { can } from '../../lib/roles'
import { accountName, isMt5 } from '../../lib/platform'
import type {
  Account, AccountDetails, ApiState, CloseAllResult, Mt5AccountCreated, StateSnapshot,
  SymbolAliases,
} from '../../lib/types'

// How long the green "Flattened ✓" confirmation stays on a row.
const FLATTEN_DONE_MS = 5000

export type FlattenState = 'busy' | 'done' | 'error'

/** A key is shown exactly once. This is held only while its dialog is open
 *  and dropped the moment the dialog closes -- never stored anywhere else. */
export interface KeyReveal {
  title: string
  key: string
  /** Present on creation; a rotation only replaces the key. */
  download_url: string | null
  install: string[]
}

/** The drawer's edit form. Empty strings mean "none", exactly as the old
 *  inline inputs sent them (an empty cutoff_date clears the cutoff). */
export interface AccountDraft {
  nickname: string
  role: string
  enabled: boolean
  cutoff_date: string
}

export type EditField = keyof AccountDraft

/** One field whose PATCH the server refused, with the reason to show. */
export interface SaveFailure {
  field: EditField
  message: string
}

/** What a Save did that the form must reflect: fields that failed keep the
 *  typed value and are reported in place, and a promotion the operator
 *  backed out of leaves the role select where the server still has it. */
export interface SaveOutcome {
  ok: boolean
  failed: SaveFailure[]
  promoteCancelled: boolean
}

/** The one confirm dialog that can be open. Master promotion re-shapes the
 *  whole fleet and MT5 removal is permanent, so both always confirm first. */
export type PageDialog =
  | { kind: 'disconnect' | 'remove' | 'flatten' | 'promote' | 'rotate'; account: Account }
  | { kind: 'add-mt5'; nickname: string }
  | null

interface ListState { accounts: Account[]; equity: StateSnapshot; isLoading: boolean }
interface Feedback { error: string | null; notice: string | null }
interface RowState {
  pending: Set<number>
  flatten: Record<number, FlattenState>
}
interface DrawerState {
  accountId: number | null
  details: AccountDetails | null
  detailsError: string | null
  aliases: SymbolAliases | null
  aliasesError: string | null
}

const CLOSED_DRAWER: DrawerState = {
  accountId: null, details: null, detailsError: null, aliases: null, aliasesError: null,
}

/** A cTrader account whose grant is no longer active: the same condition
 *  that paints its health amber instead of "Active". MT5 has no grant. */
export function needsRegrant(account: Account): boolean {
  return !isMt5(account) && account.connection_status !== 'active'
}

export function draftOf(account: Account): AccountDraft {
  return {
    nickname: account.nickname ?? '',
    role: account.role,
    enabled: account.enabled,
    cutoff_date: account.cutoff_date ?? '',
  }
}

function reason(err: unknown, fallback = 'unknown'): string {
  return err instanceof Error ? err.message : fallback
}

export function useAccountsPage() {
  const { orgId, role } = useOrg()
  const canControl = can(role, 'control')
  const canTrade = can(role, 'trade')

  const [list, setList] = useState<ListState>({ accounts: [], equity: {}, isLoading: true })
  const [feedback, setFeedback] = useState<Feedback>({ error: null, notice: null })
  const [rows, setRows] = useState<RowState>({ pending: new Set(), flatten: {} })
  const [dialog, setDialog] = useState<PageDialog>(null)
  const [busy, setBusy] = useState(false)
  const [keyReveal, setKeyReveal] = useState<KeyReveal | null>(null)
  const [drawer, setDrawer] = useState<DrawerState>(CLOSED_DRAWER)
  // Resolves the promise saveEdits awaits while the promote dialog is open.
  const promoteResolver = useRef<((confirmed: boolean) => void) | null>(null)
  // The account the drawer is showing; late answers for another are dropped.
  const drawerIdRef = useRef<number | null>(null)

  const showError = (error: string | null) => setFeedback((prev) => ({ ...prev, error }))
  const showNotice = (notice: string | null) => setFeedback((prev) => ({ ...prev, notice }))

  const setFlatten = (accountId: number, state: FlattenState | null) =>
    setRows((prev) => {
      const flatten = { ...prev.flatten }
      if (state) flatten[accountId] = state
      else delete flatten[accountId]
      return { ...prev, flatten }
    })

  // Live equity per account, keyed by account id. Held apart from the rows
  // because it comes from the engine, not the database: the accounts table
  // has no balance column, and a stale number here would be worse than none.
  const fetchEquity = async () => {
    try {
      const envelope = await orgApi<ApiState>(orgId, 'state')
      setList((prev) => ({ ...prev, equity: envelope.accounts ?? {} }))
    } catch {
      // The engine being unreachable must not blank the accounts list --
      // this screen is how an operator disconnects or flattens an account,
      // and it has to work when the copier is the thing that is broken.
      setList((prev) => ({ ...prev, equity: {} }))
    }
  }

  const fetchAccounts = async () => {
    try {
      const data = await orgApi<Account[]>(orgId, 'accounts')
      setList((prev) => ({ ...prev, accounts: data || [] }))
      showError(null)
    } catch (err) {
      showError(reason(err, 'Failed to load accounts'))
    } finally {
      setList((prev) => ({ ...prev, isLoading: false }))
    }
  }

  useEffect(() => {
    fetchAccounts()
    fetchEquity()
  }, [orgId])

  // Refetch when the OAuth round trip returns focus to this window.
  useEffect(() => {
    const handleFocus = () => {
      fetchAccounts()
      fetchEquity()
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [orgId])

  const withPending = async (accountId: number, action: () => Promise<void>) => {
    setRows((prev) => ({ ...prev, pending: new Set([...prev.pending, accountId]) }))
    try {
      await action()
    } finally {
      setRows((prev) => {
        const pending = new Set(prev.pending)
        pending.delete(accountId)
        return { ...prev, pending }
      })
    }
  }

  const connectOAuth = () => {
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

  // ---- Edit (the drawer's Save changes) ----

  /** One PATCH; answers the reason it failed, or null when it saved. */
  const patchField = async (accountId: number, body: Record<string, unknown>): Promise<string | null> => {
    try {
      await orgApi(orgId, `accounts/${accountId}`, { method: 'PATCH', body: JSON.stringify(body) })
      return null
    } catch (err) {
      return reason(err)
    }
  }

  const askPromote = (account: Account) => new Promise<boolean>((resolve) => {
    promoteResolver.current = resolve
    setDialog({ kind: 'promote', account })
  })

  const resolvePromote = (confirmed: boolean) => {
    const resolve = promoteResolver.current
    promoteResolver.current = null
    setDialog(null)
    resolve?.(confirmed)
  }

  /** One PATCH per changed field, each with the single-key body the old
   *  blur-save sent, then one reload. Master waits for its confirmation.
   *  A refused field does not stop the others; it is reported back so the
   *  drawer can say so in place (the page alert sits behind the drawer). */
  const saveEdits = async (account: Account, draft: AccountDraft): Promise<SaveOutcome> => {
    const accountId = account.ctid_trader_account_id
    const failed: SaveFailure[] = []
    let promoteCancelled = false
    const send = async (field: EditField, body: Record<string, unknown>) => {
      const message = await patchField(accountId, body)
      if (message != null) failed.push({ field, message })
    }
    await withPending(accountId, async () => {
      let sent = false
      if (draft.nickname !== (account.nickname ?? '')) {
        await send('nickname', { nickname: draft.nickname })
        sent = true
      }
      if (draft.cutoff_date !== (account.cutoff_date ?? '')) {
        // An empty value clears the cutoff (and with it the reminder).
        await send('cutoff_date', { cutoff_date: draft.cutoff_date })
        sent = true
      }
      if (draft.enabled !== account.enabled) {
        await send('enabled', { enabled: draft.enabled })
        sent = true
      }
      if (draft.role !== account.role) {
        if (draft.role === 'master' && !(await askPromote(account))) {
          promoteCancelled = true
        } else {
          const message = await patchField(accountId, { role: draft.role })
          if (message != null) {
            failed.push({
              field: 'role',
              message: message === '409' ? 'A master already exists' : message,
            })
          }
          sent = true
        }
      }
      if (sent) await fetchAccounts()
    })
    return { ok: failed.length === 0, failed, promoteCancelled }
  }

  // ---- Dialogs ----

  const cancelDialog = () => {
    if (dialog?.kind === 'promote') resolvePromote(false)
    else setDialog(null)
  }

  const askAddMt5 = () => setDialog({ kind: 'add-mt5', nickname: '' })
  const setMt5Nickname = (nickname: string) =>
    setDialog((prev) => (prev?.kind === 'add-mt5' ? { ...prev, nickname } : prev))
  const askDisconnect = (account: Account) => setDialog({ kind: 'disconnect', account })
  const askRemove = (account: Account) => setDialog({ kind: 'remove', account })
  const askRotate = (account: Account) => setDialog({ kind: 'rotate', account })
  const askFlatten = (account: Account) => {
    // A failed row's retry starts clean; the dialog decides what happens next.
    setFlatten(account.ctid_trader_account_id, null)
    setDialog({ kind: 'flatten', account })
  }

  const confirmRemove = async () => {
    if (dialog?.kind !== 'remove') return
    const accountId = dialog.account.ctid_trader_account_id
    try {
      setBusy(true)
      await orgApi(orgId, `mt5/accounts/${accountId}`, { method: 'DELETE' })
      showNotice('Account removed. Its terminal key stops working immediately.')
      setDialog(null)
      await fetchAccounts()
    } catch (err) {
      setDialog(null)
      showError(reason(err, 'Failed to remove account'))
    } finally {
      setBusy(false)
    }
  }

  const confirmDisconnect = async () => {
    if (dialog?.kind !== 'disconnect') return
    const accountId = dialog.account.ctid_trader_account_id
    try {
      setBusy(true)
      const result = await orgApi<{ accounts_removed: number }>(
        orgId, `accounts/${accountId}/connection`, { method: 'DELETE' })
      showNotice(
        `Disconnected. ${result.accounts_removed} account${result.accounts_removed === 1 ? '' : 's'} ` +
        'removed. The token stays revocable at ctrader.com.')
      setDialog(null)
      await fetchAccounts()
    } catch (err) {
      setDialog(null)
      showError(reason(err, 'Failed to disconnect account'))
    } finally {
      setBusy(false)
    }
  }

  const confirmAddMt5 = async () => {
    if (dialog?.kind !== 'add-mt5') return
    const nickname = dialog.nickname
    try {
      setBusy(true)
      const result = await orgApi<Mt5AccountCreated>(orgId, 'mt5/accounts', {
        method: 'POST',
        body: JSON.stringify({ nickname: nickname.trim() }),
      })
      setDialog(null)
      setKeyReveal({
        title: 'MT5 account added — install the EA',
        key: result.key,
        download_url: result.download_url,
        install: result.install,
      })
      await fetchAccounts()
    } catch (err) {
      setDialog(null)
      showError(`Could not add the MT5 account (${reason(err)})`)
    } finally {
      setBusy(false)
    }
  }

  const confirmRotate = async () => {
    if (dialog?.kind !== 'rotate') return
    const account = dialog.account
    try {
      setBusy(true)
      const result = await orgApi<{ key: string }>(
        orgId, `mt5/accounts/${account.ctid_trader_account_id}/key`, { method: 'POST' })
      setDialog(null)
      setKeyReveal({
        title: `New key for ${accountName(account)}`,
        key: result.key,
        download_url: null,
        install: [],
      })
    } catch (err) {
      setDialog(null)
      showError(`Could not rotate the key (${reason(err)})`)
    } finally {
      setBusy(false)
    }
  }

  const confirmFlatten = async () => {
    if (dialog?.kind !== 'flatten') return
    const account = dialog.account
    const accountId = account.ctid_trader_account_id
    // Close the dialog right away; progress and outcome live on the row so
    // it is always clear WHICH account is being flattened.
    setDialog(null)
    showError(null)
    setFlatten(accountId, 'busy')
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
        setFlatten(accountId, 'error')
        showError(
          `Account ${account.trader_login}: closed ${summary.positions_closed}, but ` +
          `${stillOpen} position${stillOpen === 1 ? '' : 's'} could not be closed. ` +
          `You are still exposed — close ${stillOpen === 1 ? 'it' : 'them'} in the platform.`)
        return
      }
      showNotice(
        `Closed ${summary.positions_closed} position${summary.positions_closed === 1 ? '' : 's'} ` +
        `and cancelled ${summary.orders_cancelled} order${summary.orders_cancelled === 1 ? '' : 's'} ` +
        `on account ${account.trader_login}. Verified flat.`)
      setFlatten(accountId, 'done')
      window.setTimeout(() => {
        setRows((prev) => {
          if (prev.flatten[accountId] !== 'done') return prev
          const flatten = { ...prev.flatten }
          delete flatten[accountId]
          return { ...prev, flatten }
        })
      }, FLATTEN_DONE_MS)
    } catch (err) {
      setFlatten(accountId, 'error')
      showError(`Flatten failed on account ${account.trader_login}: ${reason(err, 'unknown error')}`)
    }
  }

  // The key leaves memory here; it is never shown again.
  const closeKeyReveal = () => setKeyReveal(null)

  // ---- Details drawer ----

  const loadAliases = async (accountId: number) => {
    try {
      const aliases = await orgApi<SymbolAliases>(orgId, `accounts/${accountId}/symbol-aliases`)
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({ ...prev, aliases, aliasesError: null }))
    } catch (err) {
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({
        ...prev, aliasesError: `Could not load the symbol mapping (${reason(err)})`,
      }))
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
      setDrawer((prev) => ({
        ...prev, aliasesError: `Could not save the mapping for ${canonical} (${reason(err)})`,
      }))
    }
  }

  const openDetails = async (account: Account) => {
    const accountId = account.ctid_trader_account_id
    drawerIdRef.current = accountId
    setDrawer({ ...CLOSED_DRAWER, accountId })
    // The mapping lives in the api's database, so it loads even when the
    // copier is down; reading it needs the admin role.
    if (isMt5(account) && canTrade) loadAliases(accountId)
    try {
      const details = await orgApi<AccountDetails>(orgId, `accounts/${accountId}/details`)
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({ ...prev, details }))
    } catch (err) {
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({
        ...prev,
        detailsError:
          `Could not fetch details: ${reason(err, 'unknown error')}. ` +
          (isMt5(account)
            ? 'The copier may be offline or the terminal has not connected yet.'
            : 'The copier may be offline or the account not yet authorized.'),
      }))
    }
  }

  const closeDetails = () => {
    drawerIdRef.current = null
    setDrawer(CLOSED_DRAWER)
  }

  // The drawer reads the account from the live list, so a save or a
  // promotion elsewhere shows up in it at once; a row that disappears
  // (disconnected, removed) closes it.
  const drawerAccount = drawer.accountId == null
    ? null
    : list.accounts.find((a) => a.ctid_trader_account_id === drawer.accountId) ?? null

  return {
    canControl,
    canTrade,
    accounts: list.accounts,
    equity: list.equity,
    isLoading: list.isLoading,
    error: feedback.error,
    notice: feedback.notice,
    dismissNotice: () => showNotice(null),
    reportError: (message: string) => showError(message),
    pending: rows.pending,
    flatten: rows.flatten,
    dialog,
    busy,
    keyReveal,
    drawer: {
      account: drawerAccount,
      details: drawer.details,
      detailsError: drawer.detailsError,
      aliases: drawer.aliases,
      aliasesError: drawer.aliasesError,
    },
    connectOAuth,
    askAddMt5,
    setMt5Nickname,
    confirmAddMt5,
    askFlatten,
    confirmFlatten,
    askDisconnect,
    confirmDisconnect,
    askRemove,
    confirmRemove,
    askRotate,
    confirmRotate,
    resolvePromote,
    cancelDialog,
    closeKeyReveal,
    openDetails,
    closeDetails,
    saveEdits,
    saveAlias,
  }
}

export type AccountsPage = ReturnType<typeof useAccountsPage>
