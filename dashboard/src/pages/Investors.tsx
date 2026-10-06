import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, money, signed } from '../lib/format'
import { accountName, moneyOrDash, walletLabel } from '../lib/investor'
import Badge from '../components/Badge'
import Banner from '../components/Banner'
import Card from '../components/Card'
import Loading from '../components/Loading'
import Menu from '../components/Menu'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import AccountsDrawer from './investors/AccountsDrawer'
import AdjustDialog from './investors/AdjustDialog'
import GrantBonusDialog from './investors/GrantBonusDialog'
import LedgerDrawer from './investors/LedgerDrawer'
import PackagesTab from './investors/PackagesTab'
import PaymentMethodsTab, { type Runner } from './investors/PaymentMethodsTab'
import { kycBadge, kycLabel } from '../lib/identity'
import type { Account, InvestorRow, PaymentMethod, PortalSettings } from '../lib/types'

const POLL_MS = 10000

type Tab = 'investors' | 'methods' | 'packages'

type PendingKind = keyof InvestorRow['pending']

const PENDING_WORD: Record<PendingKind, [string, string]> = {
  deposits: ['deposit', 'deposits'],
  withdrawals: ['withdrawal', 'withdrawals'],
  transfers: ['transfer', 'transfers'],
  payout_destinations: ['payout account', 'payout accounts'],
}

/** "2 deposits" "1 withdrawal": one chip per non-zero open count, each a
 *  link into the Requests desk on that tab. */
function OpenRequests({ orgId, pending }: { orgId: number; pending: InvestorRow['pending'] }) {
  const kinds = (Object.keys(PENDING_WORD) as PendingKind[]).filter((k) => pending[k] > 0)
  if (kinds.length === 0) return <span className="text-ink-faint">—</span>
  return (
    <div className="flex flex-wrap gap-1.5">
      {kinds.map((k) => (
        <Link key={k} to={`/org/${orgId}/requests?tab=${k}`} className="inline-flex rounded-full">
          <Badge tone="warn">{pending[k]} {PENDING_WORD[k][pending[k] === 1 ? 0 : 1]}</Badge>
        </Link>
      ))}
    </div>
  )
}

/** "not linked", "Growth", or "Growth · 2 accounts". */
function accountsText(r: InvestorRow): string {
  const [first] = r.accounts
  if (!first) return 'not linked'
  return r.accounts.length === 1 ? accountName(first) : `${accountName(first)} · ${r.accounts.length} accounts`
}

/** The accounts' equity together; null while any is unknown, or there is none. */
function totalEquity(r: InvestorRow): number | null {
  if (r.accounts.length === 0 || r.accounts.some((a) => a.equity == null)) return null
  return r.accounts.reduce((sum, a) => sum + (a.equity ?? 0), 0)
}

