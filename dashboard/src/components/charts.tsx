import { ReactNode, useEffect, useRef, useState } from 'react'
import { signed as signedFmt } from '../lib/format'

/**
 * Hand-rolled SVG charts in the MirrorFleet design system.
 *
 * Rules baked in (see the dataviz method): one axis per chart, recessive
 * hairline grid, thin marks with the data end rounded, sign never encoded by
 * color alone (bars diverge around a drawn zero baseline; up-candles are
 * hollow and down-candles filled; values carry +/- prefixes), text in ink
 * tokens with tabular-mono numerals, and a hover layer on every plot.
 */

const INK_SOFT = 'var(--color-ink-soft)'
const INK_FAINT = 'var(--color-ink-faint)'
const LINE = 'var(--color-line)'
const LINE_STRONG = 'var(--color-line-strong)'
const BRAND = 'var(--color-brand)'
const BRAND_WASH = 'var(--color-brand-wash)'
const PROFIT = 'var(--color-profit)'
const LOSS = 'var(--color-loss)'

const MONO = '"IBM Plex Mono", Menlo, monospace'

export function useElementWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(600)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setWidth(el.clientWidth || 600)
    update()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

interface TooltipState {
  x: number
  y: number
  content: ReactNode
}

function Tooltip({ tip }: { tip: TooltipState | null }) {
  if (!tip) return null
  return (
    <div
      className="pointer-events-none absolute z-10 rounded border border-line bg-card px-2.5 py-1.5 text-xs shadow-md"
      style={{ left: tip.x + 12, top: tip.y - 8 }}
    >
      {tip.content}
    </div>
  )
}



