export type Role = 'investor' | 'viewer' | 'admin'
export type Action = 'trade' | 'control' | 'manage_members'

// investor sits below viewer: it may open only the investor portal.
const RANK: Record<Role, number> = { investor: -1, viewer: 0, admin: 1 }

// One person runs the desk. Everything that used to need Trader, Admin or
// Owner needs the single Admin role; the action names stay so each call
// site still reads as what it gates.
const THRESHOLD: Record<Action, number> = {
  trade: RANK.admin,
  control: RANK.admin,
  manage_members: RANK.admin,
}

/** UI-side mirror of the server's role matrix — hides controls the server
 * would reject. The server enforces regardless. */
export function can(role: Role | null | undefined, action: Action): boolean {
  if (!role || !(role in RANK)) return false
  return RANK[role] >= THRESHOLD[action]
}

const ROLE_LABEL: Record<Role, string> = {
  admin: 'Admin',
  viewer: 'Viewer',
  investor: 'Investor',
}

export function roleLabel(role: string): string {
  return (ROLE_LABEL as Record<string, string>)[role] ?? role
}

/** Every role an admin can hand out, in the order the pickers show them. */
export const OFFERED_ROLES: Role[] = ['admin', 'viewer', 'investor']
