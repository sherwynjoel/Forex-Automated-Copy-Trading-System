import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'

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
    <AuthCard
      title="Sign in"
      lead="Sign in to open the desk."
      footer={
        <p className="text-center text-sm text-ink-soft">
          New here?{' '}
          <Link to="/register" className="font-medium text-brand hover:text-brand-deep">
            Create an account
          </Link>
        </p>
      }
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        {error && <Banner kind="error">{error}</Banner>}
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
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <Button type="submit" block disabled={isLoading}>
          {isLoading ? 'Signing in...' : 'Sign in'}
        </Button>
      </form>
      {/* There is no self-service password reset yet; until there is, say
          plainly who can help instead of offering a link that goes nowhere. */}
      <p className="mt-5 text-sm text-ink-soft">
        <span className="font-medium text-ink">Forgot password?</span>{' '}
        Ask an admin of your workspace to reset it.
      </p>
    </AuthCard>
  )
}
