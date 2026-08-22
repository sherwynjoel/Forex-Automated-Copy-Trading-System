import { readFileSync } from 'node:fs'
import { expect, test } from 'vitest'

// fs, not `?raw`: vitest's css transform intercepts .css imports and hands
// back an empty module, which would make every assertion here vacuous.
const css = readFileSync('src/index.css', 'utf8')

// jsdom never applies index.css, so the suite would stay green if the dark
// block vanished. This test pins the artifact itself: every themed token
// must be redefined inside [data-theme="dark"].

const TOKENS = [
  'paper', 'card', 'ink', 'ink-soft', 'ink-faint', 'line', 'line-strong',
  'brand', 'brand-deep', 'brand-wash', 'profit', 'profit-deep', 'profit-wash',
  'loss', 'loss-deep', 'loss-wash', 'warn', 'warn-deep', 'warn-wash',
  'on-accent',
]

function blockOf(selector: string): string {
  const at = css.indexOf(selector)
  expect(at, `${selector} present in index.css`).toBeGreaterThanOrEqual(0)
  return css.slice(at, css.indexOf('}', at))
}

test('the light theme defines every token, including on-accent', () => {
  const theme = blockOf('@theme')
  for (const t of TOKENS) {
    expect(theme, `--color-${t} in @theme`).toContain(`--color-${t}:`)
  }
})

test('the dark theme overrides every token and flips color-scheme', () => {
  const dark = blockOf('[data-theme="dark"]')
  expect(dark).toContain('color-scheme: dark')
  for (const t of TOKENS) {
    expect(dark, `--color-${t} in dark block`).toContain(`--color-${t}:`)
  }
})

test('dark paper stays in sync with the pre-paint script and useTheme', () => {
  const dark = blockOf('[data-theme="dark"]')
  expect(dark).toContain('--color-paper: #131118')
})

test('the select chevron gets a dark-brand override', () => {
  expect(css).toContain('[data-theme="dark"] select.bg-card')
  expect(css).toContain('%239d90ec')
})
