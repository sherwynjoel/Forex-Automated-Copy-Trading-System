import { orgApi } from './api'
import type { PortalNotification } from './types'

/** Marks one notification read and answers the server's row; a row that is
 *  read already is answered as it is, with no request. The bell and the
 *  Notifications page both go through here. Throws the API error. */
export async function markRead(orgId: number, n: PortalNotification): Promise<PortalNotification> {
  if (n.read_at != null) return n
  return orgApi<PortalNotification>(orgId, `notifications/${n.id}/read`, { method: 'POST' })
}

/** Marks every notification of the caller in this org read. */
export async function markAllRead(orgId: number): Promise<void> {
  await orgApi(orgId, 'notifications/read-all', { method: 'POST' })
}

/** A list after read-all: each row keeps its first read_at, as the server does. */
export function withAllRead(list: PortalNotification[],
                            now: string = new Date().toISOString()): PortalNotification[] {
  return list.map((n) => ({ ...n, read_at: n.read_at ?? now }))
}
