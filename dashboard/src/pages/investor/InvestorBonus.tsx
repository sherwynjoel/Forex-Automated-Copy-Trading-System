import { useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { ACCOUNT_CURRENCY } from '../../lib/investor'
import { BONUS_SOURCES, BONUS_SOURCE_LABELS } from '../../lib/engagement'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import type { Bonus, InvestorSummary } from '../../lib/types'

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'
const NO_FILTER = { source: '', from: '', to: '' }

/** The bonuses tail with only the filters that are set. */
function bonusesQuery(f: typeof NO_FILTER): string {
  const p = new URLSearchParams()
  if (f.source) p.set('source', f.source)
  if (f.from) p.set('from', f.from)
  if (f.to) p.set('to', f.to)
  const s = p.toString()
  return s ? `investor/bonuses?${s}` : 'investor/bonuses'
}

/**
 * The investor's bonuses: the Credit wallet they land in (which only ever
 * moves on to a trading account) and the history with Source and date
 * filters.
 */
export default function InvestorBonus() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<Bonus[] | null>(null)
  const [draft, setDraft] = useState(NO_FILTER)
  const [applied, setApplied] = useState(NO_FILTER)
  const [error, setError] = useState<string | null>(null)
  // Bumped per load: Apply (or an org switch) drops an older answer.
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    Promise.all([
      orgApi<InvestorSummary>(orgId, 'investor/summary'),
      orgApi<Bonus[]>(orgId, bonusesQuery(applied)),
    ]).then(
      ([s, b]) => {
        if (mine !== seq.current) return
        setSummary(s); setRows(b); setError(null)
      },
      (err) => {
        if (mine !== seq.current) return
        setRows((r) => r ?? [])
        setError(errorText(err, 'Could not load your bonuses'))
      },
    )
  }, [orgId, applied])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  const credit = summary?.wallets.credit
  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Bonus"
        subtitle="Bonuses are paid into your Credit wallet. Credit can only be moved to one of your trading accounts; it cannot be withdrawn."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {summary && (
        <Card title="Credit wallet" actions={
          <Button variant="secondary" size="sm" to={`/org/${orgId}/invest/transfer`}>Move to a trading account</Button>
        }>
          <div className="inset p-4 grid gap-3 sm:grid-cols-2 text-sm">
            <div>
              <div className="desk-label">Balance</div>
              <div className="text-2xl font-semibold text-ink"><Money value={credit?.balance ?? null} unit={unit} /></div>
            </div>
            <div>
              <div className="desk-label">Available to move</div>
              <div className="text-ink"><Money value={credit?.available ?? null} unit={unit} /></div>
            </div>
          </div>
        </Card>
      )}
      {/* The filters stay usable while a load is in flight; the seq ref
          drops whichever answer is no longer the newest. */}
      <Card title="Bonus history" inset>
        <form onSubmit={(e) => { e.preventDefault(); setApplied(draft) }}
              className="flex flex-wrap items-end gap-3 p-4 border-b border-line">
          <label className="block">
            <span className="desk-label block mb-1">Source</span>
            <Select aria-label="Source" value={draft.source}
                    onChange={(e) => setDraft({ ...draft, source: e.target.value })}>
              <option value="">All</option>
              {BONUS_SOURCES.map((s) => <option key={s} value={s}>{BONUS_SOURCE_LABELS[s]}</option>)}
            </Select>
          </label>
          <label className="block">
            <span className="desk-label block mb-1">From</span>
            <Input type="date" aria-label="From date" value={draft.from}
                   onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
          </label>
          <label className="block">
            <span className="desk-label block mb-1">To</span>
            <Input type="date" aria-label="To date" value={draft.to}
                   onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
          </label>
          <Button type="submit" variant="secondary" size="sm">Apply</Button>
        </form>
        {rows == null ? (
          <Loading lines={3} label="Loading bonuses" className="p-4" />
        ) : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className={TH}>Date</th>
                  <th className={TH}>Source</th>
                  <th className={`${TH} text-right`}>Amount</th>
                  <th className={TH}>Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={4} className="text-center py-8 text-ink-faint">No bonuses yet</td></tr>
                )}
                {rows.map((b) => (
                  <tr key={b.id} className="border-b border-line last:border-0">
                    <td data-label="Date" className={`num ${TD}`}>{formatWhen(b.created_at)}</td>
                    <td data-label="Source" className={TD}>{BONUS_SOURCE_LABELS[b.source]}</td>
                    <td data-label="Amount" className={`${TD} text-right`}><Money value={b.amount} unit={b.currency} signed /></td>
                    <td data-label="Note" className={`${TD} text-ink-soft`}>{b.note ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
