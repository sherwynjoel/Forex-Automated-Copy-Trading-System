import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'

export default function Join() {
  const { token } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const join = async () => {
      try {
        const result = await api<{ org_id: number }>('/api/orgs/join', {
          method: 'POST',
          body: JSON.stringify({ token }),
        })
        if (!cancelled) navigate(`/org/${result.org_id}`, { replace: true })
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : ''
        setError(
          message.includes('410')
            ? 'This invite is invalid, expired, or already used.'
            : message.includes('409')
              ? 'You are already a member of this organization.'
              : 'Could not join the organization.')
      }
    }
    join()
    return () => { cancelled = true }
  }, [token, navigate])

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper px-4">
      <div className="max-w-md w-full text-center space-y-4">
        <h2 className="font-display text-3xl text-brand">Copy Desk</h2>
        {error ? (
          <p className="text-sm font-medium text-loss-deep">{error}</p>
        ) : (
          <p className="text-sm text-ink-soft">Joining organization…</p>
        )}
      </div>
    </div>
  )
}
