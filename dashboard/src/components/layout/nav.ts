import { can, type Role } from '../../lib/roles'

export interface NavItem {
  path: string
  label: string
  /** Match only the exact path (the org root), not every child. */
  end?: boolean
}

export interface NavGroup {
  name: string
  items: NavItem[]
}

/** The admin/viewer rail: three groups, ten links at most. */
export function adminNav(orgId: number, role: Role): NavGroup[] {
  const o = `/org/${orgId}`
  const trade = can(role, 'trade')
  return [
    {
      name: 'Desk',
      items: [
        { path: o, label: 'Overview', end: true },
        { path: `${o}/positions`, label: 'Positions' },
        ...(trade ? [{ path: `${o}/trade`, label: 'Trade' }] : []),
        { path: `${o}/history`, label: 'History' },
      ],
    },
    {
      name: 'Fleet',
      items: [
        { path: `${o}/accounts`, label: 'Accounts' },
        ...(trade ? [{ path: `${o}/automation`, label: 'Automation' }] : []),
        { path: `${o}/performance`, label: 'Performance' },
      ],
    },
    {
      name: 'Org',
      items: [
        { path: `${o}/members`, label: 'Members' },
        ...(can(role, 'control') ? [{ path: `${o}/investors`, label: 'Investors' }] : []),
        { path: `${o}/logs`, label: 'Logs' },
      ],
    },
  ]
}

/** The investor portal: only their own account and their own money. */
export function investorNav(orgId: number): NavGroup[] {
  const p = `/org/${orgId}/invest`
  return [{
    name: '',
    items: [
      { path: p, label: 'Overview', end: true },
      { path: `${p}/deposit`, label: 'Deposit' },
      { path: `${p}/withdraw`, label: 'Withdraw' },
      { path: `${p}/history`, label: 'History' },
      { path: `${p}/account`, label: 'Account' },
    ],
  }]
}

/** The phone tab bar: four links (five for investors) in thumb reach. */
export function bottomBarItems(orgId: number, role: Role): NavItem[] {
  if (role === 'investor') return investorNav(orgId)[0].items
  const o = `/org/${orgId}`
  return [
    { path: o, label: 'Overview', end: true },
    { path: `${o}/positions`, label: 'Positions' },
    can(role, 'trade') ? { path: `${o}/trade`, label: 'Trade' } : { path: `${o}/history`, label: 'History' },
    { path: `${o}/accounts`, label: 'Accounts' },
  ]
}
