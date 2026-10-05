import { useState } from 'react'
import { orgApi } from '../../lib/api'
import { errorText } from '../../lib/format'
import { accountName, moneyOrDash } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Select from '../../components/Select'
import type { Account, InvestorRow } from '../../lib/types'
import type { Runner } from './PaymentMethodsTab'

/**
 * One investor's live accounts: Unlink each, or Link one more from the
 * accounts nobody owns yet (any platform; only fulfil insists on MT5). The
 * link/unlink requests run here, not through `run()`, so a refusal shows
 * inside this drawer and never reaches the page banner; `run()` is called
 * only once a request has already succeeded, to refresh the list and show
 * the notice.
 */
export default function AccountsDrawer({ investor, linkable, orgId, run, onClose }: {
  investor: InvestorRow | null
  linkable: Account[]
  orgId: number
  run: Runner
  onClose: () => void
}) {
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const chosen = linkable.some((a) => String(a.ctid_trader_account_id) === pick)
    ? pick : (linkable[0] ? String(linkable[0].ctid_trader_account_id) : '')

  const link = async () => {
    if (!investor || !chosen) return
    setError(null); setBusy(true)
    try {
      await orgApi(orgId, `investors/${investor.user_id}/accounts`, {
        method: 'POST', body: JSON.stringify({ account_id: Number(chosen) }) })
      await run(async () => {}, 'Account linked')
    } catch (err) {
      setError(errorText(err, 'Could not link the account'))
    } finally {
      setBusy(false)
    }
  }

  const unlink = async (accountId: number) => {
    if (!investor) return
    setError(null); setBusy(true)
    try {
      await orgApi(orgId, `investors/${investor.user_id}/accounts/${accountId}`, { method: 'DELETE' })
      await run(async () => {}, 'Account unlinked')
    } catch (err) {
      setError(errorText(err, 'Could not unlink the account'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer open={investor != null} busy={busy} onClose={onClose}
            title={investor ? `${investor.display_name}'s accounts` : ''}>
      {investor && (
        <div className="space-y-4">
          {error && <Banner kind="error">{error}</Banner>}
          <ul className="inset divide-y divide-line text-sm">
            {investor.accounts.length === 0 && (
              <li className="text-center py-6 text-ink-faint">No accounts linked yet</li>
            )}
            {investor.accounts.map((a) => (
              <li key={a.account_id} className="px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="text-ink flex-1 min-w-0">{accountName(a)}</span>
                <span className="num">{moneyOrDash(a.equity)}</span>
                <Button variant="ghost" tone="loss" size="sm" aria-label={`Unlink ${accountName(a)}`}
                        disabled={busy} onClick={() => void unlink(a.account_id)}>
                  Unlink
                </Button>
              </li>
            ))}
          </ul>
          {linkable.length === 0 ? (
            <p className="text-xs text-ink-soft">Every account is already linked; add one under Accounts first.</p>
          ) : (
            <div className="flex flex-wrap items-end gap-3">
              <label className="block">
                <span className="desk-label block mb-1">Account to link</span>
                <Select aria-label="Account to link" value={chosen} onChange={(e) => setPick(e.target.value)}>
                  {linkable.map((a) => (
                    <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                      {`${a.nickname ?? a.trader_login} (${(a.platform ?? 'ctrader').toUpperCase()})`}
                    </option>
                  ))}
                </Select>
              </label>
              <Button disabled={busy || !chosen} onClick={() => void link()}>Link</Button>
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}
