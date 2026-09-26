import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import { isMpinPending, type Me, type MpinPending } from '../lib/types'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'
import Logo from '../components/Logo'
import PinInput from '../components/PinInput'

type Mode = 'loading' | 'set' | 'verify' | 'forgot'

/** Only a same-origin path may be the landing after the MPIN. */
function safeNext(raw: string | null): string {
  if (!raw) return '/'
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

function countdown(until: Date, now: Date): string {
  const s = Math.max(0, Math.ceil((until.getTime() - now.getTime()) / 1000))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/**
 * The second gate. Email and password are already right (a half session);
 * nothing else in the platform answers until the six-digit MPIN is set (first
 * login), verified (every login after) or reset (through the password).
 */
export default function Mpin() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [mode, setMode] = useState<Mode>('loading')
  const [pin, setPin] = useState('')
  const [confirm, setConfirm] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockedUntil, setLockedUntil] = useState<Date | null>(null)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let cancelled = false
    api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
      .then((me) => {
        if (cancelled) return
        if (!isMpinPending(me)) { navigate(next, { replace: true }); return }
        setMode(me.mpin.set ? 'verify' : 'set')
      })
      .catch(() => { if (!cancelled) navigate('/login', { replace: true }) })
    return () => { cancelled = true }
  }, [navigate, next])

  // The lock countdown ticks once a second and releases itself.
  useEffect(() => {
    if (!lockedUntil) return
    const id = setInterval(() => {
      const t = new Date()
      setNow(t)
      if (t >= lockedUntil) { setLockedUntil(null); setError(null) }
    }, 1000)
    return () => clearInterval(id)
  }, [lockedUntil])

  const fail = (err: unknown) => setError(errorText(err, 'Something went wrong'))

  const verify = useCallback(async (value: string) => {
    setBusy(true); setError(null)
    try {
      await api('/api/mpin/verify', { method: 'POST', body: JSON.stringify({ mpin: value }) })
      navigate(next, { replace: true })
    } catch (err) {
      setPin('')
      const res = (err as ApiError).response
      if (res?.status === 423 && typeof res.body?.locked_until === 'string') {
        setLockedUntil(new Date(res.body.locked_until))
      } else if (res?.status === 401 && typeof res.body?.attempts_left === 'number') {
        const n = res.body.attempts_left
        setError(`Wrong MPIN, ${n} ${n === 1 ? 'try' : 'tries'} left`)
      } else {
        fail(err)
      }
    } finally {
      setBusy(false)
    }
  }, [navigate, next])

  const submitSet = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pin.length !== 6) { setError('MPIN must be exactly 6 digits'); return }
    if (pin !== confirm) { setError('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/mpin/set', { method: 'POST', body: JSON.stringify({ mpin: pin, mpin_confirm: confirm }) })
      navigate(next, { replace: true })
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pin.length !== 6) { setError('MPIN must be exactly 6 digits'); return }
    if (pin !== confirm) { setError('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/mpin/reset', {
        method: 'POST', body: JSON.stringify({ password, mpin: pin, mpin_confirm: confirm }),
      })
      navigate(next, { replace: true })
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  const signOut = async () => {
    try { await api('/api/logout', { method: 'POST' }) } catch { /* cookies may already be gone */ }
    navigate('/login', { replace: true })
  }

  const locked = lockedUntil != null && lockedUntil > now
  const lockText = lockedUntil ? `Locked. Try again in ${countdown(lockedUntil, now)}` : null

  if (mode === 'loading') {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper py-12 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="flex justify-center"><Logo size={38} textClass="text-3xl" /></h1>
          <p className="mt-2 text-sm text-ink-soft">
            {mode === 'set' && 'One more step: a six-digit MPIN you will enter at every sign-in.'}
            {mode === 'verify' && 'Your password was right. Now your MPIN.'}
            {mode === 'forgot' && 'Prove your password to choose a new MPIN.'}
          </p>
        </div>
        <div className="bg-card rounded-lg border border-line p-8 space-y-5">
          {mode === 'verify' && (
            <>
              <h2 className="font-display text-lg text-ink">Enter your MPIN</h2>
              <PinInput id="mpin" label="MPIN" value={pin} onChange={setPin}
                        onComplete={verify} disabled={busy || locked}
                        error={locked ? lockText : error} autoFocus />
              <div className="flex items-center justify-between">
                <Button variant="ghost" tone="brand" size="sm"
                        onClick={() => { setMode('forgot'); setPin(''); setConfirm(''); setError(null) }}>
                  Forgot MPIN?
                </Button>
                <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
              </div>
            </>
          )}
          {mode === 'set' && (
            <form onSubmit={submitSet} className="space-y-5">
              <h2 className="font-display text-lg text-ink">Choose your MPIN</h2>
              {error && <Banner kind="error">{error}</Banner>}
              <PinInput id="mpin" label="MPIN" value={pin} onChange={setPin} disabled={busy} autoFocus />
              <PinInput id="mpin-confirm" label="Confirm MPIN" value={confirm} onChange={setConfirm} disabled={busy} />
              <Button type="submit" block busy={busy}>Save MPIN</Button>
              <div className="text-center">
                <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
              </div>
            </form>
          )}
          {mode === 'forgot' && (
            <form onSubmit={submitReset} className="space-y-5">
              <h2 className="font-display text-lg text-ink">Reset your MPIN</h2>
              {error && <Banner kind="error">{error}</Banner>}
              <div>
                <label htmlFor="password" className="desk-label block mb-1">Password</label>
                <Input id="password" type="password" autoComplete="current-password" required
                       value={password} onChange={(e) => setPassword(e.target.value)} disabled={busy} />
              </div>
              <PinInput id="mpin" label="New MPIN" value={pin} onChange={setPin} disabled={busy} />
              <PinInput id="mpin-confirm" label="Confirm new MPIN" value={confirm} onChange={setConfirm} disabled={busy} />
              <Button type="submit" block tone="loss" busy={busy}>Reset MPIN</Button>
              <div className="flex items-center justify-between">
                <Button variant="ghost" tone="brand" size="sm"
                        onClick={() => { setMode('verify'); setPin(''); setConfirm(''); setPassword(''); setError(null) }}>
                  Back
                </Button>
                <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
