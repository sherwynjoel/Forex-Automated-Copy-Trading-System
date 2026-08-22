import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import Logo from '../components/Logo'

type Outcome =
  | { kind: 'joining' }
  | { kind: 'already' }
  | { kind: 'dead'; message: string }

/**
 * Accept an invite.
 *
 * Joining requires an account, and an invited person usually does not have
 * one yet -- so a 401 here is the NORMAL first step, not an error. It sends
 * them to sign up with the token in hand (the register endpoint accepts a
 * valid invite as authorization even when open signup is closed), and the
 * signup completes the join.
 *
 * Every outcome offers a way forward: the previous version left an already-
 * member on a bare sentence with no link, and bounced a new invitee to a
 * login page they could not get past.
 */
export default function Join() {
  const { token } = useParams()
  const navigate = useNavigate()
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'joining' })

  useEffect(() => {
    let cancelled = false
    const join = async () => {
      // Ask who we are FIRST. A visitor with no session also has no CSRF
      // cookie, so attempting the join would be refused at the CSRF layer
      // (403) before auth was ever considered -- an unreadable failure for
      // the most ordinary case there is. /api/me is a plain GET.
      try {
        await api('/api/me', undefined, { redirectOn401: false })
      } catch {
        if (cancelled) return
        navigate(`/register?invite=${encodeURIComponent(token ?? '')}`,
                 { replace: true })
        return
      }

      try {
        const result = await api<{ org_id: number }>('/api/orgs/join', {
          method: 'POST',
          body: JSON.stringify({ token }),
        }, { redirectOn401: false })
        if (!cancelled) navigate(`/org/${result.org_id}`, { replace: true })
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : ''
        if (message.includes('409')) {
          setOutcome({ kind: 'already' })
          return
        }
        setOutcome({
          kind: 'dead',
          message: message.includes('410')
            ? 'This invite is invalid or expired — it may already have been '
              + 'used. Ask whoever invited you for a fresh link.'
            : 'Could not join the organization.',
        })
      }
    }
    join()
    return () => { cancelled = true }
  }, [token, navigate])

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper px-4">
      <div className="max-w-md w-full text-center space-y-4">
        <h2 className="flex justify-center"><Logo size={38} textClass="text-3xl" /></h2>
        {outcome.kind === 'joining' && (
          <p className="text-sm text-ink-soft">Joining organization…</p>
        )}
        {outcome.kind === 'already' && (
          <>
            <p className="text-sm text-ink">
              You are already a member of this organization.
            </p>
            <Link
              to="/welcome"
              className="inline-block px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep transition-colors"
            >
              Open MirrorFleet
            </Link>
          </>
        )}
        {outcome.kind === 'dead' && (
          <>
            <p className="text-sm font-medium text-loss-deep">{outcome.message}</p>
            <Link to="/login" className="text-sm text-brand hover:underline">
              Sign in
            </Link>
          </>
        )}
      </div>
    </div>
  )
}
