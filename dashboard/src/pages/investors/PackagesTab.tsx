import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, money } from '../../lib/format'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { AccountPackage } from '../../lib/types'

interface Form { name: string; min_deposit: string; currency: string; spread_label: string; leverage: string; sort_order: string }

const EMPTY: Form = { name: '', min_deposit: '0', currency: 'USD', spread_label: '', leverage: '100, 200, 500', sort_order: '0' }
const AMOUNT = /^\d+(\.\d{1,2})?$/
const FIELDS: { key: keyof Form; label: string; num?: boolean }[] = [
  { key: 'name', label: 'Name' },
  { key: 'min_deposit', label: 'Minimum deposit', num: true },
  { key: 'currency', label: 'Currency' },
  { key: 'spread_label', label: 'Spread' },
  { key: 'leverage', label: 'Leverage options', num: true },
  { key: 'sort_order', label: 'Sort order', num: true },
]

function formOf(p: AccountPackage): Form {
  return {
    name: p.name, min_deposit: String(p.min_deposit), currency: p.currency, spread_label: p.spread_label ?? '',
    leverage: p.leverage_options.join(', '), sort_order: String(p.sort_order),
  }
}

/** "100, 200 500" -> [100, 200, 500]; null unless every part is a whole number 1-3000. */
export function parseLeverage(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean)
  if (parts.length === 0 || parts.some((p) => !/^\d+$/.test(p))) return null
  const nums = parts.map(Number)
  return nums.every((n) => n >= 1 && n <= 3000) ? nums : null
}

type Editing = { mode: 'add' } | { mode: 'edit'; pkg: AccountPackage }

/** What investors may request: name, minimum deposit, spread and leverage
 *  choices. Loads its own list; investors cannot request an account until
 *  at least one package is enabled. */
export default function PackagesTab({ orgId, control }: { orgId: number; control: boolean }) {
  const [rows, setRows] = useState<AccountPackage[] | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [form, setForm] = useState<Form>(EMPTY)
  const [formError, setFormError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<AccountPackage | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await orgApi<AccountPackage[]>(orgId, 'account-packages'))
    } catch (err) {
      setError(errorText(err, 'Could not load the account packages'))
      setRows((r) => r ?? [])
    }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const openAdd = () => { setEditing({ mode: 'add' }); setForm(EMPTY); setFormError(null) }
  const openEdit = (pkg: AccountPackage) => { setEditing({ mode: 'edit', pkg }); setForm(formOf(pkg)); setFormError(null) }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    if (!form.name.trim()) { setFormError('Name is required'); return }
    if (!AMOUNT.test(form.min_deposit.trim())) { setFormError('Minimum deposit: a number with at most two decimals'); return }
    const leverage = parseLeverage(form.leverage)
    if (!leverage) { setFormError('Leverage options: whole numbers from 1 to 3000, separated by commas'); return }
    const body = {
      name: form.name.trim(), min_deposit: form.min_deposit.trim(), currency: form.currency.trim() || 'USD',
      spread_label: form.spread_label.trim(), leverage_options: leverage, sort_order: Number(form.sort_order) || 0,
    }
    setBusy(true); setFormError(null)
    try {
      if (editing.mode === 'add') {
        await orgApi(orgId, 'account-packages', { method: 'POST', body: JSON.stringify(body) })
      } else {
        await orgApi(orgId, `account-packages/${editing.pkg.id}`, { method: 'PATCH', body: JSON.stringify(body) })
      }
      setEditing(null)
      setNotice('Package saved')
      await load()
    } catch (err) {
      setFormError(errorText(err, 'Could not save the package'))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (pkg: AccountPackage) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi(orgId, `account-packages/${pkg.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !pkg.enabled }) })
      setNotice(`${pkg.name} ${pkg.enabled ? 'disabled' : 'enabled'}`)
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not change the package'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!deleting) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi(orgId, `account-packages/${deleting.id}`, { method: 'DELETE' })
      setNotice('Package deleted')
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not delete the package'))
    } finally {
      setBusy(false)
      setDeleting(null)
    }
  }

  return (
    <div className="space-y-4">
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      <Card title="Account packages" inset
            actions={control ? <Button size="sm" onClick={openAdd}>Add package</Button> : undefined}>
        {rows == null ? (
          <div className="p-4"><Loading lines={3} label="Loading packages" /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Name</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Minimum deposit</th>
                  <th className="desk-label px-4 py-2 font-semibold">Spread</th>
                  <th className="desk-label px-4 py-2 font-semibold">Leverage</th>
                  <th className="desk-label px-4 py-2 font-semibold">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={6} className="text-center py-8 text-ink-faint">
                    No packages yet. Investors cannot request an account until you add one.
                  </td></tr>
                )}
                {rows.map((p) => (
                  <tr key={p.id} className="border-b border-line last:border-0 align-top">
                    <td data-label="Name" className="px-4 py-2.5 text-ink">{p.name}</td>
                    <td data-label="Minimum deposit" className="num px-4 py-2.5 text-right">{money(p.min_deposit, p.currency)}</td>
                    <td data-label="Spread" className="num px-4 py-2.5">{p.spread_label ?? '—'}</td>
                    <td data-label="Leverage" className="num px-4 py-2.5">{p.leverage_options.map((l) => `1:${l}`).join(' · ')}</td>
                    <td data-label="Status" className="px-4 py-2.5">
                      <Badge tone={p.enabled ? 'profit' : 'neutral'}>{p.enabled ? 'Enabled' : 'Disabled'}</Badge>
                    </td>
                    <td className="px-4 py-2.5">
                      {control && (
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <Button variant="ghost" size="sm" aria-label={`Edit ${p.name}`} disabled={busy}
                                  onClick={() => openEdit(p)}>Edit</Button>
                          <Button variant="ghost" size="sm" aria-label={`${p.enabled ? 'Disable' : 'Enable'} ${p.name}`}
                                  disabled={busy} onClick={() => { void toggle(p) }}>
                            {p.enabled ? 'Disable' : 'Enable'}
                          </Button>
                          <Button variant="ghost" tone="loss" size="sm" aria-label={`Delete ${p.name}`} disabled={busy}
                                  onClick={() => setDeleting(p)}>Delete</Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Drawer open={editing != null}
              title={editing?.mode === 'edit' ? `Edit ${editing.pkg.name}` : 'Add account package'}
              onClose={() => setEditing(null)} busy={busy}>
        <form onSubmit={save} noValidate className="space-y-4">
          {formError && <Banner kind="error" onDismiss={() => setFormError(null)}>{formError}</Banner>}
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label htmlFor={`package-${f.key}`} className="desk-label block mb-1">{f.label}</label>
              <Input id={`package-${f.key}`} num={f.num} value={form[f.key]} disabled={busy}
                     onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
              {f.key === 'leverage' && <p className="mt-1 text-xs text-ink-soft">e.g. 100, 200, 500</p>}
              {f.key === 'spread_label' && <p className="mt-1 text-xs text-ink-soft">Shown as written, e.g. 20-25 (optional)</p>}
            </div>
          ))}
          <Button type="submit" disabled={busy}>Save package</Button>
        </form>
      </Drawer>

      <ConfirmDialog
        open={deleting != null}
        title={`Delete ${deleting?.name ?? ''}?`}
        confirmLabel="Delete"
        danger
        busy={busy}
        onConfirm={() => { void remove() }}
        onCancel={() => setDeleting(null)}
      >
        <p>Requests already decided keep the package name. A package with an open request cannot be deleted; disable it instead.</p>
      </ConfirmDialog>
    </div>
  )
}
