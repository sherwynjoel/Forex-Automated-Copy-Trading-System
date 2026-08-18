import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

export default function Welcome() {
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const createOrg = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const org = await api<{ id: number }>('/api/orgs', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      navigate(`/org/${org.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create organization')
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
      <div className="max-w-md w-full space-y-10">
        <h2 className="text-center font-display text-3xl text-brand">Copy Desk</h2>
        {error && (
          <div className="rounded-md bg-loss-wash p-4">
            <p className="text-sm font-medium text-loss-deep">{error}</p>
          </div>
        )}
        <form onSubmit={createOrg} className="space-y-4">
          <h3 className="text-lg font-semibold text-ink">Create an organization</h3>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Organization name"
            aria-label="Organization name"
            className="w-full px-3 py-2 border border-line-strong rounded-md bg-card text-ink sm:text-sm"
          />
          <button
            type="submit"
            className="w-full py-2 px-4 text-sm font-semibold rounded-md text-white bg-brand hover:bg-brand-deep transition-colors"
          >
            Create organization
          </button>
        </form>
        <form onSubmit={useInvite} className="space-y-4">
          <h3 className="text-lg font-semibold text-ink">Or join with an invite</h3>
          <input
            value={invite}
            onChange={(e) => setInvite(e.target.value)}
            placeholder="Paste an invite link or code"
            aria-label="Invite link or code"
            className="w-full px-3 py-2 border border-line-strong rounded-md bg-card text-ink sm:text-sm"
          />
          <button
            type="submit"
            className="w-full py-2 px-4 text-sm font-semibold rounded-md border border-brand text-brand hover:bg-brand-wash transition-colors"
          >
            Join organization
          </button>
        </form>
      </div>
    </div>
  )
}
