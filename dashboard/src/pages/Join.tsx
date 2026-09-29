import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { isMpinPending } from '../lib/types'
import type { Me, MpinPending } from '../lib/types'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Loading from '../components/Loading'

type Outcome =
  | { kind: 'joining' }
  | { kind: 'already' }
  | { kind: 'dead'; message: string }

/**
 * Nothing on this page can name the organization: POST /api/orgs/join
 * answers { org_id, role } and the page leaves at once, and there is no
 * invite preview endpoint. So the title is the generic one.
 */
const TITLE = 'Join the workspace'

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
      let me: Me | MpinPending
      try {
        me = await api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
      } catch {
        if (cancelled) return
        navigate(`/register?invite=${encodeURIComponent(token ?? '')}`,
                 { replace: true })
        return
      }
      if (isMpinPending(me)) {
        if (!cancelled) {
          navigate(`/mpin?next=${encodeURIComponent(`/join/${token ?? ''}`)}`, { replace: true })
        }
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
    <AuthCard
      title={TITLE}
      lead={outcome.kind === 'joining' ? 'Checking your invite.' : undefined}
    >
      <div className="space-y-4 text-center">
        {outcome.kind === 'joining' && <Loading lines={2} label="Joining the workspace" />}
        {outcome.kind === 'already' && (
          <>
            <p className="text-sm text-ink">
              You are already a member of this organization.
            </p>
            <Button to="/welcome">Open MirrorFleet</Button>
          </>
        )}
        {outcome.kind === 'dead' && (
          <>
            <Banner kind="error">{outcome.message}</Banner>
            <Link to="/login" className="inline-block text-sm font-medium text-brand hover:text-brand-deep">
              Sign in
            </Link>
          </>
        )}
      </div>
    </AuthCard>
  )
}
