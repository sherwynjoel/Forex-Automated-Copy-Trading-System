import { readFileSync } from 'node:fs'
import { expect, test } from 'vitest'

// fs, not `?raw`: vitest's css transform intercepts .css imports and hands
// back an empty module, which would make every assertion here vacuous.
const css = readFileSync('src/index.css', 'utf8')
const html = readFileSync('index.html', 'utf8')
const themeHook = readFileSync('src/hooks/useTheme.ts', 'utf8')

const TOKENS = [
  'paper', 'card', 'ink', 'ink-soft', 'ink-faint', 'line', 'line-strong', 'field-line',
  'brand', 'brand-deep', 'brand-wash', 'profit', 'profit-deep', 'profit-wash',
  'loss', 'loss-deep', 'loss-wash', 'warn', 'warn-deep', 'warn-wash',
  'on-accent', 'wash-deep', 'glass', 'glass-line',
]

function blockOf(selector: string): string {
  const at = css.indexOf(selector)
  expect(at, `${selector} present in index.css`).toBeGreaterThanOrEqual(0)
  return css.slice(at, css.indexOf('\n}', at))
}

function hex(block: string, token: string): string {
  const m = new RegExp(`--color-${token}:\\s*(#[0-9a-fA-F]{6})`).exec(block)
  expect(m, `--color-${token} is a hex value`).not.toBeNull()
  return m![1].toLowerCase()
}

test('the light theme defines every token, the radii, the shadows and the easing', () => {
  const theme = blockOf('@theme')
  for (const t of TOKENS) expect(theme, `--color-${t} in @theme`).toContain(`--color-${t}:`)
  for (const v of ['--radius-card:', '--radius-inset:', '--radius-control:',
                   '--shadow-card:', '--shadow-float:', '--ease-out-quint:']) {
    expect(theme, `${v} in @theme`).toContain(v)
  }
})

test('the dim theme overrides every colour token and flips color-scheme', () => {
  const dark = blockOf('[data-theme="dark"]')
  expect(dark).toContain('color-scheme: dark')
  for (const t of TOKENS) expect(dark, `--color-${t} in dark block`).toContain(`--color-${t}:`)
})

test('the wash, glass, inset and type utilities exist', () => {
  expect(css).toContain('body::before')
  expect(css).toContain('@keyframes wash-drift')
  expect(css).toMatch(/\.glass\s*\{[^}]*backdrop-filter/)
  expect(css).toMatch(/\.inset\s*\{[^}]*var\(--radius-inset\)/)
  expect(css).toMatch(/\.hero-title\s*\{[^}]*clamp\(/)
  expect(css).toMatch(/prefers-reduced-transparency:\s*reduce/)
  expect(css).toMatch(/prefers-reduced-motion:\s*reduce/)
})

test('paper stays in sync between index.css, the pre-paint script and useTheme', () => {
  const light = hex(blockOf('@theme'), 'paper')
  const dark = hex(blockOf('[data-theme="dark"]'), 'paper')
  expect(html).toContain(`<meta name="theme-color" content="${light}" />`)
  expect(html).toContain(`setAttribute('content', '${dark}')`)
  expect(themeHook).toContain(`light: '${light}'`)
  expect(themeHook).toContain(`dark: '${dark}'`)
})

test('the favicon and the logo carry the aqua ramp, not the old violet', () => {
  const logo = readFileSync('src/components/Logo.tsx', 'utf8')
  for (const src of [html, logo]) {
    expect(src).not.toMatch(/6c5fc7|8b7fd7|a99ff0/i)
    expect(src.toLowerCase()).toContain('0b7c80')
  }
})
