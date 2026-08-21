import { ReactNode, useEffect, useRef, useState } from 'react'
import { signed as signedFmt } from '../lib/format'
import type { SeriesPoint } from '../lib/perf'

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

/**
 * Composite health score for the copier: win rate, win/loss ratio, and
 * profit factor, each normalized to 0..1 and averaged into a 0-100 score.
 * Rendered as a small triangle radar beside the score and a fixed
 * red-to-green meter; the number carries the value, the color is a
 * reinforcement (never the only encoding).
 */
export function MirrorScore({ winRate, avgWin, avgLoss, profitFactor, large = false }: {
  winRate: number | null
  avgWin: number | null
  avgLoss: number | null
  profitFactor: number | null
  large?: boolean
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
  const cx = large ? 100 : 60
  const cy = large ? 88 : 52
  const R = large ? 58 : 32
  const labelSize = large ? 13 : 12
  const angles = [-90, 30, 150].map((deg) => (deg * Math.PI) / 180)
  const coords = (angle: number, r: number): [number, number] =>
    [cx + r * Math.cos(angle), cy + r * Math.sin(angle)]
  const point = (angle: number, r: number) => {
    const [x, y] = coords(angle, r)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }
  // The large card gets concentric grid rings; the compact strip just the rim.
  const gridRs = large ? [R, (R * 2) / 3, R / 3] : [R]
  const innerPts = angles.map((a, i) => coords(a, Math.max(vals[i], 0.1) * R))
  const inner = innerPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const aria = `MirrorFleet score ${score} of 100: win rate ${Math.round(vals[0] * 100)}%, win/loss ${Math.round(vals[1] * 100)}%, profit factor ${Math.round(vals[2] * 100)}% of scale`

  const radar = (viewBox: string, cls: string) => (
    <svg viewBox={viewBox} className={cls} aria-hidden="true">
      {gridRs.map((r) => (
        <polygon key={r} points={angles.map((a) => point(a, r)).join(' ')}
                 fill="none" stroke={LINE_STRONG} strokeWidth={r === R ? 1.5 : 1} />
      ))}
      <polygon points={inner} fill={BRAND} fillOpacity={0.18} stroke={BRAND}
               strokeWidth={2.5} strokeLinejoin="round" />
      {innerPts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={3} fill={BRAND} stroke="var(--color-card)" strokeWidth={1.5} />
      ))}
      <text x={cx} y={cy - R - 8} textAnchor="middle" fontSize={labelSize} fontWeight={600} fill={INK_SOFT}>Win %</text>
      <text x={cx + R + 2} y={cy + R * 0.68 + 12} textAnchor="middle" fontSize={labelSize} fontWeight={600} fill={INK_SOFT}>PF</text>
      <text x={cx - R - 2} y={cy + R * 0.68 + 12} textAnchor="middle" fontSize={labelSize} fontWeight={600} fill={INK_SOFT}>W/L</text>
    </svg>
  )
  const meter = (
    <div
      className="relative h-1.5 w-full rounded-full"
      style={{ background: 'linear-gradient(to right, #d6344b, #e7b54e, #15803d)' }}
    >
      <span
        className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-3 w-3 rounded-full bg-card border-2 border-ink"
        style={{ left: `${score}%` }}
      />
    </div>
  )

  if (large) {
    return (
      <div className="flex h-full flex-col" role="img" aria-label={aria}>
        {radar('0 0 200 152', 'w-full max-w-60 mx-auto h-auto')}
        <div className="mt-auto flex items-center gap-3 pt-3">
          <div className="num text-2xl font-semibold tracking-tight shrink-0">
            <span className={toneClass}>{score}</span>
            <span className="text-ink-soft text-sm font-medium"> / 100</span>
          </div>
          <div className="min-w-0 flex-1">{meter}</div>
        </div>
      </div>
    )
  }
  return (
    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1" role="img" aria-label={aria}>
      {radar('0 0 120 100', 'h-20 w-24 shrink-0')}
      <div className="min-w-0 flex-1 max-w-32">
        <div className="num text-xl font-semibold tracking-tight">
          <span className={toneClass}>{score}</span>
          <span className="text-ink-soft text-sm font-medium"> / 100</span>
        </div>
        <div className="mt-1.5 max-w-24">{meter}</div>
      </div>
    </div>
  )
}

