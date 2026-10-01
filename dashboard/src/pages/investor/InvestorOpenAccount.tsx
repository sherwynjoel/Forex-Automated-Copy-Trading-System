import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { PASSWORD_RULE, generatePassword, passwordProblem, requestBadge, requestLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import NextStep from './NextStep'
import type { AccountPackage, AccountRequest, KycProfile, KycStatus } from '../../lib/types'

function PasswordField({ id, label, hint, value, shown, onChange, onGenerate, generateLabel }: {
  id: string; label: string; hint?: string; value: string; shown: boolean
  onChange: (v: string) => void; onGenerate: () => void; generateLabel: string
}) {
  return (
    <div>
      <label htmlFor={id} className="desk-label block mb-1">{label}</label>
      <div className="flex flex-wrap items-center gap-2">
        <Input id={id} num type={shown ? 'text' : 'password'} autoComplete="new-password" value={value}
               onChange={(e) => onChange(e.target.value)} />
        <Button type="button" variant="ghost" size="sm" aria-label={generateLabel} onClick={onGenerate}>Generate</Button>
      </div>
      {hint && <p className="mt-1 text-xs text-ink-soft">{hint}</p>}
    </div>
  )
}

/**
 * Request a live MT5 account. Verification comes first; then one request at
 * a time: the packages the workspace offers, a leverage and two passwords
 * the investor keeps, confirmed with the MPIN. The admin opens the account
 * at the broker by hand and the login appears here (and on Account).
 */
export default function InvestorOpenAccount() {
  const { orgId } = useOrg()
  const [kyc, setKyc] = useState<KycStatus | null>(null)
  const [packages, setPackages] = useState<AccountPackage[]>([])
  const [requests, setRequests] = useState<AccountRequest[]>([])
  const [chosen, setChosen] = useState<AccountPackage | null>(null)
  const [leverage, setLeverage] = useState('')
  const [mainPassword, setMainPassword] = useState('')
  const [investorPassword, setInvestorPassword] = useState('')
  const [shown, setShown] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [pinOpen, setPinOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [p, pk, rq] = await Promise.all([
        orgApi<KycProfile>(orgId, 'investor/profile'),
        orgApi<AccountPackage[]>(orgId, 'investor/account-packages'),
        orgApi<AccountRequest[]>(orgId, 'investor/account-requests'),
      ])
      setKyc(p.status); setPackages(pk); setRequests(rq)
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load account opening'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const latest = requests[0] ?? null
  const current = latest && (latest.status === 'requested' || latest.status === 'fulfilled') ? latest : null

  const choose = (p: AccountPackage) => {
    setChosen(p)
    setLeverage(String(p.leverage_options[0]))
    setMainPassword(''); setInvestorPassword(''); setShown(false); setFormError(null)
  }

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    const problem = passwordProblem(mainPassword) ?? passwordProblem(investorPassword)
    if (problem) { setFormError(problem); return }
    if (mainPassword === investorPassword) {
      setFormError('The investor password must differ from the main password')
      return
    }
    setFormError(null)
    setPinOpen(true)
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const confirm = async (mpin: string) => {
    if (!chosen) return
    setBusy(true)
    try {
      await orgApi<AccountRequest>(orgId, 'investor/account-requests', {
        method: 'POST',
        body: JSON.stringify({
          package_id: chosen.id, leverage: Number(leverage),
          main_password: mainPassword, investor_password: investorPassword, mpin,
        }),
      }, { redirectOn401: false })
      setPinOpen(false)
      setChosen(null); setMainPassword(''); setInvestorPassword('')
      setNotice('Request sent. An admin opens your account at the broker; you will get an email with your login.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!current) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<AccountRequest>(orgId, `investor/account-requests/${current.id}/cancel`, { method: 'POST' })
      setNotice('Request cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the request'))
    } finally {
      setBusy(false)
      setCancelOpen(false)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Open account" subtitle="Request a live MT5 trading account from the packages this workspace offers." />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {!loaded ? (
        <Loading lines={3} label="Loading account opening" />
      ) : kyc !== 'approved' ? (
        <>
          <NextStep title="Verify your identity first">
            An admin verifies your identity before you can request a trading account: one form and four uploads.
          </NextStep>
          <Button to={`/org/${orgId}/invest/profile`}>Go to Profile & verification</Button>
        </>
      ) : current ? (
        <Card title="Your request">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="desk-label">Package</dt><dd className="text-ink">{current.package_name}</dd></div>
            <div><dt className="desk-label">Leverage</dt><dd className="num text-ink">{`1:${current.leverage}`}</dd></div>
            <div><dt className="desk-label">Status</dt>
              <dd><Badge tone={requestBadge(current.status)}>{requestLabel(current.status)}</Badge></dd></div>
            <div><dt className="desk-label">Requested on</dt><dd className="num text-ink">{formatWhen(current.created_at)}</dd></div>
            {current.status === 'fulfilled' && (
              <>
                <div><dt className="desk-label">MT5 login</dt><dd className="num text-ink">{current.mt5_login}</dd></div>
                <div><dt className="desk-label">Server</dt><dd className="num text-ink">{current.mt5_server}</dd></div>
              </>
            )}
          </dl>
          {current.status === 'requested' ? (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <p className="text-sm text-ink-soft flex-1 min-w-0">
                An admin is opening your account at the broker. You will get an email with your login.
              </p>
              <Button variant="secondary" tone="loss" onClick={() => setCancelOpen(true)} disabled={busy}>
                Cancel request
              </Button>
            </div>
          ) : (
            <p className="mt-4 text-sm text-ink-soft">
              Sign in to MetaTrader 5 with this login and the passwords you chose.
            </p>
          )}
        </Card>
      ) : (
        <>
          {latest?.status === 'rejected' && (
            <Banner kind="warn" announce={false}>
              {`Your last request was rejected: ${latest.decision_note ?? 'no reason given'}`}
            </Banner>
          )}
          {packages.length === 0 ? (
            <NextStep title="No account packages yet">
              This workspace has not published any account packages yet.
            </NextStep>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {packages.map((p) => (
                <Card key={p.id} title={p.name}>
                  <dl className="inset p-4 grid grid-cols-2 gap-3 text-sm">
                    <div><dt className="desk-label">Minimum deposit</dt>
                      <dd className="num text-ink">{money(p.min_deposit, p.currency)}</dd></div>
                    <div><dt className="desk-label">Spread</dt>
                      <dd className="num text-ink">{p.spread_label ?? '—'}</dd></div>
                    <div className="col-span-2"><dt className="desk-label">Leverage</dt>
                      <dd className="num text-ink">{p.leverage_options.map((l) => `1:${l}`).join(' · ')}</dd></div>
                  </dl>
                  <div className="mt-4">
                    <Button aria-label={`Choose ${p.name}`} variant={chosen?.id === p.id ? 'primary' : 'secondary'}
                            onClick={() => choose(p)}>
                      {chosen?.id === p.id ? 'Chosen' : 'Choose'}
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          )}

          {chosen && (
            <Card title={`Request a ${chosen.name} account`}>
              <form onSubmit={review} noValidate className="space-y-4">
                {formError && <Banner kind="error" onDismiss={() => setFormError(null)}>{formError}</Banner>}
                <div>
                  <label htmlFor="open-leverage" className="desk-label block mb-1">Leverage</label>
                  <Select id="open-leverage" value={leverage} onChange={(e) => setLeverage(e.target.value)}>
                    {chosen.leverage_options.map((l) => <option key={l} value={l}>{`1:${l}`}</option>)}
                  </Select>
                </div>
                <PasswordField id="open-main" label="Main password" value={mainPassword} shown={shown}
                               onChange={setMainPassword} generateLabel="Generate main password"
                               onGenerate={() => { setMainPassword(generatePassword()); setShown(true) }} />
                <PasswordField id="open-investor" label="Investor password" value={investorPassword} shown={shown}
                               hint="Read-only access: for someone who should see the account but not trade."
                               onChange={setInvestorPassword} generateLabel="Generate investor password"
                               onGenerate={() => { setInvestorPassword(generatePassword()); setShown(true) }} />
                <p className="text-xs text-ink-soft">
                  {`Both: ${PASSWORD_RULE}. Write them down: the admin uses them once to create your account, then MirrorFleet deletes them.`}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="secondary" onClick={() => setShown(!shown)}>
                    {shown ? 'Hide passwords' : 'Show passwords'}
                  </Button>
                  <Button type="submit" disabled={busy}>Request account</Button>
                </div>
              </form>
            </Card>
          )}
        </>
      )}

      <PinConfirmDialog
        open={pinOpen}
        title={`Request a ${chosen?.name ?? ''} account at 1:${leverage}?`}
        confirmLabel="Request"
        busy={busy}
        onConfirm={confirm}
        onCancel={() => setPinOpen(false)}
      >
        <p>An admin opens the account at the broker with the passwords you chose and emails you the login.</p>
      </PinConfirmDialog>

      <ConfirmDialog
        open={cancelOpen}
        title="Cancel this request?"
        confirmLabel="Cancel request"
        danger
        busy={busy}
        onConfirm={() => { void cancel() }}
        onCancel={() => setCancelOpen(false)}
      >
        <p>Your passwords are deleted with it. You can request again later.</p>
      </ConfirmDialog>
    </div>
  )
}
