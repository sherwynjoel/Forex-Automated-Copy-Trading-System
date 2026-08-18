export type Role = 'viewer' | 'trader' | 'admin' | 'owner'
export type Action = 'trade' | 'control' | 'manage_members'

const RANK: Record<Role, number> = { viewer: 0, trader: 1, admin: 2, owner: 3 }
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
