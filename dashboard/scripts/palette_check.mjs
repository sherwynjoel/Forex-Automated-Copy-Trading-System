// WCAG contrast prover for BOTH MirrorFleet palettes.
//
// Reads the token values straight out of src/index.css (the @theme block is
// the day palette, the [data-theme="dark"] block the night one) so this file
// can never drift from what ships. Every pair below must meet its threshold
// in both themes or the process exits 1 -- `npm test` runs it first.
//
// Thresholds: 4.5 for text (WCAG 1.4.3 AA), 3.0 for the boundary of a UI
// control (WCAG 1.4.11 non-text contrast), and small "distinguishable"
// floors for decorative hairlines and surfaces, which have no WCAG rule but
// vanish below them.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'index.css'), 'utf8')

function block(css, openRe) {
  const start = css.search(openRe)
  if (start < 0) throw new Error(`block not found: ${openRe}`)
  const body = css.slice(css.indexOf('{', start) + 1)
  return body.slice(0, body.indexOf('}'))
}

function tokens(body) {
  const out = {}
  for (const m of body.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]] = m[2]
  return out
}

const light = tokens(block(css, /@theme\s*\{/))
const dark = tokens(block(css, /\[data-theme="dark"\]\s*\{/))

function lum(hex) {
  const c = [0, 2, 4].map((i) => parseInt(hex.slice(1 + i, 3 + i), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
function contrast(a, b) {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (l1 + 0.05) / (l2 + 0.05)
}

// [fg, bg, min, why]
const TEXT = 4.5
const CONTROL = 3.0
const pairs = [
  // body and secondary text on the two surfaces
  ['ink', 'card', TEXT, 'body text on cards'],
  ['ink', 'paper', TEXT, 'body text on the page ground'],
  ['ink-soft', 'card', TEXT, 'secondary text on cards'],
  ['ink-soft', 'paper', TEXT, 'secondary text on the page ground'],
  ['ink-faint', 'card', TEXT, 'faint text (timestamps, ticks, placeholders) on cards'],
  ['ink-faint', 'paper', TEXT, 'faint text on the page ground (empty and loading states)'],
  ['ink-soft', 'brand-wash', TEXT, 'desk-label inside a brand wash'],
  ['ink', 'brand-wash', TEXT, 'notice banner text'],
  ['ink', 'glass-solid', TEXT, 'ink on the glass surface, worst case (fill over ink)'],
  // brand
  ['brand', 'card', TEXT, 'links and brand text on cards'],
  ['brand', 'paper', TEXT, 'brand text on the page ground'],
  ['brand-deep', 'brand-wash', TEXT, 'brand text on its wash (selected rows, chips)'],
  ['on-accent', 'brand', TEXT, 'label on the primary button'],
  ['on-accent', 'brand-deep', TEXT, 'label on the primary button, hover'],
  // profit / loss / warn
  ['profit', 'card', TEXT, 'profit figures on cards'],
  ['profit', 'paper', TEXT, 'profit figures on the page ground'],
  ['on-accent', 'profit', TEXT, 'label on Buy / Resume'],
  ['on-accent', 'profit-deep', TEXT, 'label on Buy / Resume, hover'],
  ['profit-deep', 'profit-wash', TEXT, 'profit chip'],
  ['loss', 'card', TEXT, 'loss figures on cards'],
  ['loss', 'paper', TEXT, 'loss figures and ghost danger actions on the page ground'],
  ['on-accent', 'loss', TEXT, 'label on Sell / Stop / danger'],
  ['on-accent', 'loss-deep', TEXT, 'label on danger, hover'],
  ['loss-deep', 'loss-wash', TEXT, 'error banner and loss chip'],
  ['warn', 'card', TEXT, 'warn text on cards'],
  ['on-accent', 'warn', TEXT, 'label on the dry-run button'],
  ['on-accent', 'warn-deep', TEXT, 'label on the dry-run button, hover'],
  ['warn-deep', 'warn-wash', TEXT, 'warn banner and DRY RUN chip'],
  // control boundaries (non-text)
  ['field-line', 'card', CONTROL, 'input, select and outlined-button edge on cards'],
  ['field-line', 'paper', CONTROL, 'input, select and outlined-button edge on the page ground'],
  ['brand', 'card', CONTROL, 'focus ring on cards'],
  ['brand', 'paper', CONTROL, 'focus ring on the page ground'],
  // decorative floors
  ['line-strong', 'card', 1.5, 'strong hairline still visible on cards'],
  ['line', 'card', 1.2, 'hairline still visible on cards'],
  ['card', 'paper', 1.05, 'cards distinguishable from the page ground'],
]

// The glass fill is translucent; its worst legible case is the fill laid
// over the darkest thing it can float above (ink on day, paper on night).
function glassSolid(pal, under) {
  const m = /--color-glass:\s*rgb\((\d+)\s+(\d+)\s+(\d+)\s*\/\s*([\d.]+)\)/.exec(pal.raw)
  if (!m) throw new Error('glass token not found')
  const a = parseFloat(m[4])
  const u = [1, 3, 5].map((i) => parseInt(under.slice(i, i + 2), 16))
  const mix = [m[1], m[2], m[3]].map((c, i) => Math.round(parseInt(c) * a + u[i] * (1 - a)))
  return '#' + mix.map((c) => c.toString(16).padStart(2, '0')).join('')
}
light.raw = block(css, /@theme\s*\{/)
dark.raw = block(css, /\[data-theme="dark"\]\s*\{/)
light['glass-solid'] = glassSolid(light, light.ink)
dark['glass-solid'] = glassSolid(dark, dark.paper)

let failures = 0
for (const [name, pal] of [['LIGHT', light], ['DARK', dark]]) {
  console.log(`\n${name}`)
  for (const [fg, bg, min, why] of pairs) {
    if (!pal[fg] || !pal[bg]) { console.log(`MISSING token ${fg} or ${bg}`); failures++; continue }
    const c = contrast(pal[fg], pal[bg])
    const ok = c >= min
    if (!ok) failures++
    console.log(`${ok ? 'PASS' : 'FAIL'} ${c.toFixed(2).padStart(6)} (need ${min})  ${fg} on ${bg}  -- ${why}`)
  }
}
console.log(failures === 0 ? '\nALL PASS (both themes)' : `\n${failures} FAILURE(S)`)
process.exit(failures === 0 ? 0 : 1)
