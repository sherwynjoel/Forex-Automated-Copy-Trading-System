import { useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, money } from '../../lib/format'
import { shortAddress } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Select from '../../components/Select'
import TicketSubjectsCard from './TicketSubjectsCard'
import type { PaymentMethod, PortalSettings } from '../../lib/types'

/** The action envelope the Investors page owns (busy flag, error and
 *  notice banners, then a refresh); the tab borrows it for its mutations. */
export type Runner = (fn: () => Promise<void>, done: string) => Promise<void>

type Kind = PaymentMethod['kind']

interface DetailField { key: string; label: string; optional?: boolean }

/** The detail keys the server requires per kind (portal_admin MethodBody:
 *  crypto coin/network/address, bank bank_name/holder/account_number/code),
 *  in the order the form shows them. */
export const DETAIL_FIELDS: Record<Kind, DetailField[]> = {
  crypto: [
    { key: 'coin', label: 'Coin' },
    { key: 'network', label: 'Network' },
    { key: 'address', label: 'Address' },
    { key: 'memo', label: 'Memo', optional: true },
  ],
  bank: [
    { key: 'bank_name', label: 'Bank name' },
    { key: 'holder', label: 'Account holder' },
    { key: 'account_number', label: 'Account number' },
    { key: 'code', label: 'SWIFT / IFSC code' },
    { key: 'bank_address', label: 'Bank address', optional: true },
    { key: 'country', label: 'Country', optional: true },
  ],
}

interface MethodForm {
  kind: Kind
  label: string
  currency: string
  min_amount: string
  fee_pct: string
  instructions: string
  sort_order: string
  details: Record<string, string>
}

const EMPTY_FORM: MethodForm = {
  kind: 'crypto', label: '', currency: 'USD', min_amount: '0', fee_pct: '0',
  instructions: '', sort_order: '0', details: {},
}

function formOf(m: PaymentMethod): MethodForm {
  return {
    kind: m.kind, label: m.label, currency: m.currency,
    min_amount: String(m.min_amount), fee_pct: String(m.fee_pct),
    instructions: m.instructions ?? '', sort_order: String(m.sort_order),
    details: { ...m.details },
  }
}

/** One line under the label: "USDT · TRC20 · T…ef" or "ICICI Bank ••4543". */
export function methodSummary(m: PaymentMethod): string {
  if (m.kind === 'crypto') {
    return [m.details.coin, m.details.network, m.details.address ? shortAddress(m.details.address) : '']
      .filter(Boolean).join(' · ')
  }
  const acct = m.details.account_number ?? ''
  return `${m.details.bank_name ?? ''} ••${acct.slice(-4)}`
}

type Editing = { mode: 'add' } | { mode: 'edit'; method: PaymentMethod }

const TEXTAREA = 'w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink'

