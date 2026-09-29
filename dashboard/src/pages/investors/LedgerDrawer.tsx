import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, formatWhen, signed } from '../../lib/format'
import { WALLETS, entryLabel, walletLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Loading from '../../components/Loading'
import Select from '../../components/Select'
import type { InvestorRow, WalletEntriesPage, WalletEntry, WalletKind } from '../../lib/types'

const PAGE = 50

/**
 * One investor's ledger, newest first, in a Drawer: a wallet filter and a
 * Load more button driven by the API's `has_more` / `next_before` cursor.
 * The parent mounts it with `key={investor.user_id}` so every investor
 * starts with a clean filter and an empty list.
 */
export default function LedgerDrawer({ orgId, investor, onClose }: {
  orgId: number
  investor: InvestorRow | null
  onClose: () => void
}) {
  const userId = investor?.user_id ?? null
  const [wallet, setWallet] = useState<'' | WalletKind>('')
  const [entries, setEntries] = useState<WalletEntry[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (before: number | null) => {
    if (userId == null) return
    const q = new URLSearchParams({ limit: String(PAGE) })
    if (wallet) q.set('wallet', wallet)
    if (before != null) q.set('before', String(before))
    setLoading(true)
    try {
      const page = await orgApi<WalletEntriesPage>(orgId, `investors/${userId}/wallet-entries?${q.toString()}`)
      setEntries((cur) => (before == null ? page.entries : [...cur, ...page.entries]))
      setHasMore(page.has_more)
      setNextBefore(page.next_before)
      setError(null)
      setLoaded(true)
    } catch (err) {
      setError(errorText(err, 'Could not load the ledger'))
    } finally {
      setLoading(false)
    }
  }, [orgId, userId, wallet])

  // The first page whenever the drawer opens or the wallet filter changes.
  useEffect(() => {
    setEntries([]); setHasMore(false); setNextBefore(null); setLoaded(false)
    load(null)
  }, [load])

  return (
    <Drawer open={investor != null} title={investor ? `${investor.display_name}'s ledger` : ''} onClose={onClose}>
      <div className="space-y-4">
        <label className="block">
          <span className="desk-label block mb-1">Wallet</span>
          <Select block aria-label="Wallet" value={wallet}
                  onChange={(e) => setWallet(e.target.value as '' | WalletKind)}>
            <option value="">All wallets</option>
            {WALLETS.map((w) => <option key={w} value={w}>{walletLabel(w)}</option>)}
          </Select>
        </label>
        {error && <Banner kind="error">{error}</Banner>}
        {!loaded ? (
          !error && <Loading lines={4} label="Loading ledger" />
        ) : (
          <div className="inset overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-3 py-2 font-semibold">Date</th>
                  <th className="desk-label px-3 py-2 font-semibold">Wallet</th>
                  <th className="desk-label px-3 py-2 font-semibold">Kind</th>
                  <th className="desk-label px-3 py-2 font-semibold text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {entries.length === 0 && (
                  <tr><td colSpan={4} className="text-center py-6 text-ink-faint">No entries yet</td></tr>
                )}
                {entries.map((e) => (
                  <tr key={e.id} className="border-b border-line last:border-0 align-top">
                    <td data-label="Date" className="num px-3 py-2">{formatWhen(e.created_at)}</td>
                    <td data-label="Wallet" className="px-3 py-2">{walletLabel(e.wallet)}</td>
                    <td data-label="Kind" className="px-3 py-2">
                      <div className="min-w-0">
                        <div className="text-ink">{entryLabel(e)}</div>
                        {e.note && <div className="text-xs text-ink-soft">{e.note}</div>}
                      </div>
                    </td>
                    <td data-label="Amount"
                        className={`num px-3 py-2 text-right ${e.amount < 0 ? 'text-loss' : 'text-profit'}`}>
                      {signed(e.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {hasMore && (
          <Button variant="secondary" size="sm" disabled={loading} onClick={() => load(nextBefore)}>
            Load more
          </Button>
        )}
      </div>
    </Drawer>
  )
}
