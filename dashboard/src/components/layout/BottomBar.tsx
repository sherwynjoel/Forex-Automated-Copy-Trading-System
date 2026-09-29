import { NavLink } from 'react-router-dom'
import type { Role } from '../../lib/roles'
import { bottomBarItems } from './nav'

/**
 * The phone tab bar: glass, fixed to the bottom edge in thumb reach, hidden
 * from lg up where the rail takes over. Every role gets four links plus
 * More, which opens the full menu drawer with every group (the investor
 * portal has nine pages; four fit a thumb).
 */
export default function BottomBar({ orgId, role, onMore }: {
  orgId: number
  role: Role
  onMore: () => void
}) {
  const items = bottomBarItems(orgId, role)
  const cell = 'flex flex-1 flex-col items-center justify-center gap-0.5 min-h-14 text-xs font-semibold transition-colors duration-150'
  return (
    <nav
      aria-label="Quick navigation"
      className="glass fixed inset-x-0 bottom-0 z-30 flex border-t pb-[env(safe-area-inset-bottom)] lg:hidden"
    >
      {items.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.end}
          className={({ isActive }) => `${cell} ${isActive ? 'text-brand' : 'text-ink-soft'}`}
        >
          {item.label}
        </NavLink>
      ))}
      <button type="button" onClick={onMore} className={`${cell} text-ink-soft`}>
        More
      </button>
    </nav>
  )
}