export default function PaymentMethodsTab({ orgId, control, methods, settings, busy, run }: {
  orgId: number
  control: boolean
  methods: PaymentMethod[]
  /** Null until the page's first load lands. */
  settings: PortalSettings | null
  busy: boolean
  run: Runner
}) {
  const [editing, setEditing] = useState<Editing | null>(null)
  const [form, setForm] = useState<MethodForm>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<PaymentMethod | null>(null)

  // Withdrawal and account rules: the same dirty guard the old wallet card had. Once
  // the admin touches the form, the 10 s poll leaves it alone until a save
  // clears the flag; read through a ref so the effect never sees a stale value.
  const [rules, setRules] = useState({ withdrawal_min: '0', withdrawal_fee_pct: '0', max_live_accounts: '5' })
  const [rulesDirty, setRulesDirty] = useState(false)
  const rulesDirtyRef = useRef(false)
  rulesDirtyRef.current = rulesDirty
  useEffect(() => {
    if (settings && !rulesDirtyRef.current) {
      setRules({
        withdrawal_min: String(settings.withdrawal_min),
        withdrawal_fee_pct: String(settings.withdrawal_fee_pct),
        max_live_accounts: String(settings.max_live_accounts),
      })
    }
  }, [settings])

  const editRules = (patch: Partial<typeof rules>) => {
    setRules((r) => ({ ...r, ...patch }))
    setRulesDirty(true)
  }

  const saveRules = (e: FormEvent) => {
    e.preventDefault()
    run(async () => {
      const saved = await orgApi<PortalSettings>(orgId, 'portal-settings', {
        // Digits go as a number; anything else goes as typed so the api's
        // "max_live_accounts must be a whole number from 1 to 50" shows.
        method: 'PUT', body: JSON.stringify({ ...rules, max_live_accounts:
          /^\d+$/.test(rules.max_live_accounts.trim()) ? Number(rules.max_live_accounts.trim())
            : rules.max_live_accounts }) })
      setRules({
        withdrawal_min: String(saved.withdrawal_min),
        withdrawal_fee_pct: String(saved.withdrawal_fee_pct),
        max_live_accounts: String(saved.max_live_accounts),
      })
      setRulesDirty(false)
    }, 'Portal settings saved')
  }

  const openAdd = () => { setForm(EMPTY_FORM); setFormError(null); setEditing({ mode: 'add' }) }
  const openEdit = (m: PaymentMethod) => { setForm(formOf(m)); setFormError(null); setEditing({ mode: 'edit', method: m }) }
  const patch = (p: Partial<MethodForm>) => setForm((f) => ({ ...f, ...p }))
  const patchDetail = (key: string, value: string) =>
    setForm((f) => ({ ...f, details: { ...f.details, [key]: value } }))

  const payload = (mode: Editing['mode']) => {
    const details: Record<string, string> = {}
    for (const f of DETAIL_FIELDS[form.kind]) {
      const v = (form.details[f.key] ?? '').trim()
      if (v) details[f.key] = v
    }
    return {
      label: form.label.trim(),
      currency: form.currency.trim() || 'USD',
      details,
      min_amount: form.min_amount.trim() || '0',
      fee_pct: form.fee_pct.trim() || '0',
      // The server's MethodPatch treats a missing/null `instructions` as
      // "leave unchanged" and "" as "clear" (portal_admin.py's
      // update_method). On add there is nothing to leave unchanged, so an
      // empty box means "no instructions" (null); on edit an empty box
      // must reach the server as "" so clearing it actually clears it.
      instructions: mode === 'edit' ? form.instructions.trim() : (form.instructions.trim() || null),
      sort_order: Number(form.sort_order) || 0,
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    const body = payload(editing.mode)
    if (!body.label) { setFormError('Label is required'); return }
    const missing = DETAIL_FIELDS[form.kind].find((f) => !f.optional && !body.details[f.key])
    if (missing) { setFormError(`${missing.label} is required`); return }
    setFormError(null)
    setSaving(true)
    try {
      // The request runs here, not inside run(): a refusal (400 field
      // missing, 404) must show inside the drawer, which covers the page
      // banner while it is open.
      if (editing.mode === 'add') {
        await orgApi(orgId, 'payment-methods', {
          method: 'POST', body: JSON.stringify({ kind: form.kind, ...body }) })
      } else {
        await orgApi(orgId, `payment-methods/${editing.method.id}`, {
          method: 'PATCH', body: JSON.stringify(body) })
      }
      setEditing(null)
      // The request already succeeded; run() now only announces and refreshes.
      await run(async () => {}, editing.mode === 'add' ? 'Payment method added' : 'Payment method saved')
    } catch (err) {
      setFormError(errorText(err, 'Could not save the method'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = (m: PaymentMethod) => run(async () => {
    await orgApi(orgId, `payment-methods/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !m.enabled }) })
  }, m.enabled ? 'Method disabled' : 'Method enabled')

  const remove = (m: PaymentMethod) => run(async () => {
    await orgApi(orgId, `payment-methods/${m.id}`, { method: 'DELETE' })
  }, 'Payment method deleted')

  return (
    <div className="space-y-6">
      <Card title="Payment methods" inset
            actions={control && <Button size="sm" disabled={busy} onClick={openAdd}>Add method</Button>}>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Method</th>
                <th className="desk-label px-5 py-2 font-semibold">Details</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Minimum</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Fee</th>
                <th className="desk-label px-5 py-2 font-semibold">Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {methods.length === 0 && (
                <tr><td colSpan={6} className="text-center py-8 text-ink-faint">
                  No payment methods yet — add one so investors can deposit.
                </td></tr>
              )}
              {methods.map((m) => (
                <tr key={m.id} className="border-b border-line last:border-0 align-top">
                  <td data-label="Method" className="px-5 py-2.5">
                    <div className="min-w-0">
                      <div className="text-ink">{m.label}</div>
                      <Badge tone="neutral" className="mt-1">{m.kind === 'crypto' ? 'Crypto' : 'Bank'}</Badge>
                    </div>
                  </td>
                  <td data-label="Details" className="num px-5 py-2.5 text-ink-soft break-all">{methodSummary(m)}</td>
                  <td data-label="Minimum" className="num px-5 py-2.5 text-right">{money(m.min_amount, m.currency)}</td>
                  <td data-label="Fee" className="num px-5 py-2.5 text-right">{m.fee_pct}%</td>
                  <td data-label="Status" className="px-5 py-2.5">
                    <Badge tone={m.enabled ? 'profit' : 'neutral'}>{m.enabled ? 'Enabled' : 'Disabled'}</Badge>
                  </td>
                  <td className="px-5 py-2.5 text-right">
                    {control && (
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button variant="secondary" size="sm" disabled={busy}
                                aria-label={`${m.enabled ? 'Disable' : 'Enable'} ${m.label}`}
                                onClick={() => toggle(m)}>
                          {m.enabled ? 'Disable' : 'Enable'}
                        </Button>
                        <Button variant="secondary" size="sm" disabled={busy}
                                aria-label={`Edit ${m.label}`} onClick={() => openEdit(m)}>
                          Edit
                        </Button>
                        <Button variant="ghost" tone="loss" size="sm" disabled={busy}
                                aria-label={`Delete ${m.label}`} onClick={() => setDeleting(m)}>
                          Delete
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Withdrawal and account rules">
        <form onSubmit={saveRules} className="space-y-4">
          <p className="text-sm text-ink-soft">
            Applied to every withdrawal request: the smallest amount an investor may ask for
            and the fee the workspace keeps (both default to 0). The cap counts each
            investor's live accounts plus an open account request (default 5).
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="block w-40">
              <span className="desk-label block mb-1">Minimum withdrawal</span>
              <Input aria-label="Minimum withdrawal" num inputMode="decimal" disabled={!control}
                     value={rules.withdrawal_min}
                     onChange={(e) => editRules({ withdrawal_min: e.target.value })} />
            </label>
            <label className="block w-40">
              <span className="desk-label block mb-1">Withdrawal fee %</span>
              <Input aria-label="Withdrawal fee %" num inputMode="decimal" disabled={!control}
                     value={rules.withdrawal_fee_pct}
                     onChange={(e) => editRules({ withdrawal_fee_pct: e.target.value })} />
            </label>
            <label className="block w-40">
              <span className="desk-label block mb-1">Max live accounts per investor</span>
              <Input aria-label="Max live accounts per investor" num inputMode="numeric" disabled={!control}
                     value={rules.max_live_accounts}
                     onChange={(e) => editRules({ max_live_accounts: e.target.value })} />
            </label>
            {control && (
              <Button type="submit" disabled={busy || !rulesDirty}>Save portal settings</Button>
            )}
          </div>
        </form>
      </Card>

      <TicketSubjectsCard orgId={orgId} control={control} />

      <Drawer open={editing != null} busy={saving}
              title={editing?.mode === 'edit' ? `Edit ${editing.method.label}` : 'Add payment method'}
              onClose={() => setEditing(null)}>
        <form onSubmit={save} className="space-y-4">
          {formError && <Banner kind="error">{formError}</Banner>}
          {editing?.mode === 'add' && (
            <label className="block">
              <span className="desk-label block mb-1">Kind</span>
              <Select block aria-label="Kind" value={form.kind}
                      onChange={(e) => patch({ kind: e.target.value as Kind, details: {} })}>
                <option value="crypto">Crypto</option>
                <option value="bank">Bank</option>
              </Select>
            </label>
          )}
          <label className="block">
            <span className="desk-label block mb-1">Label</span>
            <Input aria-label="Label" value={form.label}
                   placeholder={form.kind === 'crypto' ? 'USDT on TRC20' : 'ICICI Bank'}
                   onChange={(e) => patch({ label: e.target.value })} />
          </label>
          {DETAIL_FIELDS[form.kind].map((f) => (
            <label key={f.key} className="block">
              <span className="desk-label block mb-1">{f.label}{f.optional ? ' (optional)' : ''}</span>
              <Input aria-label={f.label}
                     num={f.key === 'address' || f.key === 'account_number' || f.key === 'code'}
                     value={form.details[f.key] ?? ''}
                     onChange={(e) => patchDetail(f.key, e.target.value)} />
            </label>
          ))}
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="desk-label block mb-1">Currency</span>
              <Input aria-label="Currency" value={form.currency}
                     onChange={(e) => patch({ currency: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Sort order</span>
              <Input aria-label="Sort order" num inputMode="numeric" value={form.sort_order}
                     onChange={(e) => patch({ sort_order: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Minimum deposit</span>
              <Input aria-label="Minimum deposit" num inputMode="decimal" value={form.min_amount}
                     onChange={(e) => patch({ min_amount: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Fee %</span>
              <Input aria-label="Fee %" num inputMode="decimal" value={form.fee_pct}
                     onChange={(e) => patch({ fee_pct: e.target.value })} />
            </label>
          </div>
          <label className="block">
            <span className="desk-label block mb-1">Instructions (optional)</span>
            <textarea aria-label="Instructions" rows={3} value={form.instructions}
                      onChange={(e) => patch({ instructions: e.target.value })}
                      className={TEXTAREA} />
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)} disabled={saving}>Cancel</Button>
            <Button type="submit" busy={saving}>Save method</Button>
          </div>
        </form>
      </Drawer>

      <ConfirmDialog
        open={deleting != null}
        title={`Delete ${deleting?.label ?? ''}?`}
        confirmLabel="Delete method"
        danger
        busy={busy}
        onConfirm={async () => {
          if (!deleting) return
          const m = deleting
          setDeleting(null)
          await remove(m)
        }}
        onCancel={() => setDeleting(null)}
      >
        <p>
          Investors stop seeing it at once. History keeps its own copy of the label, so
          past deposits still read correctly. A method with a deposit still pending cannot
          be deleted — decide that deposit first.
        </p>
      </ConfirmDialog>
    </div>
  )
}