export default function Investors() {
  const { orgId, role } = useOrg()
  const [rows, setRows] = useState<InvestorRow[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [settings, setSettings] = useState<PortalSettings | null>(null)
  const [tab, setTab] = useState<Tab>('investors')
  const [ledgerFor, setLedgerFor] = useState<InvestorRow | null>(null)
  const [adjustFor, setAdjustFor] = useState<InvestorRow | null>(null)
  const [bonusFor, setBonusFor] = useState<InvestorRow | null>(null)
  const [accountsFor, setAccountsFor] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Until the first load lands, the empty lists are unknown, not empty.
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [r, a, m, s] = await Promise.all([
        orgApi<InvestorRow[]>(orgId, 'investors'),
        orgApi<Account[]>(orgId, 'accounts'),
        orgApi<PaymentMethod[]>(orgId, 'payment-methods'),
        orgApi<PortalSettings>(orgId, 'portal-settings'),
      ])
      setRows(r); setAccounts(a); setMethods(m); setSettings(s)
      setError(null)
      setLoaded(true)
    } catch (err) {
      setError(errorText(err, 'Could not load investors'))
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])
  useLiveRefresh(refresh, orgId)

  const control = can(role, 'control')

  const run: Runner = async (fn, done) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(done)
      await refresh()
    } catch (err) {
      setError(errorText(err, 'The action failed'))
    } finally {
      setBusy(false)
    }
  }

  // What may be linked: any platform, never the master, nobody's yet (the
  // api refuses the rest anyway).
  const linkedIds = new Set(rows.flatMap((r) => r.accounts.map((a) => a.account_id)))
  const linkable = accounts.filter((a) =>
    a.role !== 'master' && !linkedIds.has(a.ctid_trader_account_id))

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Investors"
        subtitle="Who invests through this workspace, what their wallets hold, and where their deposits land. Decisions on their requests live on the Requests desk."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      <Tabs
        idBase="investors"
        label="Investors"
        value={tab}
        onChange={(k) => setTab(k as Tab)}
        items={[
          { key: 'investors', label: 'Investors' },
          { key: 'methods', label: 'Portal settings' },
          { key: 'packages', label: 'Account packages' },
        ]}
      />

      {!loaded ? (
        // An error before the first load shows the banner above, not an
        // endless skeleton.
        !error && <Loading lines={6} label="Loading investors" />
      ) : (
        <div id="investors-panel" role="tabpanel" aria-labelledby={`investors-tab-${tab}`}>
          {tab === 'investors' ? (
            <Card title="Investor accounts" inset>
              <div className="overflow-x-auto">
                <table className="stack-table w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-line">
                      <th className="desk-label px-5 py-2 font-semibold">Name</th>
                      <th className="desk-label px-5 py-2 font-semibold">Email</th>
                      <th className="desk-label px-5 py-2 font-semibold">Verification</th>
                      <th className="desk-label px-5 py-2 font-semibold text-right">My wallet</th>
                      <th className="desk-label px-5 py-2 font-semibold text-right">Equity</th>
                      <th className="desk-label px-5 py-2 font-semibold">Accounts</th>
                      <th className="desk-label px-5 py-2 font-semibold">Open requests</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td colSpan={8} className="text-center py-8 text-ink-faint">
                        No investors yet — invite one from Members with the Investor role.
                      </td></tr>
                    )}
                    {rows.map((r) => (
                      <tr key={r.user_id} className="border-b border-line last:border-0 align-top">
                        <td data-label="Name" className="px-5 py-2.5 text-ink">{r.display_name}</td>
                        <td data-label="Email" className="px-5 py-2.5 text-ink-soft">{r.email}</td>
                        <td data-label="Verification" className="px-5 py-2.5">
                          <Badge tone={kycBadge(r.kyc_status)}>{kycLabel(r.kyc_status)}</Badge>
                        </td>
                        <td data-label="My wallet" className="px-5 py-2.5 text-right">
                          {/* One child per cell: the stacked phone layout is a
                              flex row of label and value. */}
                          <div className="min-w-0">
                            <div className="num text-ink">{money(r.balances.main)}</div>
                            <div className="num text-xs text-ink-soft">
                              {`${r.on_hold > 0 ? `on hold ${money(r.on_hold)} · ` : ''}available ${money(r.available)}`}
                            </div>
                          </div>
                        </td>
                        <td data-label="Equity" className="num px-5 py-2.5 text-right">{moneyOrDash(totalEquity(r))}</td>
                        <td data-label="Accounts" className="px-5 py-2.5">{accountsText(r)}</td>
                        <td data-label="Open requests" className="px-5 py-2.5">
                          <OpenRequests orgId={orgId} pending={r.pending} />
                        </td>
                        <td className="px-5 py-2.5 text-right">
                          {control && (
                            <Menu label={`Actions for ${r.email}`} items={[
                              { key: 'accounts', label: 'Manage accounts', onSelect: () => setAccountsFor(r.user_id) },
                              { key: 'ledger', label: 'View ledger', onSelect: () => setLedgerFor(r) },
                              { key: 'adjust', label: 'Adjust balance', disabled: busy, onSelect: () => setAdjustFor(r) },
                              { key: 'bonus', label: 'Grant bonus', disabled: busy, onSelect: () => setBonusFor(r) },
                            ]} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : tab === 'methods' ? (
            <PaymentMethodsTab orgId={orgId} control={control} methods={methods}
                               settings={settings} busy={busy} run={run} />
          ) : (
            <PackagesTab orgId={orgId} control={control} />
          )}
        </div>
      )}

      <AccountsDrawer key={accountsFor ?? 'none-accounts'}
                      investor={rows.find((r) => r.user_id === accountsFor) ?? null}
                      linkable={linkable} orgId={orgId} run={run}
                      onClose={() => setAccountsFor(null)} />

      {/* Keyed on the investor so each opening starts with a clean filter. */}
      <LedgerDrawer key={ledgerFor?.user_id ?? 'none'} orgId={orgId} investor={ledgerFor}
                    onClose={() => setLedgerFor(null)} />
      <AdjustDialog orgId={orgId} investor={adjustFor} onCancel={() => setAdjustFor(null)}
                    onPosted={async (entry) => {
                      setAdjustFor(null)
                      setError(null)
                      setNotice(`Adjustment of ${signed(entry.amount)} posted to ${walletLabel(entry.wallet)}`)
                      await refresh()
                    }} />
      <GrantBonusDialog orgId={orgId} investor={bonusFor} onCancel={() => setBonusFor(null)}
                        onGranted={async (b) => {
                          const name = bonusFor?.display_name ?? 'the investor'
                          setBonusFor(null)
                          setError(null)
                          setNotice(b.amount > 0
                            ? `Bonus of ${money(b.amount)} paid to ${name}'s Credit wallet`
                            : `Bonus of ${money(-b.amount)} taken back from ${name}'s Credit wallet`)
                          await refresh()
                        }} />
    </div>
  )
}