function shortDate(ms: number): string {
  return new Date(ms).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

/** Rounded-end bar anchored to the baseline: only the data end is rounded. */
function roundedBarPath(x: number, w: number, y0: number, y1: number, r: number): string {
  // y0 = baseline, y1 = data end; works for bars going up (y1 < y0) or down.
  const up = y1 < y0
  const radius = Math.min(r, w / 2, Math.abs(y0 - y1))
  if (radius <= 0) return `M${x},${y0} h${w} V${y1} h${-w} Z`
  if (up) {
    return `M${x},${y0} V${y1 + radius} Q${x},${y1} ${x + radius},${y1} ` +
      `H${x + w - radius} Q${x + w},${y1} ${x + w},${y1 + radius} V${y0} Z`
  }
  return `M${x},${y0} V${y1 - radius} Q${x},${y1} ${x + radius},${y1} ` +
    `H${x + w - radius} Q${x + w},${y1} ${x + w},${y1 - radius} V${y0} Z`
}

// ---------------------------------------------------------------------------
// Equity curve
// ---------------------------------------------------------------------------

export interface EquityPoint {
  timestamp: number
  balance: number
}

export function EquityCurve({ points, height = 220 }: {
  points: EquityPoint[]
  height?: number
}) {
  const [ref, width] = useElementWidth()
  const [tip, setTip] = useState<TooltipState | null>(null)

  const pad = { left: 8, right: 64, top: 12, bottom: 22 }
  const plotW = Math.max(width - pad.left - pad.right, 10)
  const plotH = height - pad.top - pad.bottom

  const min = Math.min(...points.map((p) => p.balance))
  const max = Math.max(...points.map((p) => p.balance))
  const span = max - min || 1
  const yOf = (v: number) => pad.top + plotH - ((v - min) / span) * plotH
  const t0 = points[0]?.timestamp ?? 0
  const t1 = points[points.length - 1]?.timestamp ?? 1
  const xOf = (t: number) =>
    pad.left + (t1 === t0 ? plotW / 2 : ((t - t0) / (t1 - t0)) * plotW)

  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xOf(p.timestamp)},${yOf(p.balance)}`).join(' ')
  const area = points.length > 1
    ? `${path} L${xOf(t1)},${pad.top + plotH} L${xOf(t0)},${pad.top + plotH} Z`
    : ''

  const gridValues = [min, min + span / 2, max]

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const mx = e.clientX - rect.left
    let nearest = points[0]
    let best = Infinity
    for (const p of points) {
      const d = Math.abs(xOf(p.timestamp) - mx)
      if (d < best) { best = d; nearest = p }
    }
    if (!nearest) return
    setTip({
      x: xOf(nearest.timestamp), y: yOf(nearest.balance),
      content: (
        <div>
          <div className="text-ink-soft">{shortDate(nearest.timestamp)}</div>
          <div className="num text-ink">{nearest.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}</div>
        </div>
      ),
    })
  }

  return (
    <div ref={ref} className="relative" data-chart="equity-curve">
      <svg width={width} height={height} role="img" aria-label="Equity curve">
        {gridValues.map((v) => (
          <g key={v}>
            <line x1={pad.left} x2={pad.left + plotW} y1={yOf(v)} y2={yOf(v)}
                  stroke={LINE} strokeWidth={1} />
            <text x={pad.left + plotW + 6} y={yOf(v) + 3} fontSize={10}
                  fontFamily={MONO} fill={INK_FAINT}>
              {Math.round(v).toLocaleString('en-US')}
            </text>
          </g>
        ))}
        {points.length > 1 && <path d={area} fill={BRAND_WASH} />}
        <path d={path} fill="none" stroke={BRAND} strokeWidth={2}
              strokeLinejoin="round" strokeLinecap="round" />
        {tip && (
          <circle cx={tip.x} cy={tip.y} r={4} fill={BRAND} stroke="var(--color-paper)" strokeWidth={2} />
        )}
        {points.length > 0 && (
          <>
            <text x={pad.left} y={height - 6} fontSize={10} fontFamily={MONO} fill={INK_FAINT}>
              {shortDate(t0)}
            </text>
            <text x={pad.left + plotW} y={height - 6} fontSize={10} fontFamily={MONO}
                  fill={INK_FAINT} textAnchor="end">
              {shortDate(t1)}
            </text>
          </>
        )}
        <rect x={pad.left} y={pad.top} width={plotW} height={plotH} fill="transparent"
              onMouseMove={onMove} onMouseLeave={() => setTip(null)} />
      </svg>
      <Tooltip tip={tip} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Weekly P&L bars (polarity: sign is position around the zero baseline;
// color only reinforces it)
// ---------------------------------------------------------------------------

export interface PnlBucket {
  week_start: number
  gross_pnl: number
  trades: number
}

export function PnlBars({ buckets, height = 180 }: {
  buckets: PnlBucket[]
  height?: number
}) {
  const [ref, width] = useElementWidth()
  const [tip, setTip] = useState<TooltipState | null>(null)

  const pad = { left: 8, right: 64, top: 12, bottom: 22 }
  const plotW = Math.max(width - pad.left - pad.right, 10)
  const plotH = height - pad.top - pad.bottom

  const maxAbs = Math.max(...buckets.map((b) => Math.abs(b.gross_pnl)), 1)
  const zeroY = pad.top + plotH / 2
  const yOf = (v: number) => zeroY - (v / maxAbs) * (plotH / 2)

  const gap = 2
  const band = plotW / Math.max(buckets.length, 1)
  const barW = Math.max(Math.min(band - gap, 48), 2)

  return (
    <div ref={ref} className="relative" data-chart="weekly-pnl">
      <svg width={width} height={height} role="img" aria-label="Weekly profit and loss">
        <line x1={pad.left} x2={pad.left + plotW} y1={zeroY} y2={zeroY}
              stroke={LINE_STRONG} strokeWidth={1} />
        <text x={pad.left + plotW + 6} y={zeroY + 3} fontSize={10}
              fontFamily={MONO} fill={INK_FAINT}>0</text>
        {buckets.map((b, i) => {
          const x = pad.left + i * band + (band - barW) / 2
          const y1 = yOf(b.gross_pnl)
          const up = b.gross_pnl >= 0
          return (
            <g key={b.week_start}>
              <path
                d={roundedBarPath(x, barW, zeroY, y1, 4)}
                fill={up ? PROFIT : LOSS}
              />
              <rect
                x={pad.left + i * band} y={pad.top} width={band} height={plotH}
                fill="transparent"
                onMouseEnter={(e) => {
                  const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()
                  setTip({
                    x: x + barW / 2, y: Math.min(zeroY, y1),
                    content: (
                      <div>
                        <div className="text-ink-soft">Week of {shortDate(b.week_start)}</div>
                        <div className={`num ${up ? 'text-profit' : 'text-loss'}`}>
                          {signedFmt(b.gross_pnl)}
                        </div>
                        <div className="text-ink-faint">{b.trades} trade{b.trades === 1 ? '' : 's'}</div>
                      </div>
                    ),
                  })
                  void rect
                }}
                onMouseLeave={() => setTip(null)}
              />
            </g>
          )
        })}
        {buckets.length > 0 && (
          <>
            <text x={pad.left} y={height - 6} fontSize={10} fontFamily={MONO} fill={INK_FAINT}>
              {shortDate(buckets[0].week_start)}
            </text>
            <text x={pad.left + plotW} y={height - 6} fontSize={10} fontFamily={MONO}
                  fill={INK_FAINT} textAnchor="end">
              {shortDate(buckets[buckets.length - 1].week_start)}
            </text>
          </>
        )}
      </svg>
      <Tooltip tip={tip} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Candlestick chart. Direction is never color-alone: up candles are HOLLOW
// (outlined), down candles are FILLED -- readable in any color vision.
// ---------------------------------------------------------------------------

export interface Candle {
  timestamp: number
  open: number
  high: number
  low: number
  close: number | null
  volume: number
}

export interface PriceLine {
  price: number
  label: string
  kind: 'entry' | 'sl' | 'tp'
}

export function CandleChart({ bars, lines = [], height = 300, digits = 5 }: {
  bars: Candle[]
  lines?: PriceLine[]
  height?: number
  digits?: number
}) {
  const [ref, width] = useElementWidth()
  const [tip, setTip] = useState<TooltipState | null>(null)

  const usable = bars.filter((b) => b.close != null) as (Candle & { close: number })[]

  const pad = { left: 8, right: 72, top: 12, bottom: 22 }
  const plotW = Math.max(width - pad.left - pad.right, 10)
  const plotH = height - pad.top - pad.bottom

  const lows = usable.map((b) => b.low)
  const highs = usable.map((b) => b.high)
  const linePrices = lines.map((l) => l.price)
  const min = Math.min(...lows, ...(linePrices.length ? linePrices : [Infinity]))
  const max = Math.max(...highs, ...(linePrices.length ? linePrices : [-Infinity]))
  const span = (max - min) || 1
  const yOf = (v: number) => pad.top + plotH - ((v - min) / span) * plotH

  const band = plotW / Math.max(usable.length, 1)
  const bodyW = Math.max(Math.min(band - 2, 12), 2)

  const ticks = [min, min + span * 0.25, min + span * 0.5, min + span * 0.75, max]

  return (
    <div ref={ref} className="relative" data-chart="candles">
      <svg width={width} height={height} role="img" aria-label="Price chart">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.left} x2={pad.left + plotW} y1={yOf(v)} y2={yOf(v)}
                  stroke={LINE} strokeWidth={1} />
            <text x={pad.left + plotW + 6} y={yOf(v) + 3} fontSize={10}
                  fontFamily={MONO} fill={INK_FAINT}>
              {v.toFixed(digits)}
            </text>
          </g>
        ))}
        {usable.map((b, i) => {
          const cx = pad.left + i * band + band / 2
          const up = b.close >= b.open
          const color = up ? PROFIT : LOSS
          const bodyTop = yOf(Math.max(b.open, b.close))
          const bodyBottom = yOf(Math.min(b.open, b.close))
          const bodyH = Math.max(bodyBottom - bodyTop, 1)
          return (
            <g key={b.timestamp}>
              <line x1={cx} x2={cx} y1={yOf(b.high)} y2={yOf(b.low)}
                    stroke={color} strokeWidth={1} />
              <rect
                x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH}
                fill={up ? 'var(--color-paper)' : color}
                stroke={color} strokeWidth={1.2}
              />
              <rect
                x={pad.left + i * band} y={pad.top} width={band} height={plotH}
                fill="transparent"
                onMouseEnter={() => setTip({
                  x: cx, y: bodyTop,
                  content: (
                    <div className="num">
                      <div className="text-ink-soft">
                        {new Date(b.timestamp).toLocaleString('en-GB', {
                          day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                        })}
                      </div>
                      <div>O {b.open.toFixed(digits)} · H {b.high.toFixed(digits)}</div>
                      <div>L {b.low.toFixed(digits)} · C {b.close.toFixed(digits)}</div>
                    </div>
                  ),
                })}
                onMouseLeave={() => setTip(null)}
              />
            </g>
          )
        })}
        {lines.map((l) => (
          <g key={`${l.kind}-${l.price}`}>
            <line
              x1={pad.left} x2={pad.left + plotW} y1={yOf(l.price)} y2={yOf(l.price)}
              stroke={l.kind === 'sl' ? LOSS : l.kind === 'tp' ? PROFIT : INK_SOFT}
              strokeWidth={1} strokeDasharray="4 3"
            />
            <text x={pad.left + 2} y={yOf(l.price) - 3} fontSize={9}
                  fontFamily={MONO}
                  fill={l.kind === 'sl' ? LOSS : l.kind === 'tp' ? PROFIT : INK_SOFT}>
              {l.label}
            </text>
          </g>
        ))}
        {usable.length > 0 && (
          <>
            <text x={pad.left} y={height - 6} fontSize={10} fontFamily={MONO} fill={INK_FAINT}>
              {shortDate(usable[0].timestamp)}
            </text>
            <text x={pad.left + plotW} y={height - 6} fontSize={10} fontFamily={MONO}
                  fill={INK_FAINT} textAnchor="end">
              {shortDate(usable[usable.length - 1].timestamp)}
            </text>
          </>
        )}
      </svg>
      <Tooltip tip={tip} />
    </div>
  )
}

/* ---------------------------------------------------------------------------
   Micro-visualizations for stat tiles.  Same token palette as the big
   charts; small enough to sit under a number without shouting.
--------------------------------------------------------------------------- */

/** Tiny trend line for a stat tile — brand hue, no axes, no grid. */
export function Sparkline({ points, label }: {
  points: { timestamp: number; balance: number }[]
  label: string
}) {
  if (points.length < 2) return null
  const W = 160
  const H = 36
  const PAD = 3
  const values = points.map((pt) => pt.balance)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const x = (i: number) => PAD + (i / (points.length - 1)) * (W - 2 * PAD)
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - 2 * PAD)
  const d = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ')
  const last = values[values.length - 1]
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="w-full h-9"
    >
      <path d={d} fill="none" stroke={BRAND} strokeWidth={2}
            strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <circle cx={x(values.length - 1)} cy={y(last)} r={3} fill={BRAND}
              stroke="var(--color-card)" strokeWidth={1.5} />
    </svg>
  )
}

/** Single-ratio meter: profit fill on a quiet track. Not a pie of two. */
export function WinRateMeter({ wins, losses }: { wins: number; losses: number }) {
  const total = wins + losses
  if (total === 0) return null
  const pct = (wins / total) * 100
  return (
    <div
      data-chart="win-meter"
      role="img"
      aria-label={`Win rate ${pct.toFixed(1)}%: ${wins} wins, ${losses} losses`}
      className="mt-2 h-1.5 rounded-full bg-line overflow-hidden"
    >
      <div className="h-full rounded-full bg-profit" style={{ width: `${pct}%` }} />
    </div>
  )
}

/** Magnitude bar for one figure of a labeled pair sharing `max` as scale. */
export function TradeBar({ value, max, tone }: {
  value: number | null
  max: number
  tone: 'profit' | 'loss'
}) {
  if (value == null || max <= 0) return null
  const pct = Math.min((Math.abs(value) / max) * 100, 100)
  return (
    <div data-chart="trade-bar" aria-hidden="true"
         className="mt-2 h-1.5 rounded-full bg-line overflow-hidden">
      <div
        className={`h-full rounded-full ${tone === 'profit' ? 'bg-profit' : 'bg-loss'}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

/**
 * Composite health score for the copier: win rate, win/loss ratio, and
 * profit factor, each normalized to 0..1 and averaged into a 0-100 score.
 * Rendered as a small triangle radar beside the score and a fixed
 * red-to-green meter; the number carries the value, the color is a
 * reinforcement (never the only encoding).
 */
export function MirrorScore({ winRate, avgWin, avgLoss, profitFactor }: {
  winRate: number | null
  avgWin: number | null
  avgLoss: number | null
  profitFactor: number | null
}) {
  const wl = avgWin != null && avgLoss != null && avgLoss !== 0
    ? (avgWin / Math.abs(avgLoss)) / (1 + avgWin / Math.abs(avgLoss))
    : null
  const pf = profitFactor != null ? profitFactor / (1 + profitFactor) : null
  const axes = [winRate, wl, pf]
  if (axes.some((v) => v == null)) {
    return <div className="num text-xl mt-0.5 text-ink">—</div>
  }
  const vals = axes as number[]
  const score = Math.round((vals.reduce((a, b) => a + b, 0) / 3) * 100)
  const toneClass = score >= 70 ? 'text-profit' : score >= 40 ? 'text-ink' : 'text-loss'

  // Equilateral radar: apex up, then bottom-right, bottom-left.
  const cx = 60
  const cy = 44
  const R = 26
  const angles = [-90, 30, 150].map((deg) => (deg * Math.PI) / 180)
  const point = (angle: number, r: number) =>
    `${(cx + r * Math.cos(angle)).toFixed(1)},${(cy + r * Math.sin(angle)).toFixed(1)}`
  const outer = angles.map((a) => point(a, R)).join(' ')
  const inner = angles.map((a, i) => point(a, Math.max(vals[i], 0.08) * R)).join(' ')

  return (
    <div
      className="mt-0.5 flex items-center gap-3"
      role="img"
      aria-label={`MirrorFleet score ${score} of 100: win rate ${Math.round(vals[0] * 100)}%, win/loss ${Math.round(vals[1] * 100)}%, profit factor ${Math.round(vals[2] * 100)}% of scale`}
    >
      <svg viewBox="0 0 120 88" className="h-14 w-20 shrink-0" aria-hidden="true">
        <polygon points={outer} fill="none" stroke={LINE_STRONG} strokeWidth={1.5} />
        <polygon points={inner} fill={BRAND_WASH} stroke={BRAND} strokeWidth={2}
                 strokeLinejoin="round" />
        <text x={cx} y={12} textAnchor="middle" fontSize={9} fill={INK_SOFT}>Win %</text>
        <text x={cx + R + 4} y={cy + R * 0.62} textAnchor="middle" fontSize={9} fill={INK_SOFT}>PF</text>
        <text x={cx - R - 6} y={cy + R * 0.62} textAnchor="middle" fontSize={9} fill={INK_SOFT}>W/L</text>
      </svg>
      <div>
        <div className="num text-xl font-semibold tracking-tight">
          <span className={toneClass}>{score}</span>
          <span className="text-ink-soft text-sm font-medium"> / 100</span>
        </div>
        <div
          className="relative mt-1.5 h-1.5 w-24 rounded-full"
          style={{ background: 'linear-gradient(to right, #d6344b, #e7b54e, #15803d)' }}
        >
          <span
            className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-3 w-3 rounded-full bg-card border-2 border-ink"
            style={{ left: `${score}%` }}
          />
        </div>
      </div>
    </div>
  )
}
