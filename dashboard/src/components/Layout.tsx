import { Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useOrg } from '../lib/org'
import { useTheme } from '../hooks/useTheme'
import { saveThemePref, syncThemeFromServer } from '../lib/themeSync'
import { useRequestsBadge } from '../hooks/useRequestsBadge'
import { useUnreadCount } from '../hooks/useUnreadCount'
import type { Account } from '../lib/types'
import Button from './Button'
import Drawer from './Drawer'
import Logo from './Logo'
import Select from './Select'
import SkipLink from './SkipLink'
import Loading from './Loading'
import ChunkBoundary from './ChunkBoundary'
import NavRail from './layout/NavRail'
import BottomBar from './layout/BottomBar'
import DeskStrip from './layout/DeskStrip'
import NotificationBell from './layout/NotificationBell'
import { consumePendingFocus, markNavigated } from '../lib/navigationFocus'
import { adminNav, investorNav } from './layout/nav'
import { platformCaption } from '../lib/platform'

/**
 * The authenticated shell: a floating glass rail from lg up, a glass
 * bottom tab bar plus a menu drawer below it, the desk strip for admins
 * and viewers, and the page in a main region that fades in on route
 * change (a CSS enter animation, .page-enter). Investors are held inside
 * their portal; admins see the open-request count on the Requests link.
 */
export default function Layout() {
  const { theme, toggle: toggleTheme, choose: chooseTheme } = useTheme()
  // The account's theme follows the user between browsers; localStorage
  // stays the first-paint cache. Once per shell mount.
  useEffect(() => { void syncThemeFromServer(chooseTheme) }, [chooseTheme])
  const location = useLocation()
  const navigate = useNavigate()
  const { orgId, role, me } = useOrg()
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
  // Open requests behind the Requests link; undefined for everyone below admin.
  const requestsBadge = useRequestsBadge(orgId, role)
  const groups = investor ? investorNav(orgId) : adminNav(orgId, role, requestsBadge)
  // One poll feeds both bells (desktop rail and phone top bar).
  const unread = useUnreadCount(orgId)
  const bell = (
    <NotificationBell orgId={orgId} count={unread.count} onChange={unread.refresh}
                      pageHref={investor ? `/org/${orgId}/invest/notifications` : `/org/${orgId}/notifications`} />
  )
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

  // After a navigation (not on first load) the new page's heading takes
  // focus when it mounts, so keyboard and screen-reader users land on its
  // name. The heading claims the flag itself rather than Layout guessing a
  // moment. The entering page now mounts in the same commit as the path
  // change, and a child's effects run before its parent's, so the flag is
  // set in a layout effect: every layout effect in a commit runs before any
  // passive one, including the heading's.
  const firstPath = useRef(location.pathname)
  // A flag left by an earlier navigation that no heading claimed (a page
  // without PageHeader, a previous shell) must not steal focus on this
  // shell's first load. Cleared at first render, because the first page's
  // heading mounts -- and runs its effect -- before this component's effects.
  useState(consumePendingFocus)
  useLayoutEffect(() => {
    if (location.pathname === firstPath.current) return
    firstPath.current = ''
    markNavigated()
  }, [location.pathname])

  const railChrome = (withBell: boolean) => (
    <>
      <div className="px-5 pt-5 pb-4 border-b">
        <div className="flex items-center justify-between gap-2">
          <Logo size={26} />
          {withBell && bell}
        </div>
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
        <Button variant="ghost" tone="neutral" size="sm" block onClick={() => { void saveThemePref(toggleTheme()) }}
                aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dim theme'}
                className="justify-start">
          <span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>{' '}
          {theme === 'dark' ? 'Light mode' : 'Dim mode'}
        </Button>
        {!investor && (
          <Button variant="ghost" tone="neutral" size="sm" block to={`/org/${orgId}/settings`}
                  className="justify-start">
            Settings
          </Button>
        )}
        <Button variant="ghost" tone="neutral" size="sm" block onClick={handleLogout} className="justify-start">
          Log out
        </Button>
      </div>
    </>
  )

  return (
    <div className="flex h-screen h-dvh">
      <SkipLink />

      {/* Floating glass rail — desktop only */}
      <aside className="hidden lg:flex w-60 shrink-0 m-4 mr-0 flex-col rounded-card glass shadow-card border">
        {railChrome(true)}
      </aside>

      {/* Full menu — phone and tablet, opened from More or the top bar */}
      <div className="lg:hidden">
        <Drawer open={menuOpen} title="Menu" onClose={() => setMenuOpen(false)}>
          {railChrome(false)}
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
          <div className="ml-auto">{bell}</div>
        </div>
        {!investor && <DeskStrip onAccounts={handleAccounts} />}
        <main id="main" tabIndex={-1} className="flex-1 overflow-y-auto p-4 md:px-6 md:pt-6 pb-24 lg:pb-6 outline-none">
          {strayed ? (
            <Navigate to={portalRoot} replace />
          ) : (
            // Keyed by path so a chunk failure on one page does not stick
            // to the next one the operator opens.
            <ChunkBoundary key={location.pathname}>
              <Suspense fallback={<Loading lines={6} />}>
                <div key={location.pathname} className="page-enter">
                  <Outlet />
                </div>
              </Suspense>
            </ChunkBoundary>
          )}
        </main>
        <BottomBar orgId={orgId} role={role} onMore={() => setMenuOpen(true)} />
      </div>
    </div>
  )
}
