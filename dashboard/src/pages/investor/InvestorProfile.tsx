import { useCallback, useEffect, useState } from 'react'
import { orgApi, orgUpload, type ApiError } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { FIELD_LABELS, GENDERS, ID_TYPES, fieldValue, kycBadge, kycLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { KycFileField, KycProfile, KycTextField, UploadedFile } from '../../lib/types'

type Step = 'profile' | 'identity' | 'address' | 'photo' | 'review'
type FormStep = Exclude<Step, 'review'>

const STEPS: { key: Step; label: string }[] = [
  { key: 'profile', label: 'Profile' },
  { key: 'identity', label: 'Identity' },
  { key: 'address', label: 'Address' },
  { key: 'photo', label: 'Photo' },
  { key: 'review', label: 'Review' },
]

const STEP_FIELDS: Record<FormStep, { text: KycTextField[]; files: KycFileField[] }> = {
  profile: { text: ['full_name', 'gender', 'date_of_birth', 'phone'], files: [] },
  identity: { text: ['country_citizenship', 'id_type', 'id_number'], files: ['id_front_file_id', 'id_back_file_id'] },
  address: {
    text: ['address_line', 'area', 'landmark', 'city', 'state', 'postal_code', 'country_residence'],
    files: ['address_proof_file_id'],
  },
  photo: { text: [], files: ['photo_file_id'] },
}
const TEXT_FIELDS: KycTextField[] = Object.values(STEP_FIELDS).flatMap((s) => s.text)
const FILE_FIELDS: KycFileField[] = Object.values(STEP_FIELDS).flatMap((s) => s.files)
const OPTIONAL = new Set<string>(['area', 'landmark', 'state'])
const COUNTRY = new Set<string>(['country_residence', 'country_citizenship'])
const PHOTO_ACCEPT = ['image/jpeg', 'image/png', 'image/webp']
const FILE_HINT: Record<KycFileField, string> = {
  id_front_file_id: 'The side with your photo. JPEG, PNG, WebP or PDF up to 5 MB.',
  id_back_file_id: 'The other side (for a passport, the page with your address or signature).',
  address_proof_file_id: 'A utility bill or bank statement from the last three months.',
  photo_file_id: 'A clear photo of your face, JPEG, PNG or WebP up to 5 MB.',
}

type Form = Record<KycTextField, string>

function formOf(p: KycProfile): Form {
  return Object.fromEntries(TEXT_FIELDS.map((k) => [k, p[k] ?? ''])) as Form
}

/** The server's "someone else saved first" conflict -- reload and show it,
 *  rather than let the investor resubmit over data they never saw. */
function isReloadConflict(err: unknown): boolean {
  const res = (err as ApiError | undefined)?.response
  return res?.status === 409 && res.body?.detail === 'your profile changed; reload it'
}

function Field({ field, value, onChange }: { field: KycTextField; value: string; onChange: (v: string) => void }) {
  const id = `kyc-${field}`
  const label = `${FIELD_LABELS[field]}${OPTIONAL.has(field) ? ' (optional)' : ''}`
  if (field === 'gender' || field === 'id_type') {
    const options = field === 'gender' ? GENDERS : ID_TYPES
    return (
      <div>
        <label htmlFor={id} className="desk-label block mb-1">{label}</label>
        <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose one</option>
          {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
        </Select>
      </div>
    )
  }
  const country = COUNTRY.has(field)
  return (
    <div>
      <label htmlFor={id} className="desk-label block mb-1">{label}</label>
      <Input id={id} value={value} type={field === 'date_of_birth' ? 'date' : 'text'}
             num={country || field === 'postal_code' || field === 'id_number'}
             maxLength={country ? 2 : undefined}
             onChange={(e) => onChange(country ? e.target.value.toUpperCase() : e.target.value)} />
      {country && <p className="mt-1 text-xs text-ink-soft">Two-letter code, e.g. IN</p>}
    </div>
  )
}

function StatusBanner({ profile }: { profile: KycProfile }) {
  if (profile.status === 'draft') {
    return <Banner kind="warn" announce={false}>Not submitted yet. Fill in each step, then submit from Review.</Banner>
  }
  if (profile.status === 'submitted') {
    return (
      <Banner kind="notice" announce={false}>
        {`Under review since ${formatWhen(profile.submitted_at)}. You can edit again once an admin has decided.`}
      </Banner>
    )
  }
  if (profile.status === 'approved') {
    return (
      <Banner kind="notice" announce={false}>
        Verified. Changing your phone or address keeps you verified; changing your name, date of birth,
        citizenship, ID or any document means verifying again.
      </Banner>
    )
  }
  return (
    <Banner kind="error" announce={false}>
      {`Rejected: ${profile.decision_note ?? 'no reason given'}. Fix what the note asks and submit again.`}
    </Banner>
  )
}

/**
 * The investor's profile and identity verification, one step at a time.
 * Each step saves only what changed in it (uploads first, then one PUT);
 * Review shows what is still missing and submits with the MPIN.
 */
export default function InvestorProfile() {
  const { orgId } = useOrg()
  const [profile, setProfile] = useState<KycProfile | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [files, setFiles] = useState<Partial<Record<KycFileField, File | null>>>({})
  // An id an upload already landed under while its step's PUT then failed:
  // kept so a retry attaches it instead of uploading the same file again.
  const [pendingFileIds, setPendingFileIds] = useState<Partial<Record<KycFileField, number>>>({})
  const [step, setStep] = useState<Step>('profile')
  const [pinOpen, setPinOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const p = await orgApi<KycProfile>(orgId, 'investor/profile')
      setProfile(p)
      setForm(formOf(p))
    } catch (err) {
      setError(errorText(err, 'Could not load your profile'))
    }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const locked = profile?.status === 'submitted'

  const saveStep = async (s: FormStep): Promise<boolean> => {
    if (!profile || !form) return false
    setBusy(true); setError(null); setNotice(null)
    try {
      const body: Record<string, string | number | null> = {}
      for (const k of STEP_FIELDS[s].text) {
        const v = form[k].trim()
        if (v !== (profile[k] ?? '')) body[k] = v === '' ? null : v
      }
      for (const k of STEP_FIELDS[s].files) {
        const file = files[k]
        if (file) {
          const fd = new FormData()
          fd.append('purpose', k === 'photo_file_id' ? 'kyc_photo' : 'kyc_document')
          fd.append('file', file)
          const id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
          // Already on the server even if the PUT below fails: remember the
          // id and clear the slot, so a retry neither re-uploads nor drops it.
          setFiles((f) => ({ ...f, [k]: null }))
          setPendingFileIds((p) => ({ ...p, [k]: id }))
          body[k] = id
        } else if (pendingFileIds[k] != null) {
          body[k] = pendingFileIds[k]!
        }
      }
      if (Object.keys(body).length > 0) {
        const saved = await orgApi<KycProfile>(orgId, 'investor/profile', { method: 'PUT', body: JSON.stringify(body) })
        setNotice(profile.status === 'approved' && saved.status === 'draft'
          ? 'Saved. You changed identity details, so submit again to be verified.'
          : 'Saved')
        setProfile(saved)
        setForm(formOf(saved))
        // Only this step's slots: another step's picked or pending file is
        // still waiting for its own save.
        const savedSlots = STEP_FIELDS[s].files
        setFiles((f) => ({ ...f, ...Object.fromEntries(savedSlots.map((k) => [k, null])) }))
        setPendingFileIds((p) => Object.fromEntries(
          Object.entries(p).filter(([k]) => !(savedSlots as readonly string[]).includes(k))))
      }
      return true
    } catch (err) {
      if (isReloadConflict(err)) await load()
      setError(errorText(err, 'Could not save your profile'))
      return false
    } finally {
      setBusy(false)
    }
  }

  const saveAndContinue = async (s: FormStep) => {
    if (await saveStep(s)) setStep(STEPS[STEPS.findIndex((x) => x.key === s) + 1].key)
  }

  // A profile-changed conflict reloads before rethrowing, and a missing-fields
  // refusal is rethrown with its field names as labels; everything else
  // propagates as-is -- PinConfirmDialog shows it inline and clears the PIN.
  const submit = async (mpin: string) => {
    setBusy(true)
    try {
      const p = await orgApi<KycProfile>(orgId, 'investor/profile/submit',
        { method: 'POST', body: JSON.stringify({ mpin }) }, { redirectOn401: false })
      setProfile(p)
      setForm(formOf(p))
      setPinOpen(false)
      setNotice('Submitted. An admin reviews your documents; you will get an email with the result.')
    } catch (err) {
      if (isReloadConflict(err)) await load()
      const res = (err as ApiError | undefined)?.response
      const missing = res?.body?.missing
      if (res?.status === 400 && Array.isArray(missing)) {
        throw new Error(`complete your profile first: ${(missing as string[])
          .map((k) => FIELD_LABELS[k as KycTextField | KycFileField] ?? k).join(', ')}`)
      }
      throw err
    } finally {
      setBusy(false)
    }
  }

  const current = STEPS.find((s) => s.key === step)!

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Profile & verification"
        subtitle="Your details and identity documents. An admin verifies them before you can open a trading account."
        actions={profile ? <Badge tone={kycBadge(profile.status)}>{kycLabel(profile.status)}</Badge> : undefined}
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {!profile || !form ? (
        !error && <Loading lines={4} label="Loading your profile" />
      ) : (
        <>
          <StatusBanner profile={profile} />

          <ol aria-label="Verification steps" className="flex flex-wrap gap-2">
            {STEPS.map((s, i) => (
              <li key={s.key}>
                <Button size="sm" variant={step === s.key ? 'primary' : 'secondary'}
                        aria-current={step === s.key ? 'step' : undefined}
                        onClick={() => setStep(s.key)}>
                  {`${i + 1}. ${s.label}`}
                </Button>
              </li>
            ))}
          </ol>

          {step !== 'review' ? (
            <Card title={current.label}>
              <form onSubmit={(e) => { e.preventDefault(); void saveAndContinue(step) }} noValidate className="space-y-4">
                <fieldset disabled={locked || busy} className="space-y-4">
                  {STEP_FIELDS[step].text.map((k) => (
                    <Field key={k} field={k} value={form[k]} onChange={(v) => setForm({ ...form, [k]: v })} />
                  ))}
                  {STEP_FIELDS[step].files.map((k) => (
                    <div key={k} className="space-y-1">
                      <FileInput id={`kyc-${k}`}
                                 label={profile[k] != null ? `Replace ${FIELD_LABELS[k]}` : FIELD_LABELS[k]}
                                 accept={k === 'photo_file_id' ? PHOTO_ACCEPT : RECEIPT_ACCEPT}
                                 maxBytes={MAX_UPLOAD_BYTES} value={files[k] ?? null}
                                 onChange={(f) => setFiles({ ...files, [k]: f })}
                                 disabled={locked || busy} hint={FILE_HINT[k]} />
                      {profile[k] != null && (
                        <p className="text-sm text-ink-soft">
                          {'On file. '}
                          <a href={`/api/orgs/${orgId}/investor/files/${profile[k]}`} target="_blank" rel="noreferrer"
                             className="text-brand underline underline-offset-2 hover:text-brand-deep">
                            View it
                          </a>
                        </p>
                      )}
                    </div>
                  ))}
                </fieldset>
                {!locked && (
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" disabled={busy}>Save and continue</Button>
                    <Button type="button" variant="secondary" disabled={busy} onClick={() => { void saveStep(step) }}>
                      Save draft
                    </Button>
                  </div>
                )}
              </form>
            </Card>
          ) : (
            <Card title="Review">
              <div className="space-y-4">
                <dl className="inset p-4 grid gap-3 sm:grid-cols-2 text-sm">
                  {TEXT_FIELDS.map((k) => (
                    <div key={k}>
                      <dt className="desk-label">{FIELD_LABELS[k]}</dt>
                      <dd className="text-ink">{fieldValue(k, profile[k])}</dd>
                    </div>
                  ))}
                  {FILE_FIELDS.map((k) => (
                    <div key={k}>
                      <dt className="desk-label">{FIELD_LABELS[k]}</dt>
                      <dd className="text-ink">{profile[k] != null ? 'Uploaded' : 'Not uploaded'}</dd>
                    </div>
                  ))}
                </dl>
                {profile.missing.length > 0 && (
                  <p className="text-sm text-ink-soft">
                    {`Still needed: ${profile.missing.map((k) => FIELD_LABELS[k as KycTextField | KycFileField] ?? k).join(', ')}`}
                  </p>
                )}
                {(profile.status === 'draft' || profile.status === 'rejected') && (
                  <Button onClick={() => setPinOpen(true)} disabled={busy || profile.missing.length > 0}>
                    Submit for verification
                  </Button>
                )}
              </div>
            </Card>
          )}
        </>
      )}

      <PinConfirmDialog
        open={pinOpen}
        title="Submit your profile for verification?"
        confirmLabel="Submit"
        busy={busy}
        onConfirm={submit}
        onCancel={() => setPinOpen(false)}
      >
        <p>An admin checks your details and documents. You cannot edit them while they are under review.</p>
      </PinConfirmDialog>
    </div>
  )
}
