import type { ApiState, StateSnapshot } from './types'

// The shape of a category='quotes' WebSocket message payload: the copier's
// in-memory live feed, relayed by the api ~2x/second while anyone watches.

export interface TickPosition {
  position_id: number
  symbol?: string | null
  current_price?: number | null
  pnl_quote?: number | null
}

export interface TickAccount {
  equity?: number | null
  open_pnl?: number
  positions?: TickPosition[]
}

export interface TicksPayload {
  quotes?: Record<string, { bid: number | null; ask: number | null }>
  accounts?: Record<string, TickAccount>
}

/**
 * Fold a quotes tick into an accounts snapshot: update equity, open P&L
 * and the per-position marks (matched by position id) in place, and touch
 * nothing else. Accounts or positions the snapshot does not know yet are
 * ignored -- the next full refresh brings their rows; a tick must never
 * invent structure. Returns the SAME reference when nothing matched so
 * React can skip the re-render.
 */
export function mergeTicksIntoSnapshot(
  snapshot: StateSnapshot,
  ticks: Record<string, TickAccount>,
): StateSnapshot {
  let changed = false
  const next: StateSnapshot = { ...snapshot }
  for (const [accountId, tick] of Object.entries(ticks)) {
    const prev = snapshot[accountId]
    if (!prev) continue
    const marks = new Map((tick.positions ?? []).map((p) => [p.position_id, p]))
    let accountChanged = false
    const equity = tick.equity !== undefined ? tick.equity : prev.equity
    const openPnl = tick.open_pnl !== undefined ? tick.open_pnl : prev.open_pnl
    if (equity !== prev.equity || openPnl !== prev.open_pnl) accountChanged = true
    const positions = (prev.positions ?? []).map((p) => {
      const m = marks.get(p.position_id)
      if (!m) return p
      // Honest values, null included: a tick saying "no quote" must show
      // as a dash, not freeze the last number as if it were still live.
      const price = m.current_price === undefined ? (p.current_price ?? null) : m.current_price
      const pnl = m.pnl_quote === undefined ? (p.pnl_quote ?? null) : m.pnl_quote
      if (price === (p.current_price ?? null) && pnl === (p.pnl_quote ?? null)) return p
      accountChanged = true
      return { ...p, current_price: price, pnl_quote: pnl }
    })
    if (!accountChanged) continue
    next[accountId] = { ...prev, equity, open_pnl: openPnl, positions }
    changed = true
  }
  return changed ? next : snapshot
}

/**
 * Same fold for a full ApiState: the accounts block via the snapshot
 * merge, plus the master-position rows patched from whichever account's
 * book carries the position (the master's own).
 */
export function mergeTicksIntoApiState(
  state: ApiState,
  ticks: Record<string, TickAccount>,
): ApiState {
  const marks = new Map<number, TickPosition>()
  for (const tick of Object.values(ticks)) {
    for (const p of tick.positions ?? []) marks.set(p.position_id, p)
  }
  const base = state.accounts ?? {}
  const accounts = mergeTicksIntoSnapshot(base, ticks)
  let masterChanged = false
  const master_positions = (state.master_positions ?? []).map((mp) => {
    const m = marks.get(mp.position_id)
    if (!m) return mp
    const price = m.current_price === undefined ? (mp.current_price ?? null) : m.current_price
    const pnl = m.pnl_quote === undefined ? (mp.pnl_quote ?? null) : m.pnl_quote
    if (price === (mp.current_price ?? null) && pnl === (mp.pnl_quote ?? null)) return mp
    masterChanged = true
    return { ...mp, current_price: price, pnl_quote: pnl }
  })
  // Same-reference bail so a no-op tick never re-renders the page.
  if (accounts === base && !masterChanged) return state
  return {
    ...state,
    accounts,
    master_positions: masterChanged ? master_positions : (state.master_positions ?? []),
  }
}
