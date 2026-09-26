import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import { isMpinPending } from '../lib/types'
import type { Me, MpinPending } from '../lib/types'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'
import Logo from '../components/Logo'
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
    <div className="min-h-screen flex items-center justify-center bg-paper px-4">
      <div className="max-w-md w-full space-y-8">
        <h1 className="flex justify-center"><Logo size={38} textClass="text-3xl" /></h1>
        {error && <Banner kind="error">{error}</Banner>}

        {orgs != null && orgs.length > 0 && (
          <div className="bg-card rounded-lg border border-line p-6 space-y-3">
            <h2 className="text-lg font-display font-semibold text-ink">Your workspaces</h2>
            <ul className="space-y-2">
              {orgs.map((o) => (
                <li key={o.id}>
                  <Link
                    to={`/org/${o.id}`}
                    className="flex items-center justify-between gap-3 rounded border border-line-strong px-3 py-2.5 text-sm text-ink hover:border-brand hover:bg-brand-wash transition-colors"
                  >
                    <span className="font-medium">{o.name}</span>
                    <span className="desk-label">{roleLabel(o.role)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        <form onSubmit={createOrg} className="bg-card rounded-lg border border-line p-6 space-y-4">
          <h2 className="text-lg font-display font-semibold text-ink">Create an organization</h2>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Organization name"
            aria-label="Organization name"
          />
          <Button type="submit" block disabled={creating}>
            {creating ? 'Creating…' : 'Create organization'}
          </Button>
        </form>
        <form onSubmit={useInvite} className="bg-card rounded-lg border border-line p-6 space-y-4">
          <h2 className="text-lg font-display font-semibold text-ink">Or join with an invite</h2>
          <Input
            value={invite}
            onChange={(e) => setInvite(e.target.value)}
            placeholder="Paste an invite link or code"
            aria-label="Invite link or code"
          />
          <Button type="submit" variant="secondary" tone="brand" block>
            Join organization
          </Button>
        </form>
      </div>
    </div>
  )
}
