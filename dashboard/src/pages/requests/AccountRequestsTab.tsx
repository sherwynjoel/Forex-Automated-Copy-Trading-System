import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi, type ApiError } from '../../lib/api'
import { errorText, formatWhen } from '../../lib/format'
import { requestBadge, requestLabel } from '../../lib/identity'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PinInput from '../../components/PinInput'
import Select from '../../components/Select'
import { mpinErrorText } from '../../components/PinConfirmDialog'
import { Row, Section } from './RequestDetailsDrawer'
import type { DeskTabProps } from './VerificationTab'
import type { Account, AccountRequest, RevealedPasswords } from '../../lib/types'

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'
const TEXTAREA = 'w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink'

/**
 * Live account requests. Fulfil opens a drawer: the admin reveals the two
 * passwords with their own MPIN (inline, not a second dialog over the
 * drawer), opens the login at the broker, then records login, server and
 * optionally the MirrorFleet MT5 account to link. Reject needs a note.
 */
export default function AccountRequestsTab({ orgId, control, show, onDone }: DeskTabProps) {
  const [rows, setRows] = useState<AccountRequest[] | null>(null)
  // A failed load stays in the tab (with Retry): the page's poll clears its
  // own banner, which would leave a misleading empty queue behind.
  const [loadError, setLoadError] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [target, setTarget] = useState<AccountRequest | null>(null)
  const [pin, setPin] = useState('')
  const [pinError, setPinError] = useState<string | null>(null)
  const [revealed, setRevealed] = useState<RevealedPasswords | null>(null)
  const [login, setLogin] = useState('')
  const [server, setServer] = useState('')
  const [accountId, setAccountId] = useState('')
  const [note, setNote] = useState('')
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState<AccountRequest | null>(null)
  const [rejectNote, setRejectNote] = useState('')
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Latest-applied wins, as on the page: a load started before a decision
  // must not land over the queue fetched after it.
  const requestSeq = useRef(0)
  const lastApplied = useRef(0)

  const load = useCallback(async () => {
    const seq = ++requestSeq.current
    try {
      const [r, a] = await Promise.all([
        orgApi<AccountRequest[]>(orgId, 'account-requests'),
        orgApi<Account[]>(orgId, 'accounts'),
      ])
      if (seq <= lastApplied.current) return
      lastApplied.current = seq
      setRows(r); setAccounts(a); setLoadError(null)
    } catch (err) {
      if (seq <= lastApplied.current) return
      lastApplied.current = seq
      setLoadError(errorText(err, 'Could not load the account requests'))
      setRows((x) => x ?? [])
    }
  }, [orgId])

  useEffect(() => { load() }, [load])

  const openFulfil = (r: AccountRequest) => {
    setTarget(r); setPin(''); setPinError(null); setRevealed(null)
    setLogin(''); setServer(''); setAccountId(''); setNote(''); setDrawerError(null)
  }
  // Closing forgets the passwords: they are on screen only while needed.
  const closeFulfil = () => { setTarget(null); setRevealed(null); setPin('') }

  const reveal = async () => {
    if (!target || pin.length !== 6) return
    setBusy(true); setPinError(null)
    try {
      setRevealed(await orgApi<RevealedPasswords>(orgId, `account-requests/${target.id}/reveal`,
        { method: 'POST', body: JSON.stringify({ mpin: pin }) }, { redirectOn401: false }))
    } catch (err) {
      setPinError(mpinErrorText(err, 'Could not reveal the passwords'))
      if ((err as ApiError).response?.status === 409) void load()
    } finally {
      setPin('')
      setBusy(false)
    }
  }

  const fulfil = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!target) return
    const loginNo = Number(login.trim())
    if (!/^\d+$/.test(login.trim()) || !Number.isSafeInteger(loginNo) || loginNo <= 0) {
      setDrawerError('Enter the MT5 login number'); return
    }
    if (!server.trim()) { setDrawerError('Enter the MT5 server'); return }
    setBusy(true); setDrawerError(null)
    try {
      await orgApi(orgId, `account-requests/${target.id}/fulfil`, {
        method: 'POST',
        body: JSON.stringify({
          mt5_login: loginNo, mt5_server: server.trim(),
          account_id: accountId ? Number(accountId) : null, note: note.trim() || null,
        }),
      })
      const id = target.id
      closeFulfil()
      await load()
      onDone(`Account request #${id} fulfilled`)
    } catch (err) {
      setDrawerError(errorText(err, 'Could not fulfil the request'))
      // 409: decided elsewhere -- its passwords are gone; refresh the row.
      if ((err as ApiError).response?.status === 409) { setRevealed(null); void load() }
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    if (!rejecting) return
    setBusy(true); setDialogError(null)
    try {
      await orgApi(orgId, `account-requests/${rejecting.id}/reject`, {
        method: 'POST', body: JSON.stringify({ note: rejectNote.trim() }),
      })
      const id = rejecting.id
      setRejecting(null)
      await load()
      onDone(`Account request #${id} rejected`)
    } catch (err) {
      setDialogError(errorText(err, 'The action failed'))
      if ((err as ApiError).response?.status === 409) void load()
    } finally {
      setBusy(false)
    }
  }

  if (rows == null) return <Loading lines={4} label="Loading account requests" />
  const visible = show === 'open' ? rows.filter((r) => r.status === 'requested') : rows
  const linkable = accounts.filter((a) => a.platform === 'mt5' && a.role !== 'master')
  const who = (r: AccountRequest) => r.display_name ?? r.email ?? `investor ${r.user_id}`

  return (
    <>
      {loadError && (
        <div className="p-3 space-y-2">
          <Banner kind="error">{loadError}</Banner>
          <Button size="sm" variant="secondary" onClick={() => { void load() }}>Retry</Button>
        </div>
      )}
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left border-b border-line">
            <th className={TH}>Requested</th>
            <th className={TH}>Investor</th>
            <th className={TH}>Package</th>
            <th className={TH}>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 && !loadError && (
            <tr><td colSpan={5} className="text-center py-8 text-ink-faint">
              {show === 'open' ? 'No open account requests' : 'No account requests yet'}
            </td></tr>
          )}
          {visible.map((r) => (
            <tr key={r.id} className="border-b border-line last:border-0 align-top">
              <td data-label="Requested" className={`num ${TD}`}>{formatWhen(r.created_at)}</td>
              <td data-label="Investor" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{r.display_name ?? '—'}</div>
                  <div className="text-xs text-ink-soft">{r.email ?? ''}</div>
                </div>
              </td>
              <td data-label="Package" className={TD}>
                <div className="min-w-0">
                  <div className="text-ink">{r.package_name}</div>
                  <div className="num text-xs text-ink-soft">{`1:${r.leverage}`}</div>
                </div>
              </td>
              <td data-label="Status" className={TD}>
                <div className="min-w-0">
                  <Badge tone={requestBadge(r.status)}>{requestLabel(r.status)}</Badge>
                  {r.status === 'fulfilled' && (
                    <div className="num text-xs text-ink-soft mt-1">{`${r.mt5_login} · ${r.mt5_server}`}</div>
                  )}
                  {r.decision_note && <div className="text-xs text-ink-soft mt-1">{r.decision_note}</div>}
                </div>
              </td>
              <td className={TD}>
                {control && r.status === 'requested' && (
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <Button size="sm" aria-label={`Fulfil account request ${r.id}`} disabled={busy}
                            onClick={() => openFulfil(r)}>Fulfil</Button>
                    <Button size="sm" variant="secondary" tone="loss" aria-label={`Reject account request ${r.id}`}
                            disabled={busy}
                            onClick={() => { setRejecting(r); setRejectNote(''); setDialogError(null) }}>
                      Reject
                    </Button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Drawer open={target != null} title={target ? `Fulfil request #${target.id}` : ''} onClose={closeFulfil} busy={busy}>
        {target && (
          <div className="space-y-4">
            <Section title="Request">
              <Row label="Investor" value={who(target)} />
              <Row label="Email" value={target.email ?? '—'} />
              <Row label="Package" value={target.package_name} />
              <Row label="Leverage" value={`1:${target.leverage}`} mono />
            </Section>
            <section>
              <h3 className="desk-label mb-2">Passwords</h3>
              <div className="inset p-3 space-y-3 text-sm">
                {revealed ? (
                  <>
                    <dl className="space-y-1.5">
                      <Row label="Main password" value={revealed.main_password} mono />
                      <Row label="Investor password" value={revealed.investor_password} mono />
                    </dl>
                    <p className="text-xs text-ink-soft">
                      Create the account at the broker with these now. They are deleted when you fulfil or reject.
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-ink-soft">
                      The investor chose both passwords. Confirm with your MPIN to see them; every reveal is logged.
                    </p>
                    <PinInput id="reveal-mpin" label="Your MPIN" value={pin} onChange={setPin} disabled={busy} error={pinError} />
                    <Button size="sm" variant="secondary" disabled={busy || pin.length !== 6} onClick={() => { void reveal() }}>
                      Reveal passwords
                    </Button>
                  </>
                )}
              </div>
            </section>
            <form onSubmit={fulfil} noValidate className="space-y-4">
              {drawerError && <Banner kind="error" onDismiss={() => setDrawerError(null)}>{drawerError}</Banner>}
              <div>
                <label htmlFor="fulfil-login" className="desk-label block mb-1">MT5 login</label>
                <Input id="fulfil-login" num inputMode="numeric" value={login} disabled={busy}
                       onChange={(e) => setLogin(e.target.value)} />
              </div>
              <div>
                <label htmlFor="fulfil-server" className="desk-label block mb-1">MT5 server</label>
                <Input id="fulfil-server" value={server} disabled={busy} onChange={(e) => setServer(e.target.value)} />
              </div>
              <div>
                <label htmlFor="fulfil-account" className="desk-label block mb-1">Link to account</label>
                <Select id="fulfil-account" value={accountId} disabled={busy} onChange={(e) => setAccountId(e.target.value)}>
                  <option value="">Do not link</option>
                  {linkable.map((a) => (
                    <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                      {`${a.nickname ?? a.trader_login} (MT5 ${a.mt5?.login ?? a.ctid_trader_account_id})`}
                    </option>
                  ))}
                </Select>
                <p className="mt-1 text-xs text-ink-soft">
                  Optional: link the MirrorFleet MT5 account so the investor sees its equity and positions.
                </p>
              </div>
              <div>
                <label htmlFor="fulfil-note" className="desk-label block mb-1">Note (optional)</label>
                <textarea id="fulfil-note" value={note} rows={2} disabled={busy}
                          onChange={(e) => setNote(e.target.value)} className={TEXTAREA} />
              </div>
              <Button type="submit" disabled={busy}>Fulfil request</Button>
            </form>
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={rejecting != null}
        title={rejecting ? `Reject the account request of ${who(rejecting)}` : ''}
        confirmLabel="Reject"
        danger
        busy={busy}
        disabled={rejectNote.trim() === ''}
        onConfirm={() => { void reject() }}
        onCancel={() => setRejecting(null)}
      >
        {dialogError && <Banner kind="error" onDismiss={() => setDialogError(null)}>{dialogError}</Banner>}
        <label className="block">
          <span className="desk-label block mb-1">Note</span>
          <textarea aria-label="Note" value={rejectNote} rows={2} onChange={(e) => setRejectNote(e.target.value)}
                    className={TEXTAREA} />
        </label>
      </ConfirmDialog>
    </>
  )
}
