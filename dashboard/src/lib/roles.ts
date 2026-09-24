export type Role = 'investor' | 'viewer' | 'trader' | 'admin' | 'owner'
export type Action = 'trade' | 'control' | 'manage_members'

// investor sits below viewer: it may open only the investor portal.
const RANK: Record<Role, number> = { investor: -1, viewer: 0, trader: 1, admin: 2, owner: 3 }
const THRESHOLD: Record<Action, number> = {
  trade: RANK.trader,
  control: RANK.admin,
  manage_members: RANK.owner,
}

/** UI-side mirror of the server's role matrix — hides controls the server
 * would reject. The server enforces regardless. */
export function can(role: Role | null | undefined, action: Action): boolean {
  if (!role || !(role in RANK)) return false
  return RANK[role] >= THRESHOLD[action]
}

/** What the UI calls each role. One person runs the desk, so the owner reads
 *  as "Admin". The deputy roles are no longer offered but keep a name for
 *  anyone who already holds one. */
const ROLE_LABEL: Record<Role, string> = {
  owner: 'Admin',
  admin: 'Admin (deputy)',
  trader: 'Trader',
  viewer: 'Viewer',
  investor: 'Investor',
}

export function roleLabel(role: string): string {
  return (ROLE_LABEL as Record<string, string>)[role] ?? role
}

/** The only roles an admin hands out: read-only staff and investors. */
export const OFFERED_ROLES: Role[] = ['investor', 'viewer']
