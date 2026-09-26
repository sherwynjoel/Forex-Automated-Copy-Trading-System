import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import Banner from './Banner'
import Button from './Button'
import Input from './Input'
import PinInput from './PinInput'

/**
 * Your own login, not the org's: rotate the password and cut every other
 * session loose. Both live here rather than behind a role check -- every
 * member owns their own credentials.
 */
export default function AccountSecurity() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [currentMpin, setCurrentMpin] = useState('')
  const [newMpin, setNewMpin] = useState('')
  const [confirmMpin, setConfirmMpin] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const navigate = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setNotice(null)
    setProblem(null)
    setBusy(true)
    try {
      await api('/api/me/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      setCurrent('')
      setNext('')
      setNotice('Password changed. Any other device signed in as you has been signed out.')
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'Could not change the password')
    } finally {
      setBusy(false)
    }
  }

  const submitMpin = async (e: React.FormEvent) => {
    e.preventDefault()
    setNotice(null)
    setProblem(null)
    if (newMpin !== confirmMpin) { setProblem('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/me/mpin', {
        method: 'POST',
        body: JSON.stringify({ current_mpin: currentMpin, mpin: newMpin, mpin_confirm: confirmMpin }),
      })
      setCurrentMpin(''); setNewMpin(''); setConfirmMpin('')
      setNotice('MPIN changed. Use the new one at your next sign-in; any other device signed in as you has been signed out.')
    } catch (err) {
      const res = (err as ApiError).response
      const left = res?.body?.attempts_left
      const until = res?.body?.locked_until
      if (res?.status === 401 && typeof left === 'number') {
        setProblem(`Wrong MPIN, ${left} ${left === 1 ? 'try' : 'tries'} left`)
      } else if (res?.status === 423 && typeof until === 'string') {
        const minutes = Math.max(1, Math.ceil((Date.parse(until) - Date.now()) / 60000))
        setProblem(`MPIN locked. Try again in about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`)
      } else {
        setProblem(err instanceof Error ? err.message : 'Could not change the MPIN')
      }
    } finally {
      setBusy(false)
    }
  }

  const signOutEverywhere = async () => {
    setProblem(null)
    setBusy(true)
    try {
      await api('/api/me/logout-all', { method: 'POST' })
      navigate('/login')
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'Could not sign out everywhere')
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4 border-t border-line pt-6">
      <h3 className="text-lg font-semibold text-ink">Your login</h3>
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {problem && <Banner kind="error" onDismiss={() => setProblem(null)}>{problem}</Banner>}

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="current-password" className="desk-label block mb-1">
            Current password
          </label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new-password" className="desk-label block mb-1">
            New password
          </label>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={busy || !current || !next}>
          Change password
        </Button>
      </form>

      <form onSubmit={submitMpin} className="space-y-3">
        <div className="flex flex-wrap items-start gap-4">
          <PinInput id="current-mpin" label="Current MPIN" value={currentMpin} onChange={setCurrentMpin} disabled={busy} />
          <PinInput id="new-mpin" label="New MPIN" value={newMpin} onChange={setNewMpin} disabled={busy} />
          <PinInput id="confirm-mpin" label="Confirm new MPIN" value={confirmMpin} onChange={setConfirmMpin} disabled={busy} />
        </div>
        <Button type="submit" disabled={busy || currentMpin.length !== 6 || newMpin.length !== 6 || confirmMpin.length !== 6}>
          Change MPIN
        </Button>
      </form>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={signOutEverywhere} disabled={busy}>
          Sign out everywhere
        </Button>
        <p className="text-sm text-ink-soft">
          Ends every session for your account, on this device and any other.
          Use it if you think a login was stolen.
        </p>
      </div>
    </div>
  )
}
