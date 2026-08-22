// WCAG contrast prover for the MirrorFleet dark palette.
// Every pair listed must meet its threshold before the palette ships.

const dark = {
  paper: '#131118',
  card: '#1c1926',
  line: '#2b2738',
  'line-strong': '#443e57',
  ink: '#eeeaf4',
  'ink-soft': '#aca3bf',
  'ink-faint': '#8b829e',      // placeholder; will tune
  brand: '#9d90ec',
  'brand-deep': '#bcb2f6',
  'brand-wash': '#2b2542',
  profit: '#43c07a',
  'profit-deep': '#7edfa6',
  'profit-wash': '#152b1e',
  loss: '#f26a80',
  'loss-deep': '#fda4b0',
  'loss-wash': '#3a1c24',
  warn: '#d09a3e',
  'warn-deep': '#e9c37c',
  'warn-wash': '#332708',
  white: '#ffffff',
  'on-accent': '#17141f',
}

// fix bad placeholder
dark['ink-faint'] = '#8b81a0'

function lum(hex) {
  const h = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
function contrast(a, b) {
  const [l1, l2] = [lum(dark[a] ?? a), lum(dark[b] ?? b)].sort((x, y) => y - x)
  return (l1 + 0.05) / (l2 + 0.05)
}

// [fg, bg, min, label]
const pairs = [
  ['ink', 'card', 4.5, 'body text on cards'],
  ['ink', 'paper', 4.5, 'body text on page'],
  ['ink-soft', 'card', 4.5, 'secondary text on cards'],
  ['ink-soft', 'paper', 4.5, 'secondary text on page'],
  ['ink-faint', 'card', 4.5, 'faint text on cards (timestamps, ticks)'],
  ['ink-faint', 'paper', 4.5, 'faint text on page'],
  ['brand', 'card', 4.5, 'links/small brand text on cards'],
  ['brand', 'paper', 4.5, 'brand text on page'],
  ['on-accent', 'brand', 4.5, 'label on brand button'],
  ['on-accent', 'brand-deep', 4.5, 'label on brand hover'],
  ['brand-deep', 'brand-wash', 4.5, 'deep text on brand wash'],
  ['profit', 'card', 4.5, 'profit numbers on cards'],
  ['profit', 'paper', 4.5, 'profit numbers on page'],
  ['on-accent', 'profit', 4.5, 'label on Buy button'],
  ['on-accent', 'profit-deep', 4.5, 'label on Buy hover'],
  ['profit-deep', 'profit-wash', 4.5, 'chips: profit-deep on wash'],
  ['loss', 'card', 4.5, 'loss numbers on cards'],
  ['loss', 'paper', 4.5, 'loss numbers on page'],
  ['on-accent', 'loss', 4.5, 'label on Sell/danger button'],
  ['on-accent', 'loss-deep', 4.5, 'label on danger hover'],
  ['on-accent', 'warn', 4.5, 'label on warn button'],
  ['loss-deep', 'loss-wash', 4.5, 'chips: loss-deep on wash'],
  ['warn-deep', 'warn-wash', 4.5, 'warn text on wash'],
  ['line-strong', 'card', 1.6, 'input borders visible on cards'],
  ['card', 'paper', 1.05, 'cards distinguishable from page'],
]

let fail = 0
for (const [fg, bg, min, label] of pairs) {
  const c = contrast(fg, bg)
  const ok = c >= min
  if (!ok) fail++
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.toFixed(2)} (need ${min})  ${fg} on ${bg} — ${label}`)
}
console.log(fail === 0 ? '\nALL PASS' : `\n${fail} FAILURES`)
