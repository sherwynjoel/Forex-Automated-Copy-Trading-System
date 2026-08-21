/**
 * Post-action refresh burst. A broker action (order, close, cancel) lands in
 * the copier's book after its debounced resync (~0.2s + a broker round
 * trip), so a single delayed refetch either races it or waits too long.
 * A short burst catches the change as soon as it exists without hammering.
 */
export const ACTION_BURST_MS = [350, 1000, 2000, 3500]

export function actionBurst(refetch: () => void): void {
  for (const ms of ACTION_BURST_MS) {
    setTimeout(refetch, ms)
  }
}
