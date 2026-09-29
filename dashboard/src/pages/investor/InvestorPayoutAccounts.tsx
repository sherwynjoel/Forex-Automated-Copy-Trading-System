import { useCallback, useEffect, useState } from 'react'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText } from '../../lib/format'
import { BADGE_TONE, statusLabel, statusTone } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import type { PayoutDestination, UploadedFile } from '../../lib/types'

type Kind = PayoutDestination['kind']

const EMPTY_BANK = { nickname: '', bank_name: '', holder: '', account_number: '', code: '', bank_address: '', country: '' }
const EMPTY_CRYPTO = { nickname: '', coin: '', network: '', address: '' }
type BankForm = typeof EMPTY_BANK
type CryptoForm = typeof EMPTY_CRYPTO

interface Field { key: string; label: string; required: boolean }
const BANK_FIELDS: Field[] = [
  { key: 'nickname', label: 'Nickname', required: true },
  { key: 'bank_name', label: 'Bank name', required: true },
  { key: 'holder', label: 'Account holder', required: true },
  { key: 'account_number', label: 'Account number', required: true },
  { key: 'code', label: 'SWIFT / IFSC code', required: true },
  { key: 'bank_address', label: 'Bank address (optional)', required: false },
  { key: 'country', label: 'Country (optional)', required: false },
]
const CRYPTO_FIELDS: Field[] = [
  { key: 'nickname', label: 'Nickname', required: true },
  { key: 'coin', label: 'Coin', required: true },
  { key: 'network', label: 'Network', required: true },
  { key: 'address', label: 'Address', required: true },
]
const DRAWER_TITLE: Record<Kind, string> = { bank: 'Add bank account', crypto: 'Add crypto address' }

/** What the PIN dialog will post: everything but the MPIN. */
interface Pending { kind: Kind; nickname: string; details: Record<string, string>; proof_file_id: number | null }

