import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'

/** The start of the server's detail when open registration is switched off (api/src/api/auth.py). */
const CLOSED_DETAIL = 'Self-service registration is disabled'

/** True only for the closed-registration 403; every other failure keeps its own detail. */
function isRegistrationClosed(err: unknown): boolean {
  const res = err instanceof Error ? (err as ApiError).response : undefined
  const detail = res?.body?.detail
  return res?.status === 403 && typeof detail === 'string' && detail.startsWith(CLOSED_DETAIL)
}

export default function Register() {
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  // Registration is closed to anyone without a valid invite. Say so in plain
  // words the moment the server answers; the typed values stay in the form.
  const [closed, setClosed] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()
  // An invite token authorizes signup even when open registration is off,
  // and names the organization to join once the account exists.
  const [params] = useSearchParams()
  const invite = params.get('invite')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setClosed(false)
    setIsLoading(true)

    try {
      await api('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email, password, display_name: displayName,
          ...(invite ? { invite_token: invite } : {}),
        }),
      })
      // Registration leaves a half session: the MPIN comes first, and the
      // MPIN page hands over to the invite (or the welcome screen) after.
      const next = invite ? `/join/${invite}` : '/welcome'
      navigate(`/mpin?next=${encodeURIComponent(next)}`, { replace: true })
    } catch (err) {
      if (isRegistrationClosed(err)) setClosed(true)
      else setError(errorText(err, 'Registration failed'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AuthCard
      title="Create your account"
      lead={invite
        ? 'Create your account to accept the invitation.'
        : 'Create an account to open the desk.'}
      footer={
        <p className="text-center text-sm text-ink-soft">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-brand hover:text-brand-deep">
            Sign in
          </Link>
        </p>
      }
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        {error && <Banner kind="error">{error}</Banner>}
        {/* Always mounted, so the notice is announced when it appears. */}
        <div role="status" aria-live="polite">
          {closed && (
            <Banner kind="notice" announce={false}>
              {invite
                ? 'This invite link is invalid or expired, and registration is otherwise by invite only. '
                  + 'Ask whoever invited you for a fresh link.'
                : 'Registration is by invite only. Ask an admin of the workspace you are joining '
                  + 'for an invite link, then open it to create your account.'}
            </Banner>
          )}
        </div>
        <div>
          <label htmlFor="displayName" className="desk-label block mb-1">Display name</label>
          <Input
            id="displayName"
            name="displayName"
            type="text"
            autoComplete="name"
            required
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="email" className="desk-label block mb-1">Email</label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="password" className="desk-label block mb-1">Password</label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p id="password-hint" className="mt-1 text-xs text-ink-soft">At least 10 characters.</p>
        </div>
        <Button type="submit" block disabled={isLoading}>
          {isLoading ? 'Creating account...' : 'Create account'}
        </Button>
      </form>
    </AuthCard>
  )
}
