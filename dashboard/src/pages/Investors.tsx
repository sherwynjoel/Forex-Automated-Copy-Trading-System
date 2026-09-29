import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, money, signed } from '../lib/format'
import { moneyOrDash, walletLabel } from '../lib/investor'
import Badge from '../components/Badge'
import Banner from '../components/Banner'
import Card from '../components/Card'
import Loading from '../components/Loading'
import Menu from '../components/Menu'
import PageHeader from '../components/PageHeader'
import Select from '../components/Select'
import Tabs from '../components/Tabs'
import AdjustDialog from './investors/AdjustDialog'
import LedgerDrawer from './investors/LedgerDrawer'
import PaymentMethodsTab, { type Runner } from './investors/PaymentMethodsTab'
import type { Account, InvestorRow, PaymentMethod, PortalSettings } from '../lib/types'

const POLL_MS = 10000

type Tab = 'investors' | 'methods'

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

export default function Investors() {
  const { orgId, role } = useOrg()
  const [rows, setRows] = useState<InvestorRow[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [settings, setSettings] = useState<PortalSettings | null>(null)
  const [tab, setTab] = useState<Tab>('investors')
  const [ledgerFor, setLedgerFor] = useState<InvestorRow | null>(null)
  const [adjustFor, setAdjustFor] = useState<InvestorRow | null>(null)
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

  const linkAccount = (userId: number, accountId: number | null) => run(async () => {
    await orgApi(orgId, `investors/${userId}/account`, {
      method: 'PUT', body: JSON.stringify({ account_id: accountId }) })
  }, accountId == null ? 'Account unlinked' : 'Account linked')

  const linkedIds = new Set(rows.map((r) => r.account_id).filter((id) => id != null))
  const unlinked = accounts.filter((a) => !linkedIds.has(a.ctid_trader_account_id) && a.role !== 'master')

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
          { key: 'methods', label: 'Payment methods' },
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
                      <th className="desk-label px-5 py-2 font-semibold text-right">My wallet</th>
                      <th className="desk-label px-5 py-2 font-semibold text-right">Equity</th>
                      <th className="desk-label px-5 py-2 font-semibold">Linked account</th>
                      <th className="desk-label px-5 py-2 font-semibold">Open requests</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td colSpan={7} className="text-center py-8 text-ink-faint">
                        No investors yet — invite one from Members with the Investor role.
                      </td></tr>
                    )}
                    {rows.map((r) => (
                      <tr key={r.user_id} className="border-b border-line last:border-0 align-top">
                        <td data-label="Name" className="px-5 py-2.5 text-ink">{r.display_name}</td>
                        <td data-label="Email" className="px-5 py-2.5 text-ink-soft">{r.email}</td>
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
                        <td data-label="Equity" className="num px-5 py-2.5 text-right">{moneyOrDash(r.equity)}</td>
                        <td data-label="Linked account" className="px-5 py-2.5">
                          {control ? (
                            <Select aria-label={`Account for ${r.email}`}
                                    value={r.account_id ?? ''}
                                    disabled={busy}
                                    onChange={(e) => linkAccount(r.user_id, e.target.value ? Number(e.target.value) : null)}>
                              <option value="">not linked</option>
                              {r.account_id != null && (
                                <option value={r.account_id}>{r.nickname ?? r.account_id}</option>
                              )}
                              {unlinked.map((a) => (
                                <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                                  {a.nickname ?? a.trader_login} ({a.platform ?? 'ctrader'})
                                </option>
                              ))}
                            </Select>
                          ) : (r.nickname ?? r.account_id ?? 'not linked')}
                        </td>
                        <td data-label="Open requests" className="px-5 py-2.5">
                          <OpenRequests orgId={orgId} pending={r.pending} />
                        </td>
                        <td className="px-5 py-2.5 text-right">
                          {control && (
                            <Menu label={`Actions for ${r.email}`} items={[
                              { key: 'ledger', label: 'View ledger', onSelect: () => setLedgerFor(r) },
                              { key: 'adjust', label: 'Adjust balance', disabled: busy, onSelect: () => setAdjustFor(r) },
                            ]} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : (
            <PaymentMethodsTab orgId={orgId} control={control} methods={methods}
                               settings={settings} busy={busy} run={run} />
          )}
        </div>
      )}

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
    </div>
  )
}
