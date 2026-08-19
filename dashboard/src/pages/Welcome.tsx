import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import Banner from '../components/Banner'
import Logo from '../components/Logo'

export default function Welcome() {
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const navigate = useNavigate()

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
        <form onSubmit={createOrg} className="bg-card rounded-lg border border-line p-6 space-y-4">
          <h2 className="text-lg font-display font-semibold text-ink">Create an organization</h2>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Organization name"
            aria-label="Organization name"
            className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
          />
          <button
            type="submit"
            disabled={creating}
            className="w-full py-2.5 rounded bg-brand text-white text-sm font-semibold hover:bg-brand-deep transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creating ? 'Creating…' : 'Create organization'}
          </button>
        </form>
        <form onSubmit={useInvite} className="bg-card rounded-lg border border-line p-6 space-y-4">
          <h2 className="text-lg font-display font-semibold text-ink">Or join with an invite</h2>
          <input
            value={invite}
            onChange={(e) => setInvite(e.target.value)}
            placeholder="Paste an invite link or code"
            aria-label="Invite link or code"
            className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
          />
          <button
            type="submit"
            className="w-full py-2.5 rounded border border-brand text-brand text-sm font-semibold hover:bg-brand-wash hover:text-brand-deep transition-colors"
          >
            Join organization
          </button>
        </form>
      </div>
    </div>
  )
}
