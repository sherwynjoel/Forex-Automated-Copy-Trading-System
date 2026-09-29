import { NavLink } from 'react-router-dom'
import Badge from '../Badge'
import type { NavGroup } from './nav'

/**
 * Grouped navigation for the rail and the phone drawer. NavLink sets
 * aria-current="page" on the active link; `end` keeps the org root from
 * matching every child route. An item with a badge (open requests) shows
 * it as a neutral pill after the label and folds the count into the
 * link's accessible name ("Requests, 3 open").
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
            {g.items.map((item) => {
              const badge = item.badge && item.badge > 0 ? item.badge : null
              return (
                <li key={item.path}>
                  <NavLink
                    to={item.path}
                    end={item.end}
                    onClick={onNavigate}
                    aria-label={badge ? `${item.label}, ${badge} open` : undefined}
                    className={({ isActive }) =>
                      `flex items-center justify-between gap-2 rounded-control px-3 text-sm transition-colors duration-150 min-h-11 md:min-h-0 ${
                        dense ? 'py-2' : 'py-3'
                      } ${isActive ? 'bg-brand-wash text-brand-deep font-semibold' : 'text-ink-soft hover:bg-brand-wash/60 hover:text-ink'}`
                    }
                  >
                    <span>{item.label}</span>
                    {badge && <Badge tone="neutral">{badge}</Badge>}
                  </NavLink>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}