const tickFmt = (v: number) => {
  if (Math.abs(v) < 1e-9) v = 0 // clamp float dust so the axis never says -0.00
  return Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k`
    : Math.abs(v) >= 100 ? v.toFixed(0)
    : v.toFixed(2)
}

/**
 * Card line chart with a labeled y-axis (four hairline gridlines), first/last
 * date labels, and the standard hover readout — used for cumulative P&L and
 * drawdown curves.
 */
export function PerfLine({ points, tone = 'brand', label, chart, height = 150 }: {
  points: SeriesPoint[]
  tone?: 'brand' | 'loss'
  label: string
  chart: string
  height?: number
}) {
  const [ref, width] = useElementWidth()
  const [tip, setTip] = useState<TooltipState | null>(null)
  if (points.length < 2) {
    return (
      <div data-chart={chart} className="flex h-24 items-center justify-center text-xs text-ink-faint">
        Not enough closed trades yet.
      </div>
    )
  }
  const pad = { left: 46, right: 10, top: 10, bottom: 22 }
  const plotW = Math.max(width - pad.left - pad.right, 10)
  const plotH = height - pad.top - pad.bottom
  const vs = points.map((p) => p.v)
  let vmin = Math.min(...vs)
  let vmax = Math.max(...vs)
  if (vmax - vmin < 1e-9) {
    vmin -= 1
    vmax += 1
  }
  const span = vmax - vmin
  const t0 = points[0].t
  const t1 = points[points.length - 1].t
  const xOf = (t: number) =>
    pad.left + (t1 === t0 ? plotW / 2 : ((t - t0) / (t1 - t0)) * plotW)
  const yOf = (v: number) => pad.top + ((vmax - v) / span) * plotH
  const ticks = [vmax, vmin + (span * 2) / 3, vmin + span / 3, vmin]
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${xOf(p.t).toFixed(1)},${yOf(p.v).toFixed(1)}`).join(' ')
  const stroke = tone === 'loss' ? LOSS : BRAND
  const last = points[points.length - 1]

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const mx = e.clientX - rect.left + pad.left
    let nearest = points[0]
    let best = Infinity
    for (const p of points) {
      const d = Math.abs(xOf(p.t) - mx)
      if (d < best) { best = d; nearest = p }
    }
    setTip({
      x: xOf(nearest.t), y: yOf(nearest.v),
      content: (
        <div>
          <div className="text-ink-soft">{shortDate(nearest.t)}</div>
          <div className="num text-ink">{signedFmt(nearest.v)}</div>
        </div>
      ),
    })
  }

  return (
    <div ref={ref} className="relative" data-chart={chart}>
      <svg width={width} height={height} role="img" aria-label={label}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={pad.left} x2={pad.left + plotW} y1={yOf(v)} y2={yOf(v)}
                  stroke={LINE} strokeDasharray="2 3" />
            <text x={pad.left - 6} y={yOf(v) + 3} textAnchor="end" fontSize={10}
                  fill={INK_FAINT} fontFamily={MONO}>
              {tickFmt(v)}
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke={stroke} strokeWidth={2}
              strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={xOf(last.t)} cy={yOf(last.v)} r={3} fill={stroke} />
        {tip && (
          <circle cx={tip.x} cy={tip.y} r={4} fill={stroke} stroke="var(--color-card)" strokeWidth={2} />
        )}
        <text x={pad.left} y={height - 6} fontSize={10} fontFamily={MONO} fill={INK_FAINT}>
          {shortDate(t0)}
        </text>
        <text x={pad.left + plotW} y={height - 6} textAnchor="end" fontSize={10}
              fontFamily={MONO} fill={INK_FAINT}>
          {shortDate(t1)}
        </text>
        <rect x={pad.left} y={pad.top} width={plotW} height={plotH} fill="transparent"
              onMouseMove={onMove} onMouseLeave={() => setTip(null)} />
      </svg>
      <Tooltip tip={tip} />
    </div>
  )
}

