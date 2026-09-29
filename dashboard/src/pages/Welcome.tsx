import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import { isMpinPending } from '../lib/types'
import type { Me, MpinPending } from '../lib/types'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'
import { roleLabel } from '../lib/roles'

interface MyOrg { id: number; name: string; role: string }

export default function Welcome() {
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // Organizations this account already belongs to. Without these the page
  // was a dead end for an existing member: everything that lands here --
  // leaving an org, an invite you already accepted -- offered only "create"
  // and "join", with no way back into a workspace you are already in.
  const [orgs, setOrgs] = useState<MyOrg[] | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    api<Me | MpinPending>('/api/me')
      .then((me) => {
        if (cancelled) return
        if (isMpinPending(me)) {
          navigate('/mpin', { replace: true })
          return
        }
        setOrgs(me.orgs ?? [])
      })
      .catch(() => { if (!cancelled) setOrgs([]) })
    return () => { cancelled = true }
  }, [navigate])

  const createOrg = async (e: React.FormEvent) => {
    e.preventDefault()
    if (creating) return
    try {
      setCreating(true)
      const org = await api<{ id: number }>('/api/orgs', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      navigate(`/org/${org.id}`)
    } catch (err) {
      setError(errorText(err, 'Could not create organization'))
    } finally {
      setCreating(false)
    }
  }

  const useInvite = (e: React.FormEvent) => {
    e.preventDefault()
    const match = invite.match(/\/join\/([A-Za-z0-9_-]+)/)
    const token = match ? match[1] : invite.trim()
    if (token) navigate(`/join/${token}`)
  }

  return (
    <AuthCard
      title="Welcome"
      lead="An organization is the workspace that holds your trading accounts, your team and its settings."
    >
      <div className="space-y-5">
        {error && <Banner kind="error">{error}</Banner>}

        {orgs != null && orgs.length > 0 && (
          <section aria-labelledby="welcome-workspaces" className="inset p-5 space-y-3">
            <h2 id="welcome-workspaces" className="text-lg font-display font-semibold text-ink">
              Your workspaces
            </h2>
            <ul className="space-y-2">
              {orgs.map((o) => (
                <li key={o.id}>
                  <Link
                    to={`/org/${o.id}`}
                    className="flex items-center justify-between gap-3 rounded-control border border-line-strong px-3 py-2.5 text-sm text-ink hover:border-brand hover:bg-brand-wash transition-colors"
                  >
                    <span className="font-medium">{o.name}</span>
                    <span className="desk-label">{roleLabel(o.role)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section aria-labelledby="welcome-create" className="inset p-5">
          <form onSubmit={createOrg} className="space-y-4">
            <h2 id="welcome-create" className="text-lg font-display font-semibold text-ink">
              Create an organization
            </h2>
            <div>
              <label htmlFor="org-name" className="desk-label block mb-1">Organization name</label>
              <Input
                id="org-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                autoComplete="organization"
              />
            </div>
            <Button type="submit" block disabled={creating}>
              {creating ? 'Creating…' : 'Create organization'}
            </Button>
          </form>
        </section>

        <section aria-labelledby="welcome-join" className="inset p-5">
          <form onSubmit={useInvite} className="space-y-4">
            <h2 id="welcome-join" className="text-lg font-display font-semibold text-ink">
              Join with an invite
            </h2>
            <div>
              <label htmlFor="invite-code" className="desk-label block mb-1">Invite link or code</label>
              <Input
                id="invite-code"
                value={invite}
                onChange={(e) => setInvite(e.target.value)}
                aria-describedby="invite-code-hint"
              />
              <p id="invite-code-hint" className="mt-1 text-xs text-ink-soft">
                Paste the whole link from your invite, or just the code at its end.
              </p>
            </div>
            <Button type="submit" variant="secondary" tone="brand" block>
              Join organization
            </Button>
          </form>
        </section>
      </div>
    </AuthCard>
  )
}
