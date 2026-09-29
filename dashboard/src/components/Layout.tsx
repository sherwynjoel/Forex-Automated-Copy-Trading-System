import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { api } from '../lib/api'
import { useOrg } from '../lib/org'
import { useTheme } from '../hooks/useTheme'
import type { Account } from '../lib/types'
import Button from './Button'
import Drawer from './Drawer'
import Logo from './Logo'
import Select from './Select'
import SkipLink from './SkipLink'
import Loading from './Loading'
import NavRail from './layout/NavRail'
import BottomBar from './layout/BottomBar'
import DeskStrip from './layout/DeskStrip'
import { adminNav, investorNav } from './layout/nav'
import { platformCaption } from '../lib/platform'

const EASE = [0.23, 1, 0.32, 1] as const

/**
 * The authenticated shell: a floating glass rail from lg up, a glass
 * bottom tab bar plus a menu drawer below it, the desk strip for admins
 * and viewers, and the page in a main region that crossfades on route
 * change. Investors are held inside their portal.
 */
export default function Layout() {
  const { theme, toggle: toggleTheme } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const { orgId, role, me } = useOrg()
  const reduced = useReducedMotion()
  const [menuOpen, setMenuOpen] = useState(false)
  // "cTrader", "MT5" or "cTrader · MT5" -- null until the first fetch, so
  // the rail never claims a platform list it has not seen.
  const [caption, setCaption] = useState<string | null>(null)
  const handleAccounts = useCallback((list: Account[]) => setCaption(platformCaption(list)), [])

  const handleLogout = async () => {
    try {
      await api('/api/logout', { method: 'POST' })
    } catch (err) {
      console.error('Logout failed:', err)
    }
    navigate('/login')
  }

  const investor = role === 'investor'
  const groups = investor ? investorNav(orgId) : adminNav(orgId, role)
  const portalRoot = `/org/${orgId}/invest`
  const strayed = investor
    && location.pathname !== portalRoot
    && !location.pathname.startsWith(portalRoot + '/')

  // The drawer never outlives a navigation. Escape is Drawer's job.
  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  // Nor does it outlive the viewport: past lg the drawer is display:none,
  // but its focus trap has no way to know that on its own.
  useEffect(() => {
    if (!menuOpen) return
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(min-width: 1024px)')
    const onChange = (e: MediaQueryListEvent) => { if (e.matches) setMenuOpen(false) }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [menuOpen])

  // After a navigation (not on first load) focus the page heading so
  // keyboard and screen-reader users land on the new page's name.
  const firstPath = useRef(location.pathname)
  useEffect(() => {
    if (location.pathname === firstPath.current) return
    firstPath.current = ''
    const id = window.setTimeout(() => document.getElementById('page-title')?.focus(), 0)
    return () => window.clearTimeout(id)
  }, [location.pathname])

  const railChrome = (
    <>
      <div className="px-5 pt-5 pb-4 border-b">
        <Logo size={26} />
        <Select
          aria-label="Organization"
          value={orgId}
          onChange={(e) => navigate(`/org/${e.target.value}`)}
          block
          className="mt-3"
        >
          {me.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </Select>
        {caption && <p className="mt-1 text-xs text-ink-faint">{caption}</p>}
      </div>
      <NavRail groups={groups} onNavigate={() => setMenuOpen(false)} />
      <div className="border-t p-3">
        <Button variant="ghost" tone="neutral" size="sm" block onClick={toggleTheme}
                aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dim theme'}
                className="justify-start">
          <span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>{' '}
          {theme === 'dark' ? 'Light mode' : 'Dim mode'}
        </Button>
        <Button variant="ghost" tone="neutral" size="sm" block onClick={handleLogout} className="justify-start">
          Log out
        </Button>
      </div>
    </>
  )

  return (
    <div className="flex h-screen">
      <SkipLink />

      {/* Floating glass rail — desktop only */}
      <aside className="hidden lg:flex w-60 shrink-0 m-4 mr-0 flex-col rounded-card glass shadow-card border">
        {railChrome}
      </aside>

      {/* Full menu — phone and tablet, opened from More or the top bar */}
      <div className="lg:hidden">
        <Drawer open={menuOpen} title="Menu" onClose={() => setMenuOpen(false)}>
          {railChrome}
        </Drawer>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <div className="lg:hidden h-12 shrink-0 glass border-b flex items-center gap-2 px-2">
          <Button variant="ghost" tone="neutral" size="sm" aria-label="Open menu"
                  onClick={() => setMenuOpen(true)} className="h-11 w-11 md:h-11 md:w-11 justify-center">
            <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5">
              <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </Button>
          <Logo size={22} textClass="text-base" />
        </div>
        {!investor && <DeskStrip onAccounts={handleAccounts} />}
        <main id="main" tabIndex={-1} className="flex-1 overflow-y-auto p-4 md:p-6 pb-24 lg:pb-6 outline-none">
          {strayed ? (
            <Navigate to={portalRoot} replace />
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={location.pathname}
                initial={reduced ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
                transition={reduced ? { duration: 0 } : { duration: 0.18, ease: EASE }}
              >
                <Suspense fallback={<Loading lines={6} />}>
                  <Outlet />
                </Suspense>
              </motion.div>
            </AnimatePresence>
          )}
        </main>
        <BottomBar orgId={orgId} role={role} onMore={() => setMenuOpen(true)} />
      </div>
    </div>
  )
}