/**
 * Card bar chart: one bar per trading day diverging around a drawn zero
 * baseline (sign is position, not just color), labeled y-axis, dates, and
 * the standard hover readout.
 */
export function PerfBars({ bars, label, chart, height = 150 }: {
  bars: SeriesPoint[]
  label: string
  chart: string
  height?: number
}) {
  const [ref, width] = useElementWidth()
  const [tip, setTip] = useState<TooltipState | null>(null)
  if (!bars.length) {
    return (
      <div data-chart={chart} className="flex h-24 items-center justify-center text-xs text-ink-faint">
        Not enough closed trades yet.
      </div>
    )
  }
  const pad = { left: 46, right: 10, top: 10, bottom: 22 }
  const plotW = Math.max(width - pad.left - pad.right, 10)
  const plotH = height - pad.top - pad.bottom
  const vs = bars.map((b) => b.v)
  let vmin = Math.min(0, ...vs)
  let vmax = Math.max(0, ...vs)
  if (vmax - vmin < 1e-9) vmax += 1
  const span = vmax - vmin
  const yOf = (v: number) => pad.top + ((vmax - v) / span) * plotH
  const ticks = [vmax, vmin + (span * 2) / 3, vmin + span / 3, vmin]
  const slot = plotW / bars.length
  const bw = Math.min(slot * 0.6, 22)

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const idx = Math.min(bars.length - 1, Math.max(0, Math.floor(mx / slot)))
    const bar = bars[idx]
    setTip({
      x: pad.left + slot * idx + slot / 2, y: yOf(Math.max(bar.v, 0)),
      content: (
        <div>
          <div className="text-ink-soft">{shortDate(bar.t)}</div>
          <div className="num text-ink">{signedFmt(bar.v)}</div>
        </div>
      ),
    })
  }

  return (
    <div ref={ref} className="relative" data-chart={chart}>
      <svg width={width} height={height} role="img" aria-label={label}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={pad.left} x2={pad.left + plotW} y1={yOf(v)} y2={yOf(v)}
                  stroke={LINE} strokeDasharray="2 3" />
            <text x={pad.left - 6} y={yOf(v) + 3} textAnchor="end" fontSize={10}
                  fill={INK_FAINT} fontFamily={MONO}>
              {tickFmt(v)}
            </text>
          </g>
        ))}
        <line x1={pad.left} x2={pad.left + plotW} y1={yOf(0)} y2={yOf(0)} stroke={LINE_STRONG} />
        {bars.map((b, i) => (
          <path key={b.t}
                d={roundedBarPath(pad.left + slot * i + (slot - bw) / 2, bw, yOf(0), yOf(b.v), 2)}
                fill={b.v < 0 ? LOSS : PROFIT} />
        ))}
        {bars.length > 1 && (
          <text x={pad.left} y={height - 6} fontSize={10} fontFamily={MONO} fill={INK_FAINT}>
            {shortDate(bars[0].t)}
          </text>
        )}
        <text x={pad.left + plotW} y={height - 6} textAnchor="end" fontSize={10}
              fontFamily={MONO} fill={INK_FAINT}>
          {shortDate(bars[bars.length - 1].t)}
        </text>
        <rect x={pad.left} y={pad.top} width={plotW} height={plotH} fill="transparent"
              onMouseMove={onMove} onMouseLeave={() => setTip(null)} />
      </svg>
      <Tooltip tip={tip} />
    </div>
  )
}
