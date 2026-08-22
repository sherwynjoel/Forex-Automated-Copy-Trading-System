import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import Banner from '../components/Banner'
import Logo from '../components/Logo'

export default function Register() {
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      await api('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, display_name: displayName }),
      })
      navigate('/welcome')
    } catch (err) {
      setError(errorText(err, 'Registration failed'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper py-12 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="flex justify-center"><Logo size={38} textClass="text-3xl" /></h1>
          <p className="mt-2 text-sm text-ink-soft">Create an account to open the desk</p>
        </div>
        <form
          className="bg-card rounded-lg border border-line p-8 space-y-5"
          onSubmit={handleSubmit}
        >
          {error && <Banner kind="error">{error}</Banner>}
          <div>
            <label htmlFor="displayName" className="desk-label block mb-1">Display name</label>
            <input
              id="displayName"
              name="displayName"
              type="text"
              autoComplete="name"
              required
              className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="email" className="desk-label block mb-1">Email</label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password" className="desk-label block mb-1">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={10}
              className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="mt-1 text-xs text-ink-soft">At least 10 characters.</p>
          </div>
          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-2.5 rounded bg-brand text-on-accent text-sm font-semibold hover:bg-brand-deep transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? 'Creating account...' : 'Create account'}
          </button>
          <p className="text-center text-sm text-ink-soft">
            Already have an account?{' '}
            <Link to="/login" className="font-medium text-brand hover:text-brand-deep">
              Sign in
            </Link>
          </p>
        </form>
      </div>
    </div>
  )
}
