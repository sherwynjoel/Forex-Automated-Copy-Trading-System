import { NavLink } from 'react-router-dom'
import type { NavGroup } from './nav'

/**
 * Grouped navigation for the rail and the phone drawer. NavLink sets
 * aria-current="page" on the active link; `end` keeps the org root from
 * matching every child route.
 */
export default function NavRail({ groups, onNavigate, dense = true }: {
  groups: NavGroup[]
  onNavigate?: () => void
  dense?: boolean
}) {
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 py-2">
      {groups.map((g, gi) => (
        <div key={g.name || gi} className={gi > 0 ? 'mt-4' : ''}>
          {g.name && <div className="px-3 pb-1 text-xs font-semibold text-ink-faint">{g.name}</div>}
          <ul className="space-y-0.5">
            {g.items.map((item) => (
              <li key={item.path}>
                <NavLink
                  to={item.path}
                  end={item.end}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    `block rounded-control px-3 text-sm transition-colors duration-150 min-h-11 md:min-h-0 ${
                      dense ? 'py-2' : 'py-3'
                    } ${isActive ? 'bg-brand-wash text-brand-deep font-semibold' : 'text-ink-soft hover:bg-brand-wash/60 hover:text-ink'}`
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}
