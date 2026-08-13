import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { api } from './lib/api'
import Layout from './components/Layout'
import Login from './pages/Login'

// Placeholder pages for Tasks 26-29
const Overview = () => <div className="text-gray-600">Overview - Coming soon</div>
const Accounts = () => <div className="text-gray-600">Accounts - Coming soon</div>
const Positions = () => <div className="text-gray-600">Positions - Coming soon</div>
const Logs = () => <div className="text-gray-600">Logs - Coming soon</div>

function ProtectedLayout() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)

  useEffect(() => {
    checkAuth()
  }, [])

  const checkAuth = async () => {
    try {
      const result = await api<{ authenticated: boolean }>('/api/me')
      setIsAuthenticated(result.authenticated)
    } catch (err) {
      // 401 will redirect automatically, but set to false just in case
      setIsAuthenticated(false)
    }
  }

  if (isAuthenticated === null) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <Layout />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedLayout />}>
          <Route index element={<Overview />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="positions" element={<Positions />} />
          <Route path="logs" element={<Logs />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
