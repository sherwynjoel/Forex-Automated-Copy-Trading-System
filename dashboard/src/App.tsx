import { lazy, Suspense, useEffect, useState } from 'react'
import type { ComponentType } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { api } from './lib/api'
import { LAST_ORG_KEY, OrgProvider } from './lib/org'
import { isMpinPending } from './lib/types'
import type { Me, MpinPending } from './lib/types'
import Layout from './components/Layout'
import Loading from './components/Loading'
import ChunkBoundary, { markChunkError } from './components/ChunkBoundary'
import Landing from './pages/Landing'
import NotFound from './pages/NotFound'

// Three lazy groups: the desk, the investor portal, the auth screens. A
// signed-in investor never downloads the desk; a visitor never downloads
// either. Landing stays eager because `/` renders it for signed-out visitors.
const admin = () => import('./pages/groups/admin')
const investor = () => import('./pages/groups/investor')
const auth = () => import('./pages/groups/auth')
// A failed group import is marked, so ChunkBoundary offers a reload for it
// whatever the browser calls the failure.
const pick = <M, K extends keyof M>(load: () => Promise<M>, key: K) =>
  lazy(() => load().then(
    (m) => ({ default: m[key] as ComponentType }),
    (err: unknown) => { throw markChunkError(err) },
  ))

const Login = pick(auth, 'Login')
const Mpin = pick(auth, 'Mpin')
const Register = pick(auth, 'Register')
const Join = pick(auth, 'Join')
const Welcome = pick(auth, 'Welcome')
const Overview = pick(admin, 'Overview')
const Accounts = pick(admin, 'Accounts')
const Positions = pick(admin, 'Positions')
const Trade = pick(admin, 'Trade')
const Automation = pick(admin, 'Automation')
const History = pick(admin, 'History')
const Performance = pick(admin, 'Performance')
const Logs = pick(admin, 'Logs')
const Members = pick(admin, 'Members')
const Investors = pick(admin, 'Investors')
const Requests = pick(admin, 'Requests')
const InvestorDashboard = pick(investor, 'InvestorDashboard')
const InvestorDeposit = pick(investor, 'InvestorDeposit')
const InvestorWithdraw = pick(investor, 'InvestorWithdraw')
const InvestorPayoutAccounts = pick(investor, 'InvestorPayoutAccounts')
const InvestorTransfer = pick(investor, 'InvestorTransfer')
const InvestorWallet = pick(investor, 'InvestorWallet')
const InvestorTransactions = pick(investor, 'InvestorTransactions')
const InvestorHistory = pick(investor, 'InvestorHistory')
const InvestorAccount = pick(investor, 'InvestorAccount')
const InvestorSecurity = pick(investor, 'InvestorSecurity')
const InvestorProfile = pick(investor, 'InvestorProfile')
const InvestorOpenAccount = pick(investor, 'InvestorOpenAccount')
const Notifications = pick(admin, 'Notifications')
const InvestorNotifications = pick(investor, 'Notifications')

/** `/` → the last-used org, else the first org, else /welcome; signed-out
 *  visitors get the public front page instead of the login screen. */
function RootRedirect() {
  const [target, setTarget] = useState<string | null>(null)

  useEffect(() => {
    const resolve = async () => {
      try {
        const me = await api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
        if (isMpinPending(me)) { setTarget('/mpin'); return }
        const last = Number(localStorage.getItem(LAST_ORG_KEY))
        const org = me.orgs.find((o) => o.id === last) ?? me.orgs[0]
        setTarget(org ? `/org/${org.id}` : '/welcome')
      } catch {
        setTarget('landing')
      }
    }
    resolve()
  }, [])

  if (!target) {
    return <div className="mx-auto max-w-md pt-24"><Loading lines={4} /></div>
  }
  if (target === 'landing') return <Landing />
  return <Navigate to={target} replace />
}

const fullPage = <div className="mx-auto max-w-md pt-24"><Loading lines={4} /></div>

export default function App() {
  return (
    <BrowserRouter>
      <ChunkBoundary>
        <Suspense fallback={fullPage}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/mpin" element={<Mpin />} />
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
              <Route path="investors" element={<Investors />} />
              <Route path="requests" element={<Requests />} />
              <Route path="notifications" element={<Notifications />} />
              <Route path="invest" element={<InvestorDashboard />} />
              <Route path="invest/deposit" element={<InvestorDeposit />} />
              <Route path="invest/withdraw" element={<InvestorWithdraw />} />
              <Route path="invest/payout-accounts" element={<InvestorPayoutAccounts />} />
              <Route path="invest/transfer" element={<InvestorTransfer />} />
              <Route path="invest/wallet" element={<InvestorWallet />} />
              <Route path="invest/transactions" element={<InvestorTransactions />} />
              <Route path="invest/history" element={<InvestorHistory />} />
              <Route path="invest/account" element={<InvestorAccount />} />
              <Route path="invest/security" element={<InvestorSecurity />} />
              <Route path="invest/profile" element={<InvestorProfile />} />
              <Route path="invest/open-account" element={<InvestorOpenAccount />} />
              <Route path="invest/notifications" element={<InvestorNotifications />} />
              <Route path="*" element={<NotFound />} />
            </Route>
            <Route path="/" element={<RootRedirect />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </ChunkBoundary>
    </BrowserRouter>
  )
}
