import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, entryLabel, walletLabel } from '../../lib/investor'
import { kycBadge, kycLabel } from '../../lib/identity'
import { useHiddenBalances } from '../../lib/hideBalances'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money, { HideBalancesToggle } from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import Tabs from '../../components/Tabs'
import { PnlBars } from '../../components/charts'
import NextStep from './NextStep'
import type { InvestorSummary, KycStatus, WalletEntriesPage, WalletEntry } from '../../lib/types'

const POLL_MS = 10000
const DAY_MS = 24 * 3600 * 1000
const RANGES = [
  { key: '7', label: '7D', days: 7 },
  { key: '30', label: '30D', days: 30 },
  { key: '90', label: '90D', days: 90 },
] as const
type RangeKey = typeof RANGES[number]['key']
type Flow = InvestorSummary['cash_flow'][number]

/** "Good morning" before noon, "Good afternoon" before six, then "Good evening". */
export function greeting(hour: number): string {
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

/** ISO date (UTC) of the day `daysBack` days before `now`. */
function isoDay(now: number, daysBack: number): string {
  return new Date(now - daysBack * DAY_MS).toISOString().slice(0, 10)
}

/** cash_flow rows inside the last `days` days (today counts), oldest first. */
export function flowWindow(flow: Flow[], days: number, now = Date.now()): Flow[] {
  const from = isoDay(now, days - 1)
  return flow.filter((f) => f.date >= from).sort((a, b) => (a.date < b.date ? -1 : 1))
}

/** One figure per day for the last seven days, zero on a day without movement. */
export function last7(flow: Flow[], pick: (f: Flow) => number, now = Date.now()): number[] {
  const byDate = new Map(flow.map((f) => [f.date, f]))
  const out: number[] = []
  for (let back = 6; back >= 0; back--) {
    const f = byDate.get(isoDay(now, back))
    out.push(f ? pick(f) : 0)
  }
  return out
}

function dayLabel(ms: number): string {
  return new Date(ms).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

/**
 * The four wallet stat tiles. StatTile's `value` is a string, so this reads
 * the hide-balances flag itself and masks by hand -- and, just as
 * importantly, it does so in a component of its own: the flag is a
 * cross-tab broadcast (any mounted subscriber can be told to re-render at
 * any time, including from a test's teardown), and the page above calls
 * useOrg(), which must never be re-invoked outside its own lifecycle.
 * Keeping the subscription here means a broadcast only ever re-renders
 * this slice.
 */
function WalletTiles({ summary, unit }: { summary: InvestorSummary; unit: string }) {
  const [hidden] = useHiddenBalances()
  const tile = (v: number) => (hidden ? '••••' : money(v, unit))
  // A sparkline still shows the SHAPE of a figure (a spike three days ago)
  // even once the number reads "••••", so it is dropped along with the
  // figure -- not just recolored -- whenever balances are hidden.
  const spark = (values: number[], label: string) =>
    hidden ? '••••' : <Sparkline values={values} label={label} />
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <StatTile label="My wallet" value={tile(summary.wallets.main.available)} tone="brand"
                sub={spark(last7(summary.cash_flow, (f) => f.deposits - f.withdrawals), 'Net flow, last 7 days')} />
      <StatTile label="Total deposited" value={tile(summary.totals.deposited)}
                sub={spark(last7(summary.cash_flow, (f) => f.deposits), 'Deposits, last 7 days')} />
      <StatTile label="Total withdrawn" value={tile(summary.totals.withdrawn)}
                sub={spark(last7(summary.cash_flow, (f) => f.withdrawals), 'Withdrawals, last 7 days')} />
      {/* cash_flow carries no transfer series, so this tile says the split in words. */}
      <StatTile label="Total transferred"
                value={tile(summary.totals.transferred_in + summary.totals.transferred_out)}
                sub={hidden ? '••••'
                  : `${money(summary.totals.transferred_out, unit)} to trading · ${money(summary.totals.transferred_in, unit)} back`} />
    </div>
  )
}

/**
 * The cash-flow bars. Reads the hide-balances flag itself, for the same
 * reason WalletTiles does (a broadcast must only re-render this slice, not
 * the page, which calls useOrg()) -- and because the bars are a chart of
 * money values, they are dropped entirely while hidden, replaced by a
 * neutral placeholder; the tabs and the totals strip around it stay.
 */
function CashFlowChart({ buckets }: { buckets: { week_start: number; gross_pnl: number; trades: number }[] }) {
  const [hidden] = useHiddenBalances()
  if (hidden) {
    return <p className="py-8 text-center text-xs text-ink-faint">Chart hidden while balances are hidden</p>
  }
  if (buckets.length === 0) {
    return <p className="py-8 text-center text-xs text-ink-faint">No deposits or withdrawals in this range</p>
  }
  // Deposits rise above the baseline, withdrawals fall below it; a day with
  // both shows its net, the strip below gives the split.
  return (
    <PnlBars buckets={buckets} height={160} label="Deposits and withdrawals by day"
             bucketLabel={dayLabel} countNoun="movement" />
  )
}

/** A 96x24 line over seven daily figures with a drawn zero baseline; the
 *  tile's number carries the value, the line only shows the shape. */
function Sparkline({ values, label }: { values: number[]; label: string }) {
  const w = 96, h = 24, pad = 2
  const lo = Math.min(0, ...values)
  const hi = Math.max(0, ...values)
  const span = hi - lo || 1
  const yOf = (v: number) => pad + (h - 2 * pad) * (1 - (v - lo) / span)
  const step = values.length > 1 ? w / (values.length - 1) : 0
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${yOf(v).toFixed(1)}`).join(' ')
  return (
    <svg width={w} height={h} role="img" aria-label={label} className="mt-1 block">
      <line x1={0} x2={w} y1={yOf(0)} y2={yOf(0)} stroke="var(--color-line)" strokeWidth={1} />
      <polyline points={points} fill="none" stroke="var(--color-brand)" strokeWidth={1.5}
                strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

const KYC_TEXT: Record<KycStatus, string> = {
  draft: 'Complete your profile and upload your ID to open a trading account.',
  submitted: 'An admin is reviewing your documents.',
  approved: 'You are verified.',
  rejected: 'Your verification was rejected. Open your profile to see why and submit again.',
}

/** The reference's KYC ring, as one card: status, one sentence, one link. */
function VerificationCard({ status, linked, base }: { status: KycStatus; linked: boolean; base: string }) {
  const action = status === 'draft' || status === 'rejected' ? { label: 'Verify now', to: `${base}/profile` }
    : status === 'submitted' ? { label: 'View profile', to: `${base}/profile` }
    : linked ? null
    : { label: 'Open account', to: `${base}/open-account` }
  return (
    <Card title="Identity verification" actions={<Badge tone={kycBadge(status)}>{kycLabel(status)}</Badge>}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-soft">{KYC_TEXT[status]}</p>
        {action && <Button variant="secondary" size="sm" to={action.to}>{action.label}</Button>}
      </div>
    </Card>
  )
}

export default function InvestorDashboard() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [entries, setEntries] = useState<WalletEntry[]>([])
  const [range, setRange] = useState<RangeKey>('30')
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, page] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<WalletEntriesPage>(orgId, 'investor/wallet-entries?limit=8'),
      ])
      setSummary(s)
      setEntries(page.entries)
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your dashboard'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY

  const days = RANGES.find((r) => r.key === range)!.days
  const flow = summary ? flowWindow(summary.cash_flow, days) : []
  const buckets = flow.map((f) => ({
    week_start: Date.parse(`${f.date}T00:00:00Z`),
    gross_pnl: f.deposits - f.withdrawals,
    trades: (f.deposits > 0 ? 1 : 0) + (f.withdrawals > 0 ? 1 : 0),
  }))
  const totalIn = flow.reduce((s, f) => s + f.deposits, 0)
  const totalOut = flow.reduce((s, f) => s + f.withdrawals, 0)

  const pendingLines = summary ? [
    { n: summary.pending.deposits, noun: 'deposit', what: 'awaiting confirmation', link: 'Deposits', to: 'deposit' },
    { n: summary.pending.withdrawals, noun: 'withdrawal', what: 'in progress', link: 'Withdrawals', to: 'withdraw' },
    { n: summary.pending.transfers, noun: 'transfer', what: 'in progress', link: 'Transfers', to: 'transfer' },
    { n: summary.pending.payout_destinations, noun: 'payout account', what: 'awaiting approval', link: 'Payout accounts', to: 'payout-accounts' },
  ].filter((p) => p.n > 0) : []

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Dashboard" subtitle={summary?.org.name} />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!loaded && <Loading lines={4} />}

      {summary && (
        <>
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-3">
                <p className="text-lg font-semibold text-ink">
                  {`${greeting(new Date().getHours())}, ${summary.investor.first_name}`}
                </p>
                <div className="inset p-4 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="desk-label">Wallet balance</span>
                    <HideBalancesToggle />
                  </div>
                  <div className="text-3xl font-semibold text-ink">
                    <Money value={summary.wallets.main.available} unit={unit} />
                  </div>
                  {summary.wallets.main.on_hold > 0 && (
                    <p className="text-xs text-ink-soft">
                      <Money value={summary.wallets.main.on_hold} unit={unit} /> on hold for open requests
                    </p>
                  )}
                  {summary.profit != null && (
                    <Badge tone={summary.profit < 0 ? 'loss' : 'profit'}>
                      <Money value={summary.profit} unit={unit} signed />{' '}lifetime P&L
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button to={`${base}/deposit`}>Deposit</Button>
                <Button variant="secondary" to={`${base}/withdraw`}>Withdraw</Button>
                <Button variant="secondary" to={`${base}/transfer`}>Transfer</Button>
              </div>
            </div>
          </Card>

          <VerificationCard status={summary.kyc_status} linked={summary.link_state === 'linked'} base={base} />

          <WalletTiles summary={summary} unit={unit} />

          {summary.link_state === 'linked' && summary.account ? (
            <Card title="Trading account"
                  actions={<Button variant="ghost" size="sm" to={`${base}/account`}>View account</Button>}>
              <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                <div><dt className="desk-label">Account</dt>
                  <dd className="text-ink">{summary.account.nickname ?? summary.account.account_id}</dd></div>
                <div><dt className="desk-label">Platform</dt>
                  <dd className="text-ink uppercase">{summary.account.platform}</dd></div>
                <div><dt className="desk-label">Connection</dt>
                  <dd className={summary.account.connected ? 'text-profit' : 'text-warn-deep'}>
                    {summary.account.connected ? 'connected' : 'terminal offline'}
                  </dd></div>
                <div><dt className="desk-label">Open positions</dt>
                  <dd className="num text-ink">{summary.open_positions}</dd></div>
                <div><dt className="desk-label">Equity</dt>
                  <dd className="text-ink"><Money value={summary.equity} unit={unit} />
                    <span className="text-xs text-ink-soft"> {summary.equity_source}</span></dd></div>
                <div><dt className="desk-label">Net funded</dt>
                  <dd className="text-ink"><Money value={summary.net_funded} unit={unit} /></dd></div>
                <div><dt className="desk-label">Profit</dt>
                  <dd className={summary.profit != null && summary.profit < 0 ? 'text-loss' : 'text-profit'}>
                    <Money value={summary.profit} unit={unit} /></dd></div>
                <div><dt className="desk-label">Available to move</dt>
                  <dd className="text-ink"><Money value={summary.account_available} unit={unit} /></dd></div>
              </dl>
            </Card>
          ) : (
            <NextStep title="Your account is being set up">
              Your admin links your trading account. Deposits, withdrawals and wallet transfers work now;
              moving money into the trading account opens once it is linked.
            </NextStep>
          )}

          <Card title="Cash flow"
                actions={<Tabs items={RANGES.map((r) => ({ key: r.key, label: r.label }))} value={range}
                               onChange={(k) => setRange(k as RangeKey)} label="Cash flow range" idBase="cashflow" />}>
            <div id="cashflow-panel" role="tabpanel" aria-labelledby={`cashflow-tab-${range}`} className="space-y-3">
              <div className="inset p-3">
                <CashFlowChart buckets={buckets} />
              </div>
              <dl className="inset p-3 grid grid-cols-3 gap-3 text-sm">
                <div><dt className="desk-label">Deposits</dt>
                  <dd className="text-ink"><Money value={totalIn} unit={unit} /></dd></div>
                <div><dt className="desk-label">Withdrawals</dt>
                  <dd className="text-ink"><Money value={totalOut} unit={unit} /></dd></div>
                <div><dt className="desk-label">Net</dt>
                  <dd className={totalIn - totalOut < 0 ? 'text-loss' : 'text-profit'}>
                    <Money value={totalIn - totalOut} unit={unit} /></dd></div>
              </dl>
            </div>
          </Card>

          {pendingLines.length > 0 && (
            <Card title="Pending requests">
              <ul className="inset divide-y divide-line text-sm">
                {pendingLines.map((p) => (
                  <li key={p.to} className="px-4 py-2.5 flex items-center justify-between gap-3">
                    <span className="text-ink">{`${p.n} ${p.noun}${p.n === 1 ? '' : 's'} ${p.what}`}</span>
                    <Button variant="ghost" size="sm" to={`${base}/${p.to}`}>{p.link}</Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      <Card title="Recent activity" inset
            actions={<Button variant="ghost" size="sm" to={`${base}/transactions`}>View all</Button>}>
        <ul className="divide-y divide-line">
          {entries.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {entries.map((e) => (
            <li key={e.id} className="px-4 py-2.5 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
              {/* Fluid, not a fixed width: on a 360px phone the date takes its
                  own line and the rest wraps beneath it. */}
              <time dateTime={e.created_at} className="num text-ink-soft min-w-0 basis-full sm:basis-auto">
                {formatWhen(e.created_at)}
              </time>
              <span className="text-ink flex-1 min-w-0">{entryLabel(e)}</span>
              <span className="text-xs text-ink-soft">{walletLabel(e.wallet)}</span>
              <Money value={e.amount} unit={e.currency} signed
                     className={e.amount < 0 ? 'text-loss' : 'text-profit'} />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
