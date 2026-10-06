import { useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText } from '../../lib/format'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { BonusRules } from '../../lib/types'

interface Form {
  signup_enabled: boolean; signup_amount: string
  kyc_enabled: boolean; kyc_amount: string
  deposit_enabled: boolean; deposit_pct: string; deposit_cap: string
}

type Flag = 'signup_enabled' | 'kyc_enabled' | 'deposit_enabled'
type Field = 'signup_amount' | 'kyc_amount' | 'deposit_pct'

const RULES: { flag: Flag; field: Field; name: string; fieldLabel: string; hint: string }[] = [
  { flag: 'signup_enabled', field: 'signup_amount', name: 'Welcome bonus', fieldLabel: 'Welcome bonus amount',
    hint: 'Paid once when someone joins as an investor.' },
  { flag: 'kyc_enabled', field: 'kyc_amount', name: 'Verification bonus', fieldLabel: 'Verification bonus amount',
    hint: 'Paid once when you approve their identity.' },
  { flag: 'deposit_enabled', field: 'deposit_pct', name: 'Deposit bonus', fieldLabel: 'Deposit bonus %',
    hint: 'A share of every deposit you confirm, rounded to the cent.' },
]

function formOf(r: BonusRules): Form {
  return {
    signup_enabled: r.signup_enabled, signup_amount: String(r.signup_amount),
    kyc_enabled: r.kyc_enabled, kyc_amount: String(r.kyc_amount),
    deposit_enabled: r.deposit_enabled, deposit_pct: String(r.deposit_pct),
    deposit_cap: r.deposit_cap == null ? '' : String(r.deposit_cap),
  }
}

function isRules(r: unknown): r is BonusRules {
  return typeof r === 'object' && r != null && typeof (r as BonusRules).signup_enabled === 'boolean'
}

/**
 * The org's bonus rules: welcome, verification and deposit, each switched
 * on or off. Amounts go to the server as typed so its own messages show.
 * Owns its own load and banners; no poll, so nothing overwrites a form the
 * admin is editing.
 */
export default function BonusRulesCard({ orgId, control }: { orgId: number; control: boolean }) {
  const [form, setForm] = useState<Form | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    setForm(null)
    orgApi<BonusRules>(orgId, 'bonus-rules').then(
      (r) => { if (mine === seq.current && isRules(r)) setForm(formOf(r)) },
      (err) => { if (mine === seq.current) setError(errorText(err, 'Could not load the bonus rules')) },
    )
  }, [orgId])

  const edit = (patch: Partial<Form>) => setForm((f) => (f ? { ...f, ...patch } : f))

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!form) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const saved = await orgApi<BonusRules>(orgId, 'bonus-rules', {
        method: 'PUT',
        body: JSON.stringify({ ...form, deposit_cap: form.deposit_cap.trim() === '' ? null : form.deposit_cap.trim() }),
      })
      setForm(formOf(saved))
      setNotice('Bonus rules saved')
    } catch (err) {
      setError(errorText(err, 'Could not save the bonus rules'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Bonus rules">
      <form onSubmit={save} className="space-y-4">
        <p className="text-sm text-ink-soft">
          Bonuses are paid into the investor's Credit wallet, which can only move to one of their
          trading accounts. A rule counts from the moment you switch it on; nothing is paid backwards.
        </p>
        {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
        {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
        {form == null ? (
          !error && <Loading lines={3} label="Loading bonus rules" />
        ) : (
          <>
            {RULES.map((r) => (
              <div key={r.flag} className="flex flex-wrap items-end gap-3">
                <label className="flex items-center gap-2 text-sm text-ink w-48">
                  <input type="checkbox" className="accent-brand" aria-label={`${r.name} on`}
                         checked={form[r.flag]} disabled={!control}
                         onChange={(e) => edit({ [r.flag]: e.target.checked } as Partial<Form>)} />
                  {r.name}
                </label>
                <label className="block w-40">
                  <span className="desk-label block mb-1">{r.field === 'deposit_pct' ? '% of the deposit' : 'Amount (USD)'}</span>
                  <Input aria-label={r.fieldLabel} num inputMode="decimal" disabled={!control}
                         value={form[r.field]} onChange={(e) => edit({ [r.field]: e.target.value } as Partial<Form>)} />
                </label>
                <span className="text-xs text-ink-soft">{r.hint}</span>
              </div>
            ))}
            <label className="block w-40">
              <span className="desk-label block mb-1">Deposit bonus cap (USD)</span>
              <Input aria-label="Deposit bonus cap" num inputMode="decimal" placeholder="No cap" disabled={!control}
                     value={form.deposit_cap} onChange={(e) => edit({ deposit_cap: e.target.value })} />
            </label>
            {control && <Button type="submit" disabled={busy}>Save bonus rules</Button>}
          </>
        )}
      </form>
    </Card>
  )
}
