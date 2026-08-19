import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import Banner from '../components/Banner'
import Logo from '../components/Logo'

export default function Login() {
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
      await api('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      navigate('/')
    } catch (err) {
      setError(errorText(err, 'Login failed'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper py-12 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="flex justify-center"><Logo size={38} textClass="text-3xl" /></h1>
          <p className="mt-2 text-sm text-ink-soft">Sign in to open the desk</p>
        </div>
        <form
          className="bg-card rounded-lg border border-line p-8 space-y-5"
          onSubmit={handleSubmit}
        >
          {error && <Banner kind="error">{error}</Banner>}
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
              autoComplete="current-password"
              required
              className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-2.5 rounded bg-brand text-white text-sm font-semibold hover:bg-brand-deep transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? 'Signing in...' : 'Sign in'}
          </button>
          <p className="text-center text-sm text-ink-soft">
            New here?{' '}
            <Link to="/register" className="font-medium text-brand hover:text-brand-deep">
              Create an account
            </Link>
          </p>
        </form>
      </div>
    </div>
  )
}
