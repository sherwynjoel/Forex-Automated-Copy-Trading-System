import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { api } from './lib/api'
import { LAST_ORG_KEY, OrgProvider } from './lib/org'
import type { Me } from './lib/types'
import Layout from './components/Layout'
import Login from './pages/Login'
import Register from './pages/Register'
import Welcome from './pages/Welcome'
import Join from './pages/Join'
import Members from './pages/Members'
import Overview from './pages/Overview'
import Accounts from './pages/Accounts'
import Positions from './pages/Positions'
import Trade from './pages/Trade'
import Automation from './pages/Automation'
import History from './pages/History'
import Performance from './pages/Performance'
import Logs from './pages/Logs'

/** `/` → the last-used org, else the first org, else /welcome. */
function RootRedirect() {
  const [target, setTarget] = useState<string | null>(null)

  useEffect(() => {
    const resolve = async () => {
      try {
        const me = await api<Me>('/api/me')
        const last = Number(localStorage.getItem(LAST_ORG_KEY))
        const org = me.orgs.find((o) => o.id === last) ?? me.orgs[0]
        setTarget(org ? `/org/${org.id}` : '/welcome')
      } catch {
        setTarget('/login')
      }
    }
    resolve()
  }, [])

  if (!target) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }
  return <Navigate to={target} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/join/:token" element={<Join />} />
        <Route path="/welcome" element={<Welcome />} />
        <Route
          path="/org/:orgId"
          element={
            <OrgProvider>
              <Layout />
            </OrgProvider>
          }
        >
          <Route index element={<Overview />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="positions" element={<Positions />} />
          <Route path="trade" element={<Trade />} />
          <Route path="automation" element={<Automation />} />
          <Route path="history" element={<History />} />
          <Route path="performance" element={<Performance />} />
          <Route path="logs" element={<Logs />} />
          <Route path="members" element={<Members />} />
        </Route>
        <Route path="/" element={<RootRedirect />} />
      </Routes>
    </BrowserRouter>
  )
}