export default function InvestorPayoutAccounts() {
  const { orgId } = useOrg()
  const [rows, setRows] = useState<PayoutDestination[]>([])
  const [drawer, setDrawer] = useState<Kind | null>(null)
  const [bank, setBank] = useState<BankForm>(EMPTY_BANK)
  const [crypto, setCrypto] = useState<CryptoForm>(EMPTY_CRYPTO)
  const [proof, setProof] = useState<File | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [removing, setRemoving] = useState<PayoutDestination | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setRows(await orgApi<PayoutDestination[]>(orgId, 'investor/payout-destinations'))
    } catch (err) {
      setError(errorText(err, 'Could not load your payout accounts'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const banks = rows.filter((d) => d.kind === 'bank')
  const cryptos = rows.filter((d) => d.kind === 'crypto')

  const openDrawer = (k: Kind) => {
    setDrawer(k); setFormError(null); setProof(null)
    setBank(EMPTY_BANK); setCrypto(EMPTY_CRYPTO)
  }
  const closeDrawer = () => { setDrawer(null); setFormError(null) }

  // Validates, uploads the optional proof, then hands over to the PIN
  // dialog. The upload happens here so a wrong PIN never re-uploads it.
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!drawer) return
    setFormError(null)
    const fields = drawer === 'bank' ? BANK_FIELDS : CRYPTO_FIELDS
    const values: Record<string, string> = drawer === 'bank' ? { ...bank } : { ...crypto }
    const missing = fields.find((f) => f.required && !values[f.key].trim())
    if (missing) { setFormError(`${missing.label} is required`); return }
    const details: Record<string, string> = {}
    for (const f of fields) {
      if (f.key === 'nickname') continue
      const v = values[f.key].trim()
      if (v) details[f.key] = v
    }
    setBusy(true)
    try {
      let proof_file_id: number | null = null
      if (proof) {
        const fd = new FormData()
        fd.append('purpose', 'payout_proof')
        fd.append('file', proof)
        proof_file_id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      }
      setPending({ kind: drawer, nickname: values.nickname.trim(), details, proof_file_id })
    } catch (err) {
      setFormError(errorText(err, 'Could not upload the proof'))
    } finally {
      setBusy(false)
    }
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const confirm = async (mpin: string) => {
    if (!pending) return
    setBusy(true)
    try {
      await orgApi<PayoutDestination>(orgId, 'investor/payout-destinations', {
        method: 'POST',
        body: JSON.stringify({ ...pending, mpin }),
      }, { redirectOn401: false })
      setPending(null)
      setDrawer(null)
      setBank(EMPTY_BANK); setCrypto(EMPTY_CRYPTO); setProof(null)
      setNotice('Payout account saved. An admin approves it before it can be used for a withdrawal.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!removing) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PayoutDestination>(orgId, `investor/payout-destinations/${removing.id}/remove`, { method: 'POST' })
      setNotice('Payout account removed.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not remove the payout account'))
    } finally {
      setBusy(false)
      setRemoving(null)
    }
  }

  const list = (items: PayoutDestination[], empty: string) => (
    <ul className="divide-y divide-line">
      {items.length === 0 && <li className="text-center py-8 text-ink-faint">{empty}</li>}
      {items.map((d) => (
        <li key={d.id} className="px-4 py-3 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="font-semibold text-ink">{d.nickname}</span>
          <span className="num text-ink-soft flex-1 min-w-0">{d.summary}</span>
          <Badge tone={BADGE_TONE[statusTone(d.status, 'destination')]}>{statusLabel(d.status, 'destination')}</Badge>
          {d.status !== 'rejected' && (
            <Button variant="ghost" tone="loss" size="sm" aria-label={`Remove ${d.nickname}`}
                    onClick={() => setRemoving(d)} disabled={busy}>
              Remove
            </Button>
          )}
          {d.decision_note && <p className="basis-full text-xs text-ink-soft">Admin: {d.decision_note}</p>}
        </li>
      ))}
    </ul>
  )

  const fields = drawer === 'bank' ? BANK_FIELDS : CRYPTO_FIELDS
  const values: Record<string, string> = drawer === 'bank' ? bank : crypto
  const setValue = (key: string, v: string) => {
    if (drawer === 'bank') setBank({ ...bank, [key]: v })
    else setCrypto({ ...crypto, [key]: v })
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Payout accounts"
        subtitle="Where withdrawals are sent. An admin approves each account before it can be used."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      <Card title="Bank accounts" inset
            actions={<Button size="sm" onClick={() => openDrawer('bank')}>Add bank account</Button>}>
        {list(banks, 'No bank accounts yet')}
      </Card>

      <Card title="Crypto addresses" inset
            actions={<Button size="sm" onClick={() => openDrawer('crypto')}>Add crypto address</Button>}>
        {list(cryptos, 'No crypto addresses yet')}
      </Card>

      <Drawer open={drawer != null} title={drawer ? DRAWER_TITLE[drawer] : ''} onClose={closeDrawer} busy={busy}>
        <form onSubmit={save} noValidate className="space-y-4">
          {formError && <Banner kind="error" onDismiss={() => setFormError(null)}>{formError}</Banner>}
          {fields.map((f) => (
            <div key={f.key}>
              <label htmlFor={`payout-${f.key}`} className="desk-label block mb-1">{f.label}</label>
              <Input id={`payout-${f.key}`} num={f.key === 'account_number' || f.key === 'code' || f.key === 'address'}
                     value={values[f.key]} onChange={(e) => setValue(f.key, e.target.value)} disabled={busy} />
            </div>
          ))}
          <FileInput id="payout-proof" label="Proof (optional)" accept={RECEIPT_ACCEPT} maxBytes={MAX_UPLOAD_BYTES}
                     value={proof} onChange={setProof} disabled={busy}
                     hint="A statement header or wallet screenshot, JPEG, PNG, WebP or PDF up to 5 MB" />
          <Button type="submit" disabled={busy}>Save payout account</Button>
        </form>
      </Drawer>

      <PinConfirmDialog
        open={pending != null}
        title={`Save ${pending?.nickname ?? ''} as a payout account?`}
        confirmLabel="Confirm"
        busy={busy}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      >
        <p>An admin checks the details and approves the account before it can receive a withdrawal.</p>
        {pending && (
          <dl className="grid grid-cols-2 gap-2">
            {Object.entries(pending.details).map(([k, v]) => (
              <div key={k}>
                <dt className="desk-label">{k.replace(/_/g, ' ')}</dt>
                <dd className="num text-ink break-all">{v}</dd>
              </div>
            ))}
          </dl>
        )}
      </PinConfirmDialog>

      <ConfirmDialog
        open={removing != null}
        title={`Remove ${removing?.nickname ?? ''}?`}
        confirmLabel="Remove"
        danger
        busy={busy}
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      >
        <p>Past withdrawals keep their record of it; it just cannot be picked for a new one.</p>
      </ConfirmDialog>
    </div>
  )
}
