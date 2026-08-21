import type { EquityCurvePoint } from './types'

export interface SeriesPoint {
  t: number
  v: number
}

/** Cumulative net P&L relative to the first point of the window. */
export function cumulativeSeries(curve: EquityCurvePoint[]): SeriesPoint[] {
  if (!curve.length) return []
  const base = curve[0].balance
  return curve.map((p) => ({ t: p.timestamp, v: p.balance - base }))
}

/** Distance below the running balance peak (zero or negative) per point. */
export function drawdownSeries(curve: EquityCurvePoint[]): SeriesPoint[] {
  let peak = -Infinity
  return curve.map((p) => {
    peak = Math.max(peak, p.balance)
    return { t: p.timestamp, v: p.balance - peak }
  })
}

/**
 * One bar per local calendar day that closed at least one trade: that day's
 * net P&L. A day's baseline is the last balance before it — for the opening
 * day that is the first curve point, whose own trade P&L is unknowable from
 * balance-after alone (mirrors the activity-window logic).
 */
export function dailyPnl(curve: EquityCurvePoint[]): SeriesPoint[] {
  if (!curve.length) return []
  const dayOf = (ts: number) => {
    const d = new Date(ts)
    return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  }
  const bars: SeriesPoint[] = []
  let baseline = curve[0].balance
  let day = dayOf(curve[0].timestamp)
  let closing = curve[0].balance
  for (const p of curve.slice(1)) {
    const d = dayOf(p.timestamp)
    if (d !== day) {
      bars.push({ t: day, v: closing - baseline })
      baseline = closing
      day = d
    }
    closing = p.balance
  }
  bars.push({ t: day, v: closing - baseline })
  return bars
}
