import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import Banner from './Banner'

/**
 * Your own login, not the org's: rotate the password and cut every other
 * session loose. Both live here rather than behind a role check -- every
 * member owns their own credentials.
 */
export default function AccountSecurity() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
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
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            className="rounded border border-line-strong bg-card px-3 py-2 text-sm text-ink"
          />
        </div>
        <div>
          <label htmlFor="new-password" className="desk-label block mb-1">
            New password
          </label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            className="rounded border border-line-strong bg-card px-3 py-2 text-sm text-ink"
          />
        </div>
        <button
          type="submit"
          disabled={busy || !current || !next}
          className="min-h-11 md:min-h-0 px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep transition-colors disabled:opacity-50"
        >
          Change password
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={signOutEverywhere}
          disabled={busy}
          className="min-h-11 md:min-h-0 px-4 py-2 text-sm font-semibold rounded border border-line-strong text-ink hover:bg-line transition-colors disabled:opacity-50"
        >
          Sign out everywhere
        </button>
        <p className="text-sm text-ink-soft">
          Ends every session for your account, on this device and any other.
          Use it if you think a login was stolen.
        </p>
      </div>
    </div>
  )
}
