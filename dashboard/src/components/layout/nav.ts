import { can, type Role } from '../../lib/roles'

export interface NavItem {
  path: string
  label: string
  /** Match only the exact path (the org root), not every child. */
  end?: boolean
  /** Open items waiting behind the link (the admin Requests desk); the rail
   *  shows it as a neutral pill and folds it into the link's name. */
  badge?: number
}

export interface NavGroup {
  name: string
  items: NavItem[]
}

/** The admin/viewer rail: three groups, eleven links at most.
 *  `requestsBadge` is the open-request total from useRequestsBadge. */
export function adminNav(orgId: number, role: Role, requestsBadge?: number): NavGroup[] {
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
        ...(can(role, 'control') ? [
          { path: `${o}/investors`, label: 'Investors' },
          { path: `${o}/requests`, label: 'Requests', badge: requestsBadge },
        ] : []),
        { path: `${o}/logs`, label: 'Logs' },
      ],
    },
  ]
}

/** The investor portal: the dashboard, their money, their account and identity. */
export function investorNav(orgId: number): NavGroup[] {
  const p = `/org/${orgId}/invest`
  return [
    {
      name: '',
      items: [{ path: p, label: 'Dashboard', end: true }],
    },
    {
      name: 'Money',
      items: [
        { path: `${p}/wallet`, label: 'Wallet' },
        { path: `${p}/deposit`, label: 'Deposit' },
        { path: `${p}/withdraw`, label: 'Withdraw' },
        { path: `${p}/transfer`, label: 'Transfer' },
        { path: `${p}/transactions`, label: 'Transactions' },
        { path: `${p}/payout-accounts`, label: 'Payout accounts' },
      ],
    },
    {
      name: 'Account',
      items: [
        { path: `${p}/account`, label: 'Trading account' },
        { path: `${p}/profile`, label: 'Profile & verification' },
        { path: `${p}/open-account`, label: 'Open account' },
        { path: `${p}/security`, label: 'Security' },
        { path: `${p}/history`, label: 'History' },
        { path: `${p}/notifications`, label: 'Notifications' },
      ],
    },
  ]
}

/** The phone tab bar: four links in thumb reach for every role; More opens
 *  the full menu with every group. */
export function bottomBarItems(orgId: number, role: Role): NavItem[] {
  if (role === 'investor') {
    const p = `/org/${orgId}/invest`
    return [
      { path: p, label: 'Dashboard', end: true },
      { path: `${p}/deposit`, label: 'Deposit' },
      { path: `${p}/withdraw`, label: 'Withdraw' },
      { path: `${p}/transactions`, label: 'Transactions' },
    ]
  }
  const o = `/org/${orgId}`
  return [
    { path: o, label: 'Overview', end: true },
    { path: `${o}/positions`, label: 'Positions' },
    can(role, 'trade') ? { path: `${o}/trade`, label: 'Trade' } : { path: `${o}/history`, label: 'History' },
    { path: `${o}/accounts`, label: 'Accounts' },
  ]
}
