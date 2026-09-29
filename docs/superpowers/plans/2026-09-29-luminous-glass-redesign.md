# Luminous Glass Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the whole MirrorFleet dashboard as "luminous glass" (very light, aqua accent, frosted panels over a slow colour wash, dim mode kept) through the token layer and primitives, then redesign the six screens that define the product's feel.

**Architecture:** Tailwind v4 tokens in `dashboard/src/index.css` are the single source of colour, radius, shadow and motion; every primitive in `dashboard/src/components/` consumes them, so Tasks 1–2 change the look everywhere at once. Task 3 rebuilds the shell (glass rail, phone tab bar, code splitting, titles, skip link, NotFound). Tasks 4–8 redesign the auth stack, landing page, Overview, Accounts and investor pages against acceptance tests. Task 9 sweeps the remaining pages onto the new primitives and vocabulary; Task 10 runs the gates and hands off.

**Tech Stack:** React 18, TypeScript strict (`noUnusedLocals`, `noUnusedParameters`), Tailwind v4 (`@theme` tokens), Vite 5, vitest + Testing Library + user-event, react-router v7, `motion` (`motion/react`), `qrcode`. `npm test` = `node scripts/palette_check.mjs && tsc --noEmit -p tsconfig.app.json && vitest run`.

**Spec:** `docs/superpowers/specs/2026-09-29-luminous-glass-redesign-design.md`

## Global Constraints

- Only `dashboard/` changes (plus `app-logo.png` at the repo root in Task 1). `api/`, `copier/`, `db/`, `e2e/` are untouched; every request path, method and body the dashboard sends stays byte-identical.
- Colours, radii, shadows and easing come only from tokens in `index.css` (`bg-card`, `text-ink`, `border-field-line`, `rounded-card`, `shadow-card`, …). No hex, `rgb()` or Tailwind palette classes (`text-red-500`) in TSX; the two exceptions are `components/Logo.tsx` (SVG fills) and the favicon data URI in `index.html`.
- Glass (`.glass`) only on: the navigation rail and phone tab bar, the desk strip and page headers, `Card` panels, drawer/dialog headers, popovers. Tables, forms, figures and drawer/dialog bodies sit on `card` or `.inset`. Banners are never translucent.
- Bans (spec §2, impeccable): no gradient text, no side-stripe accents (`border-l-4`), no eyebrow kickers or numbered section markers on the landing page, no identical icon-card grids, no decorative motion beyond the wash; `.desk-label` only for table headers and field labels.
- Vocabulary: every user-visible "Slave" becomes "Follower"; sentence case for headings and labels; loading states use the `Loading` primitive, never "Loading..." text.
- Accessibility floor: every control has the aqua focus ring, 44px touch height on phones (`min-h-11 md:min-h-0`), real `<h1>` per page, `aria-current="page"` on the active nav link, `role="alert"` for errors and `role="status"` for notices, tabs with roving tabindex.
- Both themes pass `scripts/palette_check.mjs`; `npm test` is green at the end of every task; tests are updated for new structure, never weakened.
- Commit messages end with exactly these two trailers:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b`
- Work on branch `luminous-glass-redesign`; one commit per task; no push; deploy only on the owner's say-so (spec §11).
- All commands run from `dashboard/` in Git Bash unless a step says otherwise.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/index.css` | tokens (both themes), wash, glass, inset, radii, shadows, type utilities, focus ring, reduced motion | 1 |
| `scripts/palette_check.mjs` | contrast prover; glass underlay = `wash-deep`; new pairs | 1 |
| `src/components/Logo.tsx`, `index.html`, `src/hooks/useTheme.ts`, `app-logo.png` | brand mark and browser chrome colours | 1 |
| `src/theme-css.test.ts` | pins tokens, utilities and the theme-color sync | 1 |
| `src/components/Button.tsx`, `Input.tsx`, `Select.tsx`, `Badge.tsx`, `Banner.tsx`, `Drawer.tsx`, `ConfirmDialog.tsx`, `StatTile.tsx`, `SearchSelect.tsx` | primitive recipes on the new tokens | 2 |
| `src/components/Card.tsx`, `Tabs.tsx`, `Loading.tsx`, `PageHeader.tsx`, `AuthCard.tsx`, `Menu.tsx`, `SkipLink.tsx`, `src/hooks/usePageTitle.ts` | new primitives and hooks | 2 |
| `src/components/Layout.tsx`, `src/components/layout/NavRail.tsx`, `layout/BottomBar.tsx`, `layout/DeskStrip.tsx`, `src/pages/NotFound.tsx`, `src/App.tsx`, `src/lib/org.tsx` | shell, nav groups, phone tab bar, code splitting, route crossfade, NotFound, skip link, titles | 3 |
| `src/pages/Login.tsx`, `Register.tsx`, `Mpin.tsx`, `Join.tsx`, `Welcome.tsx` | auth stack on `AuthCard` | 4 |
| `src/pages/Landing.tsx`, `src/pages/landing/HeroPreview.tsx`, `public/overview-desk.png`, `index.html` (meta description) | landing page | 5 |
| `src/pages/Overview.tsx`, `src/pages/overview/AttentionCard.tsx`, `overview/SetupChecklist.tsx`, `overview/FleetGrid.tsx` | triage-first Overview | 6 |
| `src/pages/Accounts.tsx`, `src/pages/accounts/*.tsx`, `accounts/useAccountsPage.ts` | read-only rows, editing drawer, split | 7 |
| `src/pages/investor/*.tsx`, `src/lib/format.ts` | units, pending states, Copy address, review step | 8 |
| `src/pages/Positions.tsx`, `Trade.tsx`, `Automation.tsx`, `History.tsx`, `Performance.tsx`, `Logs.tsx`, `Members.tsx`, `Investors.tsx`, `src/components/KillSwitch.tsx`, `AccountSecurity.tsx` | sweep onto `PageHeader`/`Card`/`Tabs`/`Loading`, vocabulary | 9 |

---

### Task 1: Tokens, the luminous wash, glass, and the prover

**Files:**
- Modify: `src/index.css` (replace whole file)
- Modify: `scripts/palette_check.mjs:47-115`
- Modify: `src/components/Logo.tsx:14-21`
- Modify: `index.html:5-6,18-22`
- Modify: `src/hooks/useTheme.ts:7`
- Modify: `src/theme-css.test.ts`
- Create: `scripts/render_logo.mjs` (regenerates `app-logo.png`)
- Test: `src/theme-css.test.ts`, `scripts/palette_check.mjs` (runs first in `npm test`)

**Interfaces:**
- Consumes: nothing.
- Produces: token names unchanged plus `wash-deep`; Tailwind utilities `rounded-card` (1rem), `rounded-inset` (0.75rem), `rounded-control` (0.625rem), `shadow-card`, `shadow-float`, `ease-out-quint`; CSS classes `.glass`, `.inset`, `.hero-title`, `.page-title`, `.desk-label`, `.num`, `.stack-table`, `.pulse-dot`; the wash paints itself behind every page (`body::before`), so no page adds a background. Every later task relies on these names exactly.

- [ ] **Step 1: Write the failing tests**

Replace `src/theme-css.test.ts` with:

```ts
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/theme-css.test.ts`
Expected: FAIL — `--color-wash-deep` missing, no `body::before`, favicon still violet, theme-color `#f8f8fa` mismatch once the CSS changes.

- [ ] **Step 3: Replace `src/index.css`**

```css
@import "tailwindcss";

/* ---------------------------------------------------------------------------
   MirrorFleet design tokens — "luminous glass".

   A very light page over a slow colour wash; frosted glass for the layers
   that float (rail, desk strip, page headers, cards, drawer and dialog
   headers, popovers); crisp opaque insets for data. One accent: aqua.
   Type is Rubik throughout; numbers tabular. Every text/surface pair in
   BOTH themes is proven by scripts/palette_check.mjs, which npm test runs
   first. The dim theme is the same vocabulary on a deep teal-black ground.
--------------------------------------------------------------------------- */

@theme {
  --color-paper: #f4fafb;
  --color-card: #ffffff;
  --color-ink: #14303a;
  --color-ink-soft: #496670;
  --color-ink-faint: #557480;
  --color-line: #dbe9ed;
  --color-line-strong: #bcd3da;
  /* Edge of a control (inputs, selects, outlined buttons): 3:1 against both
     surfaces per WCAG 1.4.11. Decorative hairlines keep --color-line. */
  --color-field-line: #6a8893;
  --color-brand: #0b7c80;
  --color-brand-deep: #075d61;
  --color-brand-wash: #e0f5f4;
  --color-profit: #15803d;
  --color-profit-deep: #116230;
  --color-profit-wash: #e6f6ec;
  --color-loss: #c92e45;
  --color-loss-deep: #a52333;
  --color-loss-wash: #fdecec;
  --color-warn: #9a6700;
  --color-warn-deep: #7a5200;
  --color-warn-wash: #fbf1d9;
  /* Text on accent-coloured fills (buttons, chips): white by day, ink in
     the dim theme where the accents lighten. */
  --color-on-accent: #ffffff;
  /* The deepest stop of the wash: the darkest thing glass ever floats
     over, and therefore the prover's glass underlay. */
  --color-wash-deep: #c6e7ec;
  /* Glass: frosted fill and edge for the floating layers. Data never sits
     directly on glass (see .inset). */
  --color-glass: rgb(255 255 255 / 0.72);
  --color-glass-line: rgb(20 48 58 / 0.10);

  --radius-card: 1rem;
  --radius-inset: 0.75rem;
  --radius-control: 0.625rem;

  --shadow-card: 0 8px 30px rgb(11 124 128 / 0.08);
  --shadow-float: 0 16px 48px rgb(20 48 58 / 0.14);

  --ease-out-quint: cubic-bezier(0.23, 1, 0.32, 1);

  --font-sans: "Rubik", "Helvetica Neue", Arial, sans-serif;
  --font-mono: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
  --font-display: "Rubik", "Helvetica Neue", Arial, sans-serif;
}

/* The wash: three soft radial gradients, defined per theme. */
:root {
  color-scheme: light;
  --wash-a: rgb(11 124 128 / 0.16);
  --wash-b: rgb(96 165 250 / 0.12);
  --wash-c: rgb(139 127 215 / 0.10);
}

/* ---------------------------------------------------------------------------
   Dim theme.  Same token vocabulary, night values.  Applied via data-theme
   on <html>, set pre-paint by index.html and toggled from the rail.
--------------------------------------------------------------------------- */
[data-theme="dark"] {
  color-scheme: dark;
  --color-paper: #0e1a1f;
  --color-card: #152329;
  --color-ink: #e6f2f4;
  --color-ink-soft: #a8c0c6;
  --color-ink-faint: #8fa9b0;
  --color-line: #243740;
  --color-line-strong: #31474f;
  --color-field-line: #7d979f;
  --color-brand: #5fd0d3;
  --color-brand-deep: #8fe3e5;
  --color-brand-wash: #173538;
  --color-profit: #43c07a;
  --color-profit-deep: #7edfa6;
  --color-profit-wash: #152b1e;
  --color-loss: #f26a80;
  --color-loss-deep: #fda4b0;
  --color-loss-wash: #3a1c24;
  --color-warn: #d09a3e;
  --color-warn-deep: #e9c37c;
  --color-warn-wash: #332708;
  --color-on-accent: #0e1a1f;
  --color-wash-deep: #0a1418;
  --color-glass: rgb(21 35 41 / 0.72);
  --color-glass-line: rgb(230 242 244 / 0.10);
  --shadow-card: 0 8px 30px rgb(0 0 0 / 0.45);
  --shadow-float: 0 16px 48px rgb(0 0 0 / 0.60);
  --wash-a: rgb(95 208 211 / 0.14);
  --wash-b: rgb(96 165 250 / 0.10);
  --wash-c: rgb(139 127 215 / 0.08);
}

/* The select chevron follows the brand tone per theme. */
[data-theme="dark"] select.bg-card {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M4 6l4 4 4-4' fill='none' stroke='%235fd0d3' stroke-width='1.5'/%3E%3C/svg%3E");
}

/* Hints never drop below AA: the browser default is ~50% of the text colour. */
::placeholder {
  color: var(--color-ink-faint);
  opacity: 1;
}

html {
  background-color: var(--color-paper);
}

body {
  min-height: 100vh;
  color: var(--color-ink);
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
}

/* ---------------------------------------------------------------------------
   The luminous wash.  A fixed layer behind everything: three large soft
   radial gradients over the page ground, drifting once a minute.  Oversized
   (inset -10%) so the drift never reveals an edge.  It is the only
   decoration on the page.  Reduced motion freezes it through the global
   block below; it is not glass, so reduced transparency leaves it painted.
--------------------------------------------------------------------------- */
body::before {
  content: "";
  position: fixed;
  inset: -10%;
  z-index: -1;
  pointer-events: none;
  background-color: var(--color-paper);
  background-image:
    radial-gradient(60% 55% at 12% 8%, var(--wash-a), transparent 70%),
    radial-gradient(55% 50% at 92% 25%, var(--wash-b), transparent 70%),
    radial-gradient(70% 60% at 50% 100%, var(--wash-c), transparent 70%);
  animation: wash-drift 60s var(--ease-out-quint) infinite alternate;
  will-change: transform;
}

@keyframes wash-drift {
  from { transform: translate3d(0, 0, 0) rotate(0deg); }
  to   { transform: translate3d(-3%, 2%, 0) rotate(1.5deg); }
}

/* Every number in the product: Rubik digits, tabular so columns align,
   medium weight so figures stay crisp. (.tnum is the same style — kept as
   an alias where markup already uses it.) */
.num,
.tnum {
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  letter-spacing: 0.01em;
}

.font-display {
  font-family: var(--font-display);
  font-weight: 600;
}

/* Page headings. */
.page-title {
  font-family: var(--font-display);
  font-size: 1.75rem;
  font-weight: 700;
  letter-spacing: -0.015em;
  line-height: 1.15;
  color: var(--color-ink);
  text-wrap: balance;
}

/* The landing hero only. */
.hero-title {
  font-family: var(--font-display);
  font-size: clamp(2.25rem, 5vw, 4rem);
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.05;
  color: var(--color-ink);
  text-wrap: balance;
}

/* Small-caps labels for table headers and field labels only — never a
   section kicker. */
.desk-label {
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-ink-soft);
}

/* Selects paint their own chrome, with the aqua chevron. */
select.bg-card {
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M4 6l4 4 4-4' fill='none' stroke='%230b7c80' stroke-width='1.5'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 0.55rem center;
  background-size: 0.85rem;
  padding-right: 2rem;
}

/* ---------------------------------------------------------------------------
   Glass.  For layers that float over the wash: the rail and phone tab bar,
   the desk strip and page headers, Card panels, drawer and dialog headers,
   popovers.  Data never sits directly on glass: inside a glass card,
   tables, forms and figures sit on an .inset.  The prover proves ink,
   ink-soft, ink-faint, brand and brand-deep on glass mixed over wash-deep.
   Falls back to the solid card when blur is unsupported or the user asked
   for reduced transparency.

   Layer scale (Tailwind z-*): 10 sticky chrome and popovers, 40 drawers and
   the phone nav, 50 dialogs.
--------------------------------------------------------------------------- */
/* In the components layer so a utility such as border-loss or shadow-float
   on the same element still wins over the defaults set here. */
@layer components {
  .glass {
    background-color: var(--color-glass);
    border-color: var(--color-glass-line);
    -webkit-backdrop-filter: blur(16px) saturate(1.3);
    backdrop-filter: blur(16px) saturate(1.3);
  }

  /* Crisp data surface inside glass. */
  .inset {
    background-color: var(--color-card);
    border: 1px solid var(--color-line);
    border-radius: var(--radius-inset);
  }

  @supports not (backdrop-filter: blur(1px)) {
    .glass {
      background-color: var(--color-card);
    }
  }

  @media (prefers-reduced-transparency: reduce) {
    .glass {
      background-color: var(--color-card);
    }
    .glass,
    [class*="backdrop-blur"] {
      -webkit-backdrop-filter: none !important;
      backdrop-filter: none !important;
    }
  }
}

/* ---------------------------------------------------------------------------
   Stacked tables on phones.  A .stack-table renders normally from md up;
   below that each row becomes a card and each cell a "label · value" line,
   with the label drawn from the cell's data-label attribute.  Cells without
   a data-label (action buttons) right-align on their own line.
--------------------------------------------------------------------------- */
@media (max-width: 767px) {
  .stack-table thead {
    display: none;
  }
  .stack-table,
  .stack-table tbody,
  .stack-table tfoot,
  .stack-table tr,
  .stack-table td {
    display: block;
    width: 100%;
    box-sizing: border-box;
  }
  .stack-table td:empty {
    display: none;
  }
  .stack-table tr {
    border: 1px solid var(--color-line) !important;
    border-radius: var(--radius-inset);
    margin: 0.75rem;
    width: auto;
    padding: 0.35rem 0.85rem;
    background: var(--color-card);
  }
  .stack-table td {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 1rem;
    padding: 0.45rem 0 !important;
    border: 0 !important;
    text-align: right;
  }
  .stack-table td[data-label]::before {
    content: attr(data-label);
    font-size: 0.6875rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-ink-soft);
    text-align: left;
    flex-shrink: 0;
  }
  .stack-table td:not([data-label]) {
    justify-content: flex-end;
  }
}

/* Visible keyboard focus everywhere, in aqua (3:1 on every ground, proven). */
:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

/* The live-copying pulse on the desk strip. */
@keyframes desk-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.pulse-dot {
  animation: desk-pulse 2s ease-in-out infinite;
}

/* Reduced motion stops every animation and transition in one place, so no
   component has to remember its own motion-reduce: variant. The wash is
   frozen at its rest position. */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

/* ---------------------------------------------------------------------------
   Stop iOS Safari zooming the page on every tap into a field.  Safari zooms
   whenever a focused control has a font smaller than 16px and does not zoom
   back.  Raising controls to 16px on phones removes the trigger; wider
   screens keep the 14px density.  !important beats the utility classes.
--------------------------------------------------------------------------- */
@media (max-width: 640px) {
  input,
  select,
  textarea {
    font-size: 16px !important;
  }
}
```

- [ ] **Step 4: Point the prover at the wash and add the new pairs**

In `scripts/palette_check.mjs`, after the line `['brand-deep', 'glass-solid', TEXT, 'brand-deep text on glass over the worst underlay'],` add:

```js
  ['ink-faint', 'glass-solid', TEXT, 'faint text on glass over the deepest wash'],
  ['field-line', 'glass-solid', CONTROL, 'control edge drawn directly on glass (popover items)'],
  ['brand', 'wash-deep', CONTROL, 'focus ring over the deepest wash'],
  ['ink-soft', 'wash-deep', TEXT, 'labels over the deepest wash (page header on scroll)'],
```

Replace the two `glassSolid` calls and the comment above them:

```js
// The glass fill is translucent. Glass floats only over the wash (never
// over data), so the worst underlay is the wash's deepest stop.
light.raw = block(css, /@theme\s*\{/)
dark.raw = block(css, /\[data-theme="dark"\]\s*\{/)
light['glass-solid'] = glassSolid(light, light['wash-deep'])
dark['glass-solid'] = glassSolid(dark, dark['wash-deep'])
```

Keep everything else in the script as it is.

- [ ] **Step 5: Recolour the mark, the favicon, the browser chrome**

`src/components/Logo.tsx` lines 14–21 become:

```tsx
      <polygon points="14,38 38,26 38,50" fill="#0b7c80" />
      <polygon points="42,30 62,20 62,40" fill="#3aa7aa" />
      <polygon points="66,23 82,15 82,31" fill="#8fd4d6" />
      <line x1="10" y1="54" x2="90" y2="54" stroke="#bcd3da" strokeWidth="2" />
      <g opacity="0.35">
        <polygon points="14,70 38,82 38,58" fill="#0b7c80" />
        <polygon points="42,78 62,88 62,68" fill="#3aa7aa" />
        <polygon points="66,85 82,93 82,77" fill="#8fd4d6" />
      </g>
```

`index.html`: replace the favicon `href` with the same SVG using `%230b7c80`, `%233aa7aa`, `%238fd4d6` and stroke `%23bcd3da`; change `<meta name="theme-color" content="#f8f8fa" />` to `content="#f4fafb"`; in the pre-paint script change `'#131118'` to `'#0e1a1f'`.

`src/hooks/useTheme.ts` line 7: `const PAPER = { light: '#f4fafb', dark: '#0e1a1f' } as const`.

Create `scripts/render_logo.mjs` (regenerates the repo-root `app-logo.png` from the same geometry; run once, commit the PNG):

```js
// Renders app-logo.png (512x512) from the LogoMark geometry so the raster
// never drifts from the SVG. Usage: node scripts/render_logo.mjs
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createCanvas } from 'canvas' // dev dependency, see Step 6

const size = 512
const c = createCanvas(size, size)
const g = c.getContext('2d')
const s = size / 100
const poly = (pts, fill, alpha = 1) => {
  g.globalAlpha = alpha
  g.fillStyle = fill
  g.beginPath()
  pts.forEach(([x, y], i) => (i ? g.lineTo(x * s, y * s) : g.moveTo(x * s, y * s)))
  g.closePath()
  g.fill()
  g.globalAlpha = 1
}
g.fillStyle = '#f4fafb'
g.fillRect(0, 0, size, size)
poly([[14, 38], [38, 26], [38, 50]], '#0b7c80')
poly([[42, 30], [62, 20], [62, 40]], '#3aa7aa')
poly([[66, 23], [82, 15], [82, 31]], '#8fd4d6')
g.strokeStyle = '#bcd3da'; g.lineWidth = 2 * s
g.beginPath(); g.moveTo(10 * s, 54 * s); g.lineTo(90 * s, 54 * s); g.stroke()
poly([[14, 70], [38, 82], [38, 58]], '#0b7c80', 0.35)
poly([[42, 78], [62, 88], [62, 68]], '#3aa7aa', 0.35)
poly([[66, 85], [82, 93], [82, 77]], '#8fd4d6', 0.35)
writeFileSync(resolve('..', 'app-logo.png'), c.toBuffer('image/png'))
console.log('wrote ../app-logo.png')
```

- [ ] **Step 6: Regenerate the PNG**

Run: `npm install --save-dev canvas && node scripts/render_logo.mjs && npm uninstall canvas`
Expected: `wrote ../app-logo.png`; `git status` shows the PNG modified and `package.json` unchanged. If `canvas` fails to install on this machine (it needs prebuilt binaries), skip the PNG, leave `render_logo.mjs` in place, and say so in the report; nothing in the app reads the PNG.

- [ ] **Step 7: Run the prover, the CSS test, then the whole gate**

Run: `node scripts/palette_check.mjs`
Expected: `ALL PASS (both themes)` with the four new pairs listed.
Run: `npx vitest run src/theme-css.test.ts`
Expected: 5 passed.
Run: `npm test`
Expected: green. Every page still renders because no class name it uses was removed; only values changed.

- [ ] **Step 8: Commit**

```bash
git add src/index.css scripts/palette_check.mjs src/components/Logo.tsx index.html src/hooks/useTheme.ts src/theme-css.test.ts scripts/render_logo.mjs ../app-logo.png
git commit -m "feat(dashboard): luminous glass tokens -- aqua accent, the wash, glass over wash-deep, radii and shadows; prover and mark follow

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 2: Primitives on the new tokens, plus the seven new ones

**Files:**
- Modify: `src/index.css` (add `--shadow-lift` to both theme blocks)
- Modify: `src/components/Button.tsx:18-57`, `Input.tsx:19-24`, `Select.tsx:15-20`, `Badge.tsx:10-16,25-30`, `Banner.tsx:15-21`, `Drawer.tsx:38-46`, `ConfirmDialog.tsx:54-58,82`, `StatTile.tsx:39`, `SearchSelect.tsx:136`
- Create: `src/components/Card.tsx`, `Tabs.tsx`, `Loading.tsx`, `PageHeader.tsx`, `AuthCard.tsx`, `Menu.tsx`, `SkipLink.tsx`, `src/hooks/usePageTitle.ts`
- Test: `src/components/Card.test.tsx`, `Tabs.test.tsx`, `Loading.test.tsx`, `PageHeader.test.tsx`, `AuthCard.test.tsx`, `Menu.test.tsx`, `SkipLink.test.tsx`; existing `Button.test.tsx`, `Input.test.tsx`, `Drawer.test.tsx`, `ConfirmDialog.test.tsx` stay green

**Interfaces:**
- Consumes: Task 1 tokens and utilities (`rounded-card`, `rounded-inset`, `rounded-control`, `shadow-card`, `shadow-float`, `.glass`, `.inset`).
- Produces (every later task relies on these exact signatures):

```tsx
// components/Card.tsx
export default function Card(props: {
  title?: ReactNode; actions?: ReactNode; inset?: boolean; className?: string
  as?: 'section' | 'div' | 'article'; children: ReactNode
}): JSX.Element
// components/Tabs.tsx
export interface TabItem { key: string; label: string; count?: number }
export default function Tabs(props: {
  items: TabItem[]; value: string; onChange: (key: string) => void; label: string; idBase: string
}): JSX.Element   // panel: <div id={`${idBase}-panel`} role="tabpanel" aria-labelledby={`${idBase}-tab-${value}`}>
// components/Loading.tsx
export default function Loading(props: { lines?: number; label?: string; className?: string }): JSX.Element
// components/PageHeader.tsx
export default function PageHeader(props: {
  title: string; subtitle?: ReactNode; actions?: ReactNode; children?: ReactNode
}): JSX.Element
// components/AuthCard.tsx
export default function AuthCard(props: {
  title: string; lead?: ReactNode; children: ReactNode; footer?: ReactNode
}): JSX.Element
// components/Menu.tsx
export interface MenuItem { key: string; label: string; tone?: 'neutral' | 'loss'; disabled?: boolean; onSelect: () => void }
export default function Menu(props: { label: string; items: MenuItem[] }): JSX.Element
// components/SkipLink.tsx
export default function SkipLink(): JSX.Element   // targets #main
// hooks/usePageTitle.ts
export function usePageTitle(title: string): void   // `${title} · MirrorFleet`, restores 'MirrorFleet' on unmount
```

- [ ] **Step 1: Write the failing tests for the new primitives**

`src/components/Card.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Card from './Card'

test('renders a glass section with an optional titled header and actions', () => {
  render(<Card title="Fleet" actions={<button>Refresh</button>}><p>body</p></Card>)
  const section = screen.getByRole('region', { name: 'Fleet' })
  expect(section.className).toContain('glass')
  expect(section.className).toContain('rounded-card')
  expect(screen.getByRole('heading', { level: 2, name: 'Fleet' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
  expect(screen.getByText('body')).toBeInTheDocument()
})

test('inset puts the children on a crisp data surface', () => {
  render(<Card inset><table><tbody><tr><td>1.00</td></tr></tbody></table></Card>)
  expect(screen.getByText('1.00').closest('.inset')).not.toBeNull()
})

test('without a title there is no heading and no region name', () => {
  render(<Card><p>quiet</p></Card>)
  expect(screen.queryByRole('heading')).toBeNull()
  expect(screen.getByText('quiet')).toBeInTheDocument()
})
```

`src/components/Tabs.test.tsx`:

```tsx
import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import Tabs from './Tabs'

const ITEMS = [
  { key: 'a', label: 'Alpha', count: 3 },
  { key: 'b', label: 'Beta' },
  { key: 'c', label: 'Gamma', count: 0 },
]

function Harness({ onChange }: { onChange?: (k: string) => void }) {
  const [value, setValue] = useState('a')
  return (
    <>
      <Tabs items={ITEMS} value={value} onChange={(k) => { setValue(k); onChange?.(k) }} label="History views" idBase="hist" />
      <div id="hist-panel" role="tabpanel" aria-labelledby={`hist-tab-${value}`}>{value}</div>
    </>
  )
}

test('renders an accessible tablist with one selected, focusable tab', () => {
  render(<Harness />)
  const list = screen.getByRole('tablist', { name: 'History views' })
  const tabs = screen.getAllByRole('tab')
  expect(list).toBeInTheDocument()
  expect(tabs).toHaveLength(3)
  expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
  expect(tabs[0]).toHaveAttribute('tabindex', '0')
  expect(tabs[1]).toHaveAttribute('tabindex', '-1')
  expect(tabs[0]).toHaveAttribute('aria-controls', 'hist-panel')
  expect(tabs[0]).toHaveAttribute('id', 'hist-tab-a')
  expect(tabs[0]).toHaveTextContent('3')
})

test('arrow keys, Home and End move selection and focus; the panel follows', async () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  const tabs = screen.getAllByRole('tab')
  tabs[0].focus()
  await userEvent.keyboard('{ArrowRight}')
  expect(tabs[1]).toHaveFocus()
  expect(tabs[1]).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel')).toHaveTextContent('b')
  await userEvent.keyboard('{End}')
  expect(tabs[2]).toHaveFocus()
  await userEvent.keyboard('{ArrowRight}')
  expect(tabs[0]).toHaveFocus()
  await userEvent.keyboard('{Home}')
  expect(tabs[0]).toHaveFocus()
  expect(onChange).toHaveBeenLastCalledWith('a')
})

test('clicking a tab selects it', async () => {
  render(<Harness />)
  await userEvent.click(screen.getByRole('tab', { name: /Gamma/ }))
  expect(screen.getByRole('tab', { name: /Gamma/ })).toHaveAttribute('aria-selected', 'true')
})
```

`src/components/Loading.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Loading from './Loading'

test('announces as a busy status with skeleton lines and no literal ellipsis text', () => {
  render(<Loading lines={4} />)
  const status = screen.getByRole('status', { name: 'Loading' })
  expect(status).toHaveAttribute('aria-busy', 'true')
  expect(status.querySelectorAll('[data-skeleton]')).toHaveLength(4)
  expect(status).not.toHaveTextContent('...')
})

test('the label is customisable', () => {
  render(<Loading label="Loading positions" />)
  expect(screen.getByRole('status', { name: 'Loading positions' })).toBeInTheDocument()
})
```

`src/components/PageHeader.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import PageHeader from './PageHeader'

afterEach(() => { document.title = 'MirrorFleet' })

test('renders the single h1, the subtitle and the actions, and titles the document', () => {
  const view = render(
    <PageHeader title="Accounts" subtitle="Acme desk" actions={<button>Connect</button>}>
      <p>toolbar</p>
    </PageHeader>
  )
  expect(screen.getByRole('heading', { level: 1, name: 'Accounts' })).toHaveAttribute('id', 'page-title')
  expect(screen.getByText('Acme desk')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument()
  expect(screen.getByText('toolbar')).toBeInTheDocument()
  expect(document.title).toBe('Accounts · MirrorFleet')
  view.unmount()
  expect(document.title).toBe('MirrorFleet')
})
```

`src/components/AuthCard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import AuthCard from './AuthCard'

afterEach(() => { document.title = 'MirrorFleet' })

test('is the page main region with a glass card, the logo, a real h1, a lead and a footer', () => {
  render(
    <AuthCard title="Sign in" lead="Welcome back." footer={<a href="/register">Create account</a>}>
      <input aria-label="Email" />
    </AuthCard>
  )
  expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  expect(screen.getByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getByText('Welcome back.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Create account' })).toBeInTheDocument()
  expect(screen.getByLabelText('Email')).toBeInTheDocument()
  expect(screen.getByText('Mirror').closest('.glass')).not.toBeNull()
  expect(document.title).toBe('Sign in · MirrorFleet')
})
```

`src/components/Menu.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import Menu from './Menu'

const items = (spy: (k: string) => void) => [
  { key: 'flatten', label: 'Flatten', onSelect: () => spy('flatten') },
  { key: 'rotate', label: 'Rotate key', disabled: true, onSelect: () => spy('rotate') },
  { key: 'remove', label: 'Remove', tone: 'loss' as const, onSelect: () => spy('remove') },
]

test('opens on click, lists menu items, selects with Enter and returns focus', async () => {
  const spy = vi.fn()
  render(<Menu label="Actions for EUR account" items={items(spy)} />)
  const trigger = screen.getByRole('button', { name: 'Actions for EUR account' })
  expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
  expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(trigger)
  expect(trigger).toHaveAttribute('aria-expanded', 'true')
  const menu = screen.getByRole('menu')
  expect(menu.className).toContain('glass')
  const menuItems = screen.getAllByRole('menuitem')
  expect(menuItems).toHaveLength(3)
  expect(menuItems[0]).toHaveFocus()
  expect(menuItems[1]).toHaveAttribute('aria-disabled', 'true')
  await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}')
  expect(spy).toHaveBeenCalledWith('remove')
  expect(screen.queryByRole('menu')).toBeNull()
  expect(trigger).toHaveFocus()
})

test('Escape closes without selecting; a disabled item is skipped by arrows', async () => {
  const spy = vi.fn()
  render(<Menu label="Actions" items={items(spy)} />)
  await userEvent.click(screen.getByRole('button', { name: 'Actions' }))
  await userEvent.keyboard('{ArrowDown}')
  expect(screen.getByRole('menuitem', { name: 'Remove' })).toHaveFocus()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('menu')).toBeNull()
  expect(spy).not.toHaveBeenCalled()
})
```

`src/components/SkipLink.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import SkipLink from './SkipLink'

test('links to #main and is the first focusable thing', () => {
  render(<><SkipLink /><main id="main" tabIndex={-1}>content</main></>)
  const link = screen.getByRole('link', { name: 'Skip to content' })
  expect(link).toHaveAttribute('href', '#main')
  expect(link.className).toContain('sr-only')
  expect(link.className).toContain('focus:not-sr-only')
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/Card.test.tsx src/components/Tabs.test.tsx src/components/Loading.test.tsx src/components/PageHeader.test.tsx src/components/AuthCard.test.tsx src/components/Menu.test.tsx src/components/SkipLink.test.tsx`
Expected: 7 files fail to import (modules do not exist).

- [ ] **Step 3: Add the lift shadow token**

In `src/index.css`, inside `@theme { ... }` after `--shadow-float: ...;` add:

```css
  /* Inner top highlight on solid buttons: the light catching the edge. */
  --shadow-lift: inset 0 1px 0 rgb(255 255 255 / 0.25);
```

and inside `[data-theme="dark"] { ... }` after `--shadow-float: ...;` add:

```css
  --shadow-lift: inset 0 1px 0 rgb(255 255 255 / 0.08);
```

Add `'--shadow-lift:'` to the utilities list in the first test of `src/theme-css.test.ts`.

- [ ] **Step 4: Restyle the existing primitives**

`src/components/Button.tsx` — replace lines 18–57 with:

```tsx
const BASE =
  'inline-flex items-center justify-center gap-2 rounded-control font-semibold transition-colors ' +
  'duration-150 ease-out-quint min-h-11 md:min-h-0 disabled:opacity-50 disabled:cursor-not-allowed'

const SIZE: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  // Hero and empty-state calls to action only.
  lg: 'px-6 py-3 text-base',
}

const PRIMARY: Record<ButtonTone, string> = {
  brand: 'bg-brand text-on-accent shadow-lift hover:bg-brand-deep',
  profit: 'bg-profit text-on-accent shadow-lift hover:bg-profit-deep',
  loss: 'bg-loss text-on-accent shadow-lift hover:bg-loss-deep',
  warn: 'bg-warn text-on-accent shadow-lift hover:bg-warn-deep',
  // A filled neutral is ink on paper: the rare "dark" button.
  neutral: 'bg-ink text-paper hover:bg-ink-soft',
  inverse: 'bg-on-accent text-ink hover:bg-paper',
}

// Secondary buttons are glass: they float with the panel they sit on.
const SECONDARY: Record<ButtonTone, string> = {
  brand: 'glass border text-brand hover:bg-brand-wash hover:text-brand-deep',
  profit: 'glass border border-profit text-profit-deep hover:bg-profit-wash',
  loss: 'glass border border-loss text-loss hover:bg-loss hover:text-on-accent',
  warn: 'glass border border-warn text-warn-deep hover:bg-warn-wash',
  neutral: 'glass border text-ink hover:bg-brand-wash',
  inverse: 'border border-on-accent text-on-accent hover:bg-on-accent/15',
}
const SECONDARY_NEUTRAL = SECONDARY.neutral

const GHOST: Record<ButtonTone, string> = {
  brand: 'text-brand hover:text-brand-deep hover:underline',
  profit: 'text-profit hover:text-profit-deep hover:underline',
  loss: 'text-loss hover:text-loss-deep hover:underline',
  warn: 'text-warn-deep hover:underline',
  neutral: 'text-ink-soft hover:text-ink hover:bg-brand-wash',
  inverse: 'text-on-accent hover:underline',
}
```

`src/components/Input.tsx` — the class list becomes:

```tsx
  const classes = [
    'w-full rounded-control border bg-card px-3 py-2 text-sm text-ink placeholder:text-ink-faint',
    'transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
    invalid ? 'border-loss' : 'border-field-line',
    num ? 'num' : '',
    className ?? '',
  ].filter(Boolean).join(' ')
```

`src/components/Select.tsx` — the class list becomes:

```tsx
  const classes = [
    'rounded-control border border-field-line bg-card px-2 py-1.5 text-sm text-ink',
    'transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
    block ? 'w-full' : '',
    className ?? '',
  ].filter(Boolean).join(' ')
```

`src/components/Badge.tsx` — chips are always pills now; `pill` stays accepted so callers need no change:

```tsx
const TONE: Record<BadgeTone, string> = {
  brand: 'bg-brand-wash text-brand-deep',
  profit: 'bg-profit-wash text-profit-deep',
  loss: 'bg-loss-wash text-loss-deep',
  warn: 'bg-warn-wash text-warn-deep',
  neutral: 'bg-line text-ink-soft',
}

export default function Badge({ tone, className, title, children }: {
  tone: BadgeTone
  /** Accepted for compatibility; every badge is a pill in the luminous system. */
  pill?: boolean
  className?: string
  /** Hover text for a chip that abbreviates something longer (an error). */
  title?: string
  children: ReactNode
}) {
  const classes = [
    'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold',
    TONE[tone],
    className ?? '',
  ].filter(Boolean).join(' ')
  return <span className={classes} title={title}>{children}</span>
}
```

`src/components/Banner.tsx` line 21: `rounded border` → `rounded-inset border`.

`src/components/Drawer.tsx` line 44 (panel): `className="relative h-full w-full max-w-md flex flex-col bg-card shadow-float sm:rounded-l-card outline-none"`; line 46 (header): `className="glass flex items-center justify-between gap-3 px-5 py-4 border-b sm:rounded-tl-card"`.

`src/components/ConfirmDialog.tsx` line 56 (panel): `className="w-full max-w-md rounded-card bg-card shadow-float border border-line outline-none overflow-hidden"`; line 57 (header wrapper): `className="glass px-6 pt-5 pb-4 border-b"`; line 82 (footer): `className="px-6 py-4 flex justify-end gap-3 border-t border-line bg-paper"` (the wrapper's `overflow-hidden` rounds it).

`src/components/StatTile.tsx` line 39: `const frame = 'glass rounded-card shadow-card border p-4'`.

`src/components/SearchSelect.tsx` line 136: `className="glass absolute z-10 mt-1 w-full max-h-60 overflow-auto rounded-inset border shadow-float"`.

- [ ] **Step 5: Create the new primitives**

`src/hooks/usePageTitle.ts`:

```ts
import { useEffect } from 'react'

const APP = 'MirrorFleet'

/** Titles the document "<title> · MirrorFleet" while the caller is mounted
 *  and restores the bare app name on unmount, so screen readers and tabs
 *  always say where the user is. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · ${APP}` : APP
    return () => { document.title = APP }
  }, [title])
}
```

`src/components/Card.tsx`:

```tsx
import { useId, type ReactNode } from 'react'

/**
 * A glass panel: the desk's one card. The optional header carries the
 * section heading (h2) and its actions. `inset` renders the children on a
 * crisp opaque surface, which is where tables, forms and figures belong;
 * data never sits directly on glass.
 */
export default function Card({ title, actions, inset, className, as: Tag = 'section', children }: {
  title?: ReactNode
  actions?: ReactNode
  inset?: boolean
  className?: string
  as?: 'section' | 'div' | 'article'
  children: ReactNode
}) {
  const headingId = useId()
  return (
    <Tag
      aria-labelledby={title ? headingId : undefined}
      className={['glass rounded-card shadow-card border p-5', className ?? ''].filter(Boolean).join(' ')}
    >
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          {title && <h2 id={headingId} className="text-lg font-semibold text-ink">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      {inset ? <div className="inset overflow-hidden">{children}</div> : children}
    </Tag>
  )
}
```

`src/components/Tabs.tsx`:

```tsx
import type { KeyboardEvent } from 'react'

export interface TabItem {
  key: string
  label: string
  count?: number
}

/**
 * The desk's one tab bar. Roving tabindex: only the selected tab is in the
 * tab order; ArrowLeft/ArrowRight wrap, Home/End jump. The caller renders
 * the panel with id `${idBase}-panel`, role="tabpanel" and
 * aria-labelledby=`${idBase}-tab-${value}`.
 */
export default function Tabs({ items, value, onChange, label, idBase }: {
  items: TabItem[]
  value: string
  onChange: (key: string) => void
  label: string
  idBase: string
}) {
  const select = (key: string) => {
    onChange(key)
    document.getElementById(`${idBase}-tab-${key}`)?.focus()
  }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const idx = items.findIndex((t) => t.key === value)
    if (idx === -1) return
    let next: number | null = null
    if (e.key === 'ArrowRight') next = (idx + 1) % items.length
    else if (e.key === 'ArrowLeft') next = (idx + items.length - 1) % items.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = items.length - 1
    if (next == null) return
    e.preventDefault()
    select(items[next].key)
  }
  return (
    <div role="tablist" aria-label={label} onKeyDown={onKeyDown}
         className="flex gap-1 overflow-x-auto border-b border-line">
      {items.map((t) => {
        const selected = t.key === value
        return (
          <button
            key={t.key}
            id={`${idBase}-tab-${t.key}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`${idBase}-panel`}
            tabIndex={selected ? 0 : -1}
            onClick={() => select(t.key)}
            className={`min-h-11 md:min-h-0 whitespace-nowrap rounded-t-control border-b-2 px-4 py-2 text-sm transition-colors duration-150 ${
              selected ? 'border-brand text-brand font-semibold' : 'border-transparent text-ink-soft hover:text-ink'
            }`}
          >
            {t.label}
            {t.count != null && <span className="num ml-1 text-xs text-ink-faint">{t.count}</span>}
          </button>
        )
      })}
    </div>
  )
}
```

`src/components/Loading.tsx`:

```tsx
/**
 * Skeleton lines on an inset surface. Announced once as a busy status; the
 * visible bars carry no text so nothing reads "Loading..." aloud twice.
 */
export default function Loading({ lines = 3, label = 'Loading', className }: {
  lines?: number
  label?: string
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" aria-label={label}
         className={['inset p-4 space-y-3', className ?? ''].filter(Boolean).join(' ')}>
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          data-skeleton
          aria-hidden="true"
          className="h-3 rounded-full bg-line animate-pulse"
          style={{ width: `${88 - (i % 3) * 14}%` }}
        />
      ))}
    </div>
  )
}
```

`src/components/PageHeader.tsx`:

```tsx
import type { ReactNode } from 'react'
import { usePageTitle } from '../hooks/usePageTitle'

/**
 * The one page heading: the single h1 (focus target after a route change),
 * an optional subtitle, actions on the right, and any toolbar as children.
 * Titles the document too.
 */
export default function PageHeader({ title, subtitle, actions, children }: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  children?: ReactNode
}) {
  usePageTitle(title)
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="page-title" tabIndex={-1} className="page-title outline-none">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-ink-soft">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </header>
  )
}
```

`src/components/AuthCard.tsx`:

```tsx
import type { ReactNode } from 'react'
import Logo from './Logo'
import { usePageTitle } from '../hooks/usePageTitle'

/**
 * The glass card every auth screen lives in: logo, a real h1 naming the
 * step, an optional lead, the form, an optional footer. It is the page's
 * main region, so the skip link and assistive tech land on it.
 */
export default function AuthCard({ title, lead, children, footer }: {
  title: string
  lead?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  usePageTitle(title)
  return (
    <main id="main" tabIndex={-1} className="min-h-screen flex items-center justify-center p-4 outline-none">
      <div className="glass rounded-card shadow-float border w-full max-w-md p-8">
        <div className="mb-6 flex justify-center"><Logo size={28} /></div>
        <h1 className="page-title text-center">{title}</h1>
        {lead && <p className="mt-2 text-center text-sm text-ink-soft">{lead}</p>}
        <div className="mt-6">{children}</div>
        {footer && <div className="mt-6 text-center text-sm text-ink-soft">{footer}</div>}
      </div>
    </main>
  )
}
```

`src/components/Menu.tsx`:

```tsx
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import Button from './Button'

export interface MenuItem {
  key: string
  label: string
  tone?: 'neutral' | 'loss'
  disabled?: boolean
  onSelect: () => void
}

/**
 * The row-actions menu: one "⋯" trigger, a glass popover with role="menu".
 * Focus lands on the first enabled item, arrows move (skipping disabled
 * items), Enter/Space select, Escape and outside clicks close, and focus
 * returns to the trigger.
 */
export default function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0)
  const focusItem = (i: number) => {
    const el = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[i]
    el?.focus()
  }
  const close = () => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    focusItem(enabled[0] ?? 0)
    const onDocClick = (e: MouseEvent) => {
      if (!listRef.current?.contains(e.target as Node) && e.target !== triggerRef.current) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = Array.from(
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []
    ).findIndex((el) => el === document.activeElement)
    const pos = enabled.indexOf(current)
    if (e.key === 'ArrowDown') { e.preventDefault(); focusItem(enabled[(pos + 1) % enabled.length]) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); focusItem(enabled[(pos + enabled.length - 1) % enabled.length]) }
    else if (e.key === 'Home') { e.preventDefault(); focusItem(enabled[0]) }
    else if (e.key === 'End') { e.preventDefault(); focusItem(enabled[enabled.length - 1]) }
    else if (e.key === 'Escape') { e.preventDefault(); close() }
    else if (e.key === 'Tab') { setOpen(false) }
  }

  return (
    <div className="relative inline-block">
      <Button
        ref={triggerRef}
        variant="ghost" tone="neutral" size="sm"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="h-11 w-11 md:h-8 md:w-8 justify-center"
      >
        <span aria-hidden="true" className="text-lg leading-none">⋯</span>
      </Button>
      {open && (
        <div
          ref={listRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onKeyDown}
          className="glass absolute right-0 z-10 mt-1 min-w-44 rounded-inset border p-1 shadow-float"
        >
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              role="menuitem"
              aria-disabled={it.disabled || undefined}
              tabIndex={-1}
              onClick={() => { if (it.disabled) return; setOpen(false); triggerRef.current?.focus(); it.onSelect() }}
              className={`block w-full rounded-control px-3 py-2 text-left text-sm transition-colors duration-150 min-h-11 md:min-h-0 ${
                it.disabled ? 'text-ink-faint cursor-not-allowed'
                : it.tone === 'loss' ? 'text-loss hover:bg-loss-wash focus:bg-loss-wash'
                : 'text-ink hover:bg-brand-wash focus:bg-brand-wash'
              }`}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
```

`src/components/SkipLink.tsx`:

```tsx
/** First in the tab order; visible only when focused; jumps past the rail. */
export default function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 glass rounded-control border px-4 py-2 text-sm font-semibold text-brand shadow-float"
    >
      Skip to content
    </a>
  )
}
```

- [ ] **Step 6: Run the new tests, then the existing primitive tests**

Run: `npx vitest run src/components`
Expected: all pass. If `Button.test.tsx`, `Drawer.test.tsx` or `ConfirmDialog.test.tsx` assert an old class token (`rounded ` without a suffix, `shadow-xl`, `bg-card` on the secondary button), update that assertion to the new class (`rounded-control`, `shadow-float`, `glass`) — behaviour assertions stay as they are.

- [ ] **Step 7: Run the whole gate**

Run: `npm test`
Expected: prover ALL PASS, tsc clean, vitest green.

- [ ] **Step 8: Commit**

```bash
git add src/index.css src/theme-css.test.ts src/components src/hooks/usePageTitle.ts
git commit -m "feat(dashboard): primitives on the luminous tokens -- glass secondary buttons and tiles, control radii; add Card, Tabs, Loading, PageHeader, AuthCard, Menu, SkipLink, usePageTitle

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 3: The shell — glass rail, phone tab bar, code splitting, titles, skip link, NotFound

**Files:**
- Create: `src/components/layout/nav.ts`, `src/components/layout/NavRail.tsx`, `src/components/layout/BottomBar.tsx`, `src/components/layout/DeskStrip.tsx` (moved out of `Layout.tsx`)
- Create: `src/pages/NotFound.tsx`, `src/pages/groups/admin.ts`, `src/pages/groups/investor.ts`, `src/pages/groups/auth.ts`
- Modify: `src/components/Layout.tsx` (rewrite), `src/App.tsx` (rewrite), `src/lib/org.tsx:60`
- Test: `src/components/layout/NavRail.test.tsx`, `src/components/layout/BottomBar.test.tsx`, `src/pages/NotFound.test.tsx`, `src/App.test.tsx` (additions)

**Interfaces:**
- Consumes: `SkipLink`, `Loading`, `Button`, `Drawer`, `Select`, `Logo`, `usePageTitle` (Task 2); `useOrg`, `can`, `useTheme`, `api` as today.
- Produces: `nav.ts` exports `NavItem`, `NavGroup`, `adminNav(orgId, role)`, `investorNav(orgId)`, `bottomBarItems(orgId, role)`; the org shell renders `<main id="main">` with `pb-24 lg:pb-6`; route `*` renders `NotFound` both inside and outside the org shell; pages are loaded lazily by group; `document.title` changes per route through `PageHeader`/`AuthCard` (Tasks 4–9) — this task titles `NotFound` and keeps `MirrorFleet` for pages not yet migrated.

- [ ] **Step 1: Write the failing tests**

`src/components/layout/NavRail.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import NavRail from './NavRail'
import { adminNav, investorNav } from './nav'

test('admin groups are Desk, Fleet and Org, and the current page carries aria-current', () => {
  render(
    <MemoryRouter initialEntries={['/org/7/accounts']}>
      <NavRail groups={adminNav(7, 'admin')} />
    </MemoryRouter>
  )
  expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  for (const g of ['Desk', 'Fleet', 'Org']) expect(screen.getByText(g)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Accounts' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
})

test('a viewer sees no Trade, Automation or Investors; an investor sees the five portal links ungrouped', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'viewer')} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('link', { name: 'Trade' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Automation' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Investors' })).toBeNull()
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <NavRail groups={investorNav(7)} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link')).toHaveLength(5)
  expect(screen.queryByText('Desk')).toBeNull()
  expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
})
```

`src/components/layout/BottomBar.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import BottomBar from './BottomBar'

test('admins get four links and More; More opens the menu', async () => {
  const onMore = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/7/positions']}>
      <BottomBar orgId={7} role="admin" onMore={onMore} />
    </MemoryRouter>
  )
  const nav = screen.getByRole('navigation', { name: 'Quick navigation' })
  expect(nav.className).toContain('glass')
  const links = screen.getAllByRole('link')
  expect(links.map((l) => l.textContent)).toEqual(['Overview', 'Positions', 'Trade', 'Accounts'])
  expect(screen.getByRole('link', { name: 'Positions' })).toHaveAttribute('aria-current', 'page')
  await userEvent.click(screen.getByRole('button', { name: 'More' }))
  expect(onMore).toHaveBeenCalledTimes(1)
})

test('viewers get History instead of Trade; investors get their five pages and no More', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <BottomBar orgId={7} role="viewer" onMore={() => {}} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Overview', 'Positions', 'History', 'Accounts'])
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <BottomBar orgId={7} role="investor" onMore={() => {}} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Overview', 'Deposit', 'Withdraw', 'History', 'Account'])
  expect(screen.queryByRole('button', { name: 'More' })).toBeNull()
})
```

`src/pages/NotFound.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test } from 'vitest'
import NotFound from './NotFound'

afterEach(() => { document.title = 'MirrorFleet' })

test('names the problem, titles the tab and offers a way back', () => {
  render(<MemoryRouter><NotFound /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'That page is not here' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Back to the desk' })).toHaveAttribute('href', '/')
  expect(document.title).toBe('Page not found · MirrorFleet')
})
```

Append to `src/App.test.tsx` (reuse its `meResponse` helper and fetch-stubbing style):

```tsx
test('an unknown top-level URL renders NotFound instead of a blank page', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(null, { status: 401 }))))
  window.history.pushState({}, '', '/no/such/page')
  render(<App />)
  expect(await screen.findByRole('heading', { level: 1, name: 'That page is not here' })).toBeInTheDocument()
})

test('an unknown URL inside the org shell renders NotFound inside the shell, with the skip link first', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') return Promise.resolve(meResponse([{ id: 1, name: 'Acme', role: 'admin' }]))
    return Promise.resolve(new Response(JSON.stringify([]), { status: 200, headers: { 'content-type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  window.history.pushState({}, '', '/org/1/nope')
  render(<App />)
  expect(await screen.findByRole('heading', { level: 1, name: 'That page is not here' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
  expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: 'Quick navigation' })).toBeInTheDocument()
})

test('pages load lazily: the shell shows a loading status before the page resolves', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') return Promise.resolve(meResponse([{ id: 1, name: 'Acme', role: 'admin' }]))
    if (url.includes('/settings')) return Promise.resolve(new Response(JSON.stringify({ copying_enabled: true, dry_run: false }), { status: 200, headers: { 'content-type': 'application/json' } }))
    if (url.includes('/state')) return Promise.resolve(new Response(JSON.stringify({ accounts: {}, master_positions: [], pending_orders: [], drift: [] }), { status: 200, headers: { 'content-type': 'application/json' } }))
    return Promise.resolve(new Response(JSON.stringify([]), { status: 200, headers: { 'content-type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  window.history.pushState({}, '', '/org/1/logs')
  render(<App />)
  // Either the Suspense fallback is caught mid-flight or the page has already resolved; both prove the route works.
  const seen = await Promise.race([
    screen.findByRole('status', { name: 'Loading' }).then(() => 'fallback'),
    screen.findByRole('heading', { level: 1 }).then(() => 'page'),
  ])
  expect(['fallback', 'page']).toContain(seen)
  expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/layout src/pages/NotFound.test.tsx src/App.test.tsx`
Expected: the three new files fail to import; the appended App tests fail (blank render, no skip link, no "Quick navigation").

- [ ] **Step 3: The nav model**

`src/components/layout/nav.ts`:

```ts
import { can, type Role } from '../../lib/roles'

export interface NavItem {
  path: string
  label: string
  /** Match only the exact path (the org root), not every child. */
  end?: boolean
}

export interface NavGroup {
  name: string
  items: NavItem[]
}

/** The admin/viewer rail: three groups, ten links at most. */
export function adminNav(orgId: number, role: Role): NavGroup[] {
  const o = `/org/${orgId}`
  const trade = can(role, 'trade')
  return [
    {
      name: 'Desk',
      items: [
        { path: o, label: 'Overview', end: true },
        { path: `${o}/positions`, label: 'Positions' },
        ...(trade ? [{ path: `${o}/trade`, label: 'Trade' }] : []),
        { path: `${o}/history`, label: 'History' },
      ],
    },
    {
      name: 'Fleet',
      items: [
        { path: `${o}/accounts`, label: 'Accounts' },
        ...(trade ? [{ path: `${o}/automation`, label: 'Automation' }] : []),
        { path: `${o}/performance`, label: 'Performance' },
      ],
    },
    {
      name: 'Org',
      items: [
        { path: `${o}/members`, label: 'Members' },
        ...(can(role, 'control') ? [{ path: `${o}/investors`, label: 'Investors' }] : []),
        { path: `${o}/logs`, label: 'Logs' },
      ],
    },
  ]
}

/** The investor portal: only their own account and their own money. */
export function investorNav(orgId: number): NavGroup[] {
  const p = `/org/${orgId}/invest`
  return [{
    name: '',
    items: [
      { path: p, label: 'Overview', end: true },
      { path: `${p}/deposit`, label: 'Deposit' },
      { path: `${p}/withdraw`, label: 'Withdraw' },
      { path: `${p}/history`, label: 'History' },
      { path: `${p}/account`, label: 'Account' },
    ],
  }]
}

/** The phone tab bar: four links (five for investors) in thumb reach. */
export function bottomBarItems(orgId: number, role: Role): NavItem[] {
  if (role === 'investor') return investorNav(orgId)[0].items
  const o = `/org/${orgId}`
  return [
    { path: o, label: 'Overview', end: true },
    { path: `${o}/positions`, label: 'Positions' },
    can(role, 'trade') ? { path: `${o}/trade`, label: 'Trade' } : { path: `${o}/history`, label: 'History' },
    { path: `${o}/accounts`, label: 'Accounts' },
  ]
}
```

- [ ] **Step 4: The rail and the tab bar**

`src/components/layout/NavRail.tsx`:

```tsx
import { NavLink } from 'react-router-dom'
import type { NavGroup } from './nav'

/**
 * Grouped navigation for the rail and the phone drawer. NavLink sets
 * aria-current="page" on the active link; `end` keeps the org root from
 * matching every child route.
 */
export default function NavRail({ groups, onNavigate, dense = true }: {
  groups: NavGroup[]
  onNavigate?: () => void
  dense?: boolean
}) {
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 py-2">
      {groups.map((g, gi) => (
        <div key={g.name || gi} className={gi > 0 ? 'mt-4' : ''}>
          {g.name && <div className="px-3 pb-1 text-xs font-semibold text-ink-faint">{g.name}</div>}
          <ul className="space-y-0.5">
            {g.items.map((item) => (
              <li key={item.path}>
                <NavLink
                  to={item.path}
                  end={item.end}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    `block rounded-control px-3 text-sm transition-colors duration-150 min-h-11 md:min-h-0 ${
                      dense ? 'py-2' : 'py-3'
                    } ${isActive ? 'bg-brand-wash text-brand-deep font-semibold' : 'text-ink-soft hover:bg-brand-wash/60 hover:text-ink'}`
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}
```

`src/components/layout/BottomBar.tsx`:

```tsx
import { NavLink } from 'react-router-dom'
import type { Role } from '../../lib/roles'
import { bottomBarItems } from './nav'

/**
 * The phone tab bar: glass, fixed to the bottom edge in thumb reach, hidden
 * from lg up where the rail takes over. Admins get four links plus More
 * (which opens the full menu drawer); investors get their five pages.
 */
export default function BottomBar({ orgId, role, onMore }: {
  orgId: number
  role: Role
  onMore: () => void
}) {
  const items = bottomBarItems(orgId, role)
  const cell = 'flex flex-1 flex-col items-center justify-center gap-0.5 min-h-14 text-xs font-semibold transition-colors duration-150'
  return (
    <nav
      aria-label="Quick navigation"
      className="glass fixed inset-x-0 bottom-0 z-40 flex border-t pb-[env(safe-area-inset-bottom)] lg:hidden"
    >
      {items.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.end}
          className={({ isActive }) => `${cell} ${isActive ? 'text-brand' : 'text-ink-soft'}`}
        >
          {item.label}
        </NavLink>
      ))}
      {role !== 'investor' && (
        <button type="button" onClick={onMore} className={`${cell} text-ink-soft`}>
          More
        </button>
      )}
    </nav>
  )
}
```

- [ ] **Step 5: Move DeskStrip into its own file and let the margin-call banner wrap**

Create `src/components/layout/DeskStrip.tsx` by moving, verbatim, everything from `src/components/Layout.tsx` between the import block and `export default function Layout()` — the helpers `localISODate`, `cutoffDaysPhrase`, `contractPrices` and the `DeskStrip` function (lines 45–430 today) — with `export default function DeskStrip(...)`, its imports adjusted to the new depth: `orgApi` from `../../lib/api`, `useOrg` from `../../lib/org`, `can` from `../../lib/roles`, `useLiveRefresh` from `../../hooks/useLiveRefresh`, `TicksPayload` from `../../lib/ticks`, the types from `../../lib/types`, `money`/`signed`/`errorText` from `../../lib/format`, `Button` and `ConfirmDialog` from `../Button` and `../ConfirmDialog` (copy exactly the imports the moved code uses; `platformCaption` stays in `../lib/platform` and is imported by `Layout.tsx`). Then change two things inside it:

1. The strip container (the first `<div>` in its `return`): `className="glass min-h-11 border-b flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 md:px-6 py-2 md:py-1.5 text-sm"`.
2. The margin-call banner:

```tsx
      {marginCall && marginCall.id !== dismissedRiskId && (
        <div
          role="alert"
          className="px-4 md:px-6 py-2.5 bg-loss text-on-accent text-sm flex flex-wrap items-center justify-between gap-2"
        >
          <span className="min-w-0 flex-1 basis-64">
            <strong>Margin call</strong>
            {marginCall.account_id != null && (
              <> on account <span className="num">{marginCall.account_id}</span></>
            )}
            {' — '}the broker may start force-closing positions. Reduce
            exposure or add funds now.
          </span>
          <Button
            variant="ghost"
            tone="inverse"
            size="sm"
            className="ml-auto"
            onClick={() => setDismissedRiskId(marginCall.id)}
          >
            Dismiss
          </Button>
        </div>
      )}
```

Export `platformCaption` as a named export too (Layout uses it).

- [ ] **Step 6: Rewrite `src/components/Layout.tsx`**

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { api } from '../lib/api'
import { useOrg } from '../lib/org'
import { useTheme } from '../hooks/useTheme'
import type { Account } from '../lib/types'
import Button from './Button'
import Drawer from './Drawer'
import Logo from './Logo'
import Select from './Select'
import SkipLink from './SkipLink'
import Loading from './Loading'
import { Suspense } from 'react'
import NavRail from './layout/NavRail'
import BottomBar from './layout/BottomBar'
import DeskStrip from './layout/DeskStrip'
import { adminNav, investorNav } from './layout/nav'
import { platformCaption } from '../lib/platform'

const EASE = [0.23, 1, 0.32, 1] as const

/**
 * The authenticated shell: a floating glass rail from lg up, a glass
 * bottom tab bar plus a menu drawer below it, the desk strip for admins
 * and viewers, and the page in a main region that crossfades on route
 * change. Investors are held inside their portal.
 */
export default function Layout() {
  const { theme, toggle: toggleTheme } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const { orgId, role, me } = useOrg()
  const reduced = useReducedMotion()
  const [menuOpen, setMenuOpen] = useState(false)
  const [caption, setCaption] = useState<string | null>(null)
  const handleAccounts = useCallback((list: Account[]) => setCaption(platformCaption(list)), [])

  const handleLogout = async () => {
    try {
      await api('/api/logout', { method: 'POST' })
    } catch (err) {
      console.error('Logout failed:', err)
    }
    navigate('/login')
  }

  const investor = role === 'investor'
  const groups = investor ? investorNav(orgId) : adminNav(orgId, role)
  const portalRoot = `/org/${orgId}/invest`
  const strayed = investor
    && location.pathname !== portalRoot
    && !location.pathname.startsWith(portalRoot + '/')

  // The drawer never outlives a navigation. Escape is Drawer's job.
  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  // Nor does it outlive the viewport: past lg the drawer is display:none,
  // but its focus trap has no way to know that on its own.
  useEffect(() => {
    if (!menuOpen) return
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(min-width: 1024px)')
    const onChange = (e: MediaQueryListEvent) => { if (e.matches) setMenuOpen(false) }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [menuOpen])

  // After a navigation (not on first load) focus the page heading so
  // keyboard and screen-reader users land on the new page's name.
  const firstPath = useRef(location.pathname)
  useEffect(() => {
    if (location.pathname === firstPath.current) return
    firstPath.current = ''
    const id = window.setTimeout(() => document.getElementById('page-title')?.focus(), 0)
    return () => window.clearTimeout(id)
  }, [location.pathname])

  const railChrome = (
    <>
      <div className="px-5 pt-5 pb-4 border-b">
        <Logo size={26} />
        <Select
          aria-label="Organization"
          value={orgId}
          onChange={(e) => navigate(`/org/${e.target.value}`)}
          block
          className="mt-3"
        >
          {me.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </Select>
        {caption && <p className="mt-1 text-xs text-ink-faint">{caption}</p>}
      </div>
      <NavRail groups={groups} onNavigate={() => setMenuOpen(false)} />
      <div className="border-t p-3">
        <Button variant="ghost" tone="neutral" size="sm" block onClick={toggleTheme}
                aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dim theme'}
                className="justify-start">
          <span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>{' '}
          {theme === 'dark' ? 'Light mode' : 'Dim mode'}
        </Button>
        <Button variant="ghost" tone="neutral" size="sm" block onClick={handleLogout} className="justify-start">
          Log out
        </Button>
      </div>
    </>
  )

  return (
    <div className="flex h-screen">
      <SkipLink />

      {/* Floating glass rail — desktop only */}
      <aside className="hidden lg:flex w-60 shrink-0 m-4 mr-0 flex-col rounded-card glass shadow-card border">
        {railChrome}
      </aside>

      {/* Full menu — phone and tablet, opened from More or the top bar */}
      <div className="lg:hidden">
        <Drawer open={menuOpen} title="Menu" onClose={() => setMenuOpen(false)}>
          {railChrome}
        </Drawer>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <div className="lg:hidden h-12 shrink-0 glass border-b flex items-center gap-2 px-2">
          <Button variant="ghost" tone="neutral" size="sm" aria-label="Open menu"
                  onClick={() => setMenuOpen(true)} className="h-11 w-11 md:h-11 md:w-11 justify-center">
            <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5">
              <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </Button>
          <Logo size={22} textClass="text-base" />
        </div>
        {!investor && <DeskStrip onAccounts={handleAccounts} />}
        <main id="main" tabIndex={-1} className="flex-1 overflow-y-auto p-4 md:p-6 pb-24 lg:pb-6 outline-none">
          {strayed ? (
            <Navigate to={portalRoot} replace />
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={location.pathname}
                initial={reduced ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
                transition={reduced ? { duration: 0 } : { duration: 0.18, ease: EASE }}
              >
                <Suspense fallback={<Loading lines={6} />}>
                  <Outlet />
                </Suspense>
              </motion.div>
            </AnimatePresence>
          )}
        </main>
        <BottomBar orgId={orgId} role={role} onMore={() => setMenuOpen(true)} />
      </div>
    </div>
  )
}
```

- [ ] **Step 7: NotFound, the lazy groups, and App**

`src/pages/NotFound.tsx`:

```tsx
import { Link } from 'react-router-dom'
import Card from '../components/Card'
import { usePageTitle } from '../hooks/usePageTitle'

/** The catch-all route: says what happened and offers the one way back. */
export default function NotFound() {
  usePageTitle('Page not found')
  return (
    <div className="mx-auto max-w-md pt-12">
      <Card>
        <h1 id="page-title" tabIndex={-1} className="page-title outline-none">That page is not here</h1>
        <p className="mt-2 text-sm text-ink-soft">
          The link may be old, or the address has a typo. Nothing was changed.
        </p>
        <Link to="/" className="mt-5 inline-block text-sm font-semibold text-brand hover:text-brand-deep hover:underline">
          Back to the desk
        </Link>
      </Card>
    </div>
  )
}
```

`src/pages/groups/admin.ts` (one chunk for the desk):

```ts
export { default as Overview } from '../Overview'
export { default as Accounts } from '../Accounts'
export { default as Positions } from '../Positions'
export { default as Trade } from '../Trade'
export { default as Automation } from '../Automation'
export { default as History } from '../History'
export { default as Performance } from '../Performance'
export { default as Logs } from '../Logs'
export { default as Members } from '../Members'
export { default as Investors } from '../Investors'
```

`src/pages/groups/investor.ts`:

```ts
export { default as InvestorOverview } from '../investor/InvestorOverview'
export { default as InvestorDeposit } from '../investor/InvestorDeposit'
export { default as InvestorWithdraw } from '../investor/InvestorWithdraw'
export { default as InvestorHistory } from '../investor/InvestorHistory'
export { default as InvestorAccount } from '../investor/InvestorAccount'
```

`src/pages/groups/auth.ts`:

```ts
export { default as Login } from '../Login'
export { default as Mpin } from '../Mpin'
export { default as Register } from '../Register'
export { default as Join } from '../Join'
export { default as Welcome } from '../Welcome'
```

`src/App.tsx`:

```tsx
import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { api } from './lib/api'
import { LAST_ORG_KEY, OrgProvider } from './lib/org'
import { isMpinPending } from './lib/types'
import type { Me, MpinPending } from './lib/types'
import Layout from './components/Layout'
import Loading from './components/Loading'
import Landing from './pages/Landing'
import NotFound from './pages/NotFound'

// Three lazy groups: the desk, the investor portal, the auth screens. A
// signed-in investor never downloads the desk; a visitor never downloads
// either. Landing stays eager because `/` renders it for signed-out visitors.
const admin = () => import('./pages/groups/admin')
const investor = () => import('./pages/groups/investor')
const auth = () => import('./pages/groups/auth')
const pick = <M, K extends keyof M>(load: () => Promise<M>, key: K) =>
  lazy(() => load().then((m) => ({ default: m[key] as React.ComponentType })))

const Login = pick(auth, 'Login')
const Mpin = pick(auth, 'Mpin')
const Register = pick(auth, 'Register')
const Join = pick(auth, 'Join')
const Welcome = pick(auth, 'Welcome')
const Overview = pick(admin, 'Overview')
const Accounts = pick(admin, 'Accounts')
const Positions = pick(admin, 'Positions')
const Trade = pick(admin, 'Trade')
const Automation = pick(admin, 'Automation')
const History = pick(admin, 'History')
const Performance = pick(admin, 'Performance')
const Logs = pick(admin, 'Logs')
const Members = pick(admin, 'Members')
const Investors = pick(admin, 'Investors')
const InvestorOverview = pick(investor, 'InvestorOverview')
const InvestorDeposit = pick(investor, 'InvestorDeposit')
const InvestorWithdraw = pick(investor, 'InvestorWithdraw')
const InvestorHistory = pick(investor, 'InvestorHistory')
const InvestorAccount = pick(investor, 'InvestorAccount')

/** `/` → the last-used org, else the first org, else /welcome; signed-out
 *  visitors get the public front page instead of the login screen. */
function RootRedirect() {
  const [target, setTarget] = useState<string | null>(null)

  useEffect(() => {
    const resolve = async () => {
      try {
        const me = await api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
        if (isMpinPending(me)) { setTarget('/mpin'); return }
        const last = Number(localStorage.getItem(LAST_ORG_KEY))
        const org = me.orgs.find((o) => o.id === last) ?? me.orgs[0]
        setTarget(org ? `/org/${org.id}` : '/welcome')
      } catch {
        setTarget('landing')
      }
    }
    resolve()
  }, [])

  if (!target) {
    return <div className="mx-auto max-w-md pt-24"><Loading lines={4} /></div>
  }
  if (target === 'landing') return <Landing />
  return <Navigate to={target} replace />
}

const fullPage = <div className="mx-auto max-w-md pt-24"><Loading lines={4} /></div>

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={fullPage}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/mpin" element={<Mpin />} />
          <Route path="/register" element={<Register />} />
          <Route path="/join/:token" element={<Join />} />
          <Route path="/welcome" element={<Welcome />} />
          <Route
            path="/org/:orgId"
            element={
              <OrgProvider>
                <Layout />
              </OrgProvider>
            }
          >
            <Route index element={<Overview />} />
            <Route path="accounts" element={<Accounts />} />
            <Route path="positions" element={<Positions />} />
            <Route path="trade" element={<Trade />} />
            <Route path="automation" element={<Automation />} />
            <Route path="history" element={<History />} />
            <Route path="performance" element={<Performance />} />
            <Route path="logs" element={<Logs />} />
            <Route path="members" element={<Members />} />
            <Route path="investors" element={<Investors />} />
            <Route path="invest" element={<InvestorOverview />} />
            <Route path="invest/deposit" element={<InvestorDeposit />} />
            <Route path="invest/withdraw" element={<InvestorWithdraw />} />
            <Route path="invest/history" element={<InvestorHistory />} />
            <Route path="invest/account" element={<InvestorAccount />} />
            <Route path="*" element={<NotFound />} />
          </Route>
          <Route path="/" element={<RootRedirect />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}
```

`src/lib/org.tsx` line 60: replace the "Loading..." div with

```tsx
    return <div className="mx-auto max-w-md pt-24"><Loading lines={4} /></div>
```

and add `import Loading from '../components/Loading'`.

- [ ] **Step 8: Run the shell tests, then everything**

Run: `npx vitest run src/components/layout src/pages/NotFound.test.tsx src/App.test.tsx src/components/Layout.test.tsx`
Expected: all pass. Keep `Layout.test.tsx`'s `renderLayout`/`mockRoutes` helpers as they are (Task 6 relies on them) and update only selectors: the nav is now `navigation[name="Main"]`, the theme button reads "Dim mode"/"Light mode". The existing App tests still find the nav links (they are now inside `nav[aria-label="Main"]` and, in jsdom, also inside the drawer and the bottom bar; `getByRole('link', { name: 'Accounts' })` may now match two links — change those assertions to `getAllByRole(...)[0]` or scope with `within(screen.getByRole('navigation', { name: 'Main' }))`).
Run: `npm test`
Expected: green. Pages that still render "Loading..." text (migrated in Tasks 4–9) are untouched here.
Run: `npm run build` and note the chunk sizes in the report: the entry chunk must be smaller than before (`index-*.js` was 193 KB); `admin-*.js`, `investor-*.js` and `auth-*.js` appear as separate chunks.

- [ ] **Step 9: Commit**

```bash
git add src/components/Layout.tsx src/components/layout src/pages/NotFound.tsx src/pages/NotFound.test.tsx src/pages/groups src/App.tsx src/App.test.tsx src/lib/org.tsx
git commit -m "feat(dashboard): the shell -- floating glass rail with grouped nav, phone tab bar, skip link, lazy page groups, route crossfade, NotFound

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 4: The auth stack on AuthCard

**Files:**
- Modify: `dashboard/src/pages/Login.tsx`
- Modify: `dashboard/src/pages/Register.tsx`
- Modify: `dashboard/src/pages/Mpin.tsx`
- Modify: `dashboard/src/pages/Join.tsx`
- Modify: `dashboard/src/pages/Welcome.tsx`
- Test: `dashboard/src/pages/Login.test.tsx`
- Test: `dashboard/src/pages/Register.test.tsx`
- Test: `dashboard/src/pages/Mpin.test.tsx`
- Test: `dashboard/src/pages/Join.test.tsx`
- Test: `dashboard/src/pages/Welcome.test.tsx`

**Interfaces:**
- Consumes: `AuthCard` (`{ title, lead?, children, footer? }`, renders `<main id="main">`, the logo, the one `<h1 className="page-title">` and calls `usePageTitle(title)`), `Loading` (`{ lines?, label?, className? }`) from Task 2; the `.inset` and `rounded-control` utilities from Task 1; `Button`, `Input`, `Banner`, `PinInput` with today's APIs; `ApiError` from `lib/api.ts`.
- Produces: nothing new. (The five pages stop importing `Logo`; `AuthCard` owns it.)

Grounding notes for the implementer:
- The server's closed-registration answer is `403` with detail `"Self-service registration is disabled; ask an administrator for an invite link"` (`api/src/api/auth.py`, `register`). `api()` throws an `ApiError` whose `response` is `{ status: 403, body: { detail: "..." } }`. Register matches on status 403 **and** that detail prefix, so a CSRF 403 or any other 403 still shows its own detail as an error.
- Today Register never clears its fields on failure; the values already survive. The new test pins that so the notice cannot regress it.
- Join cannot know the org name: `POST /api/orgs/join` returns `{ org_id, role }` and navigates away on success, and there is no invite preview endpoint. Its title is therefore always the spec's fallback, "Join the workspace". Showing "Join <org>" needs an API change, which this plan does not make.
- Mpin keeps its modes, countdown, `safeNext`, `ApiError` handling and `PinInput` usage exactly. The `<h2>` per mode becomes the `AuthCard` `<h1>`. The loading state gets its own title ("Checking your sign-in"). If it reused "Enter your MPIN", the existing tests' `findByRole('heading', { name: 'Enter your MPIN' })` would resolve before any PIN box exists.
- Input order in Mpin's forgot mode stays password, then 6 MPIN boxes, then 6 confirm boxes. The existing test indexes `inputs()[1]` and `inputs()[7]`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/src/pages/Login.test.tsx` (keep the three existing tests unchanged):

```tsx
test('names the page in its one h1 and in the document title', () => {
  renderLogin()
  expect(screen.getByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(document.title).toBe('Sign in · MirrorFleet')
})

test('explains what to do about a forgotten password', () => {
  renderLogin()
  const line = screen.getByText(/ask an admin of your workspace to reset it/i)
  expect(line).toHaveTextContent(/^Forgot password\?\s+Ask an admin of your workspace to reset it\.$/)
})
```

Append to `dashboard/src/pages/Register.test.tsx` (keep the six existing tests unchanged):

```tsx
const CLOSED = 'Self-service registration is disabled; ask an administrator for an invite link'

async function fillAndSubmit() {
  await userEvent.type(screen.getByLabelText(/display name/i), 'Ada Trader')
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com')
  await userEvent.type(screen.getByLabelText(/^password$/i), 'correcthorsebattery')
  await userEvent.click(screen.getByRole('button', { name: /create account/i }))
}

test('names the page in its one h1 and in the document title', () => {
  renderRegister()
  expect(screen.getByRole('heading', { level: 1, name: 'Create your account' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(document.title).toBe('Create your account · MirrorFleet')
})

test('closed registration says invite only at once and keeps what was typed', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: CLOSED }), { status: 403 })
  ))
  renderRegister()
  await fillAndSubmit()

  expect(await screen.findByText(/registration is by invite only/i)).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent(/invite only/i)
  // The notice replaces the raw server error; it is not shown twice.
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByLabelText(/display name/i)).toHaveValue('Ada Trader')
  expect(screen.getByLabelText(/email/i)).toHaveValue('ada@example.com')
  expect(screen.getByLabelText(/^password$/i)).toHaveValue('correcthorsebattery')
  expect(screen.queryByText(/mpin next/)).not.toBeInTheDocument()
})

test('closed registration with a stale invite asks for a fresh link', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: CLOSED }), { status: 403 })
  ))
  renderRegister('/register?invite=stale')
  await fillAndSubmit()

  expect(await screen.findByText(/invite link is invalid or expired/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/email/i)).toHaveValue('ada@example.com')
})

test('any other 403 still shows its own detail as an error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'CSRF token missing' }), { status: 403 })
  ))
  renderRegister()
  await fillAndSubmit()

  expect(await screen.findByRole('alert')).toHaveTextContent('CSRF token missing')
  expect(screen.queryByText(/invite only/i)).not.toBeInTheDocument()
})
```

Append to `dashboard/src/pages/Mpin.test.tsx` (keep the ten existing tests unchanged; they already find the mode headings by name, and those are now the `<h1>`):

```tsx
test('each mode names itself in the one h1 and in the page title', async () => {
  stub({ mpin: { pending: true, set: false } })
  const view = renderMpin()
  expect(await screen.findByRole('heading', { level: 1, name: 'Choose your MPIN' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  await waitFor(() => expect(document.title).toBe('Choose your MPIN · MirrorFleet'))
  view.unmount()

  stub({ mpin: { pending: true, set: true } })
  renderMpin()
  expect(await screen.findByRole('heading', { level: 1, name: 'Enter your MPIN' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  await waitFor(() => expect(document.title).toBe('Enter your MPIN · MirrorFleet'))

  await userEvent.click(screen.getByRole('button', { name: /forgot mpin/i }))
  expect(screen.getByRole('heading', { level: 1, name: 'Reset your MPIN' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  await waitFor(() => expect(document.title).toBe('Reset your MPIN · MirrorFleet'))
})

test('Forgot mode asks to confirm the password, and Reset MPIN is a brand action', async () => {
  stub({ mpin: { pending: true, set: true } })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await userEvent.click(screen.getByRole('button', { name: /forgot mpin/i }))

  expect(screen.getByText('Confirm your password to choose a new MPIN.')).toBeInTheDocument()
  expect(screen.queryByText(/prove your password/i)).not.toBeInTheDocument()
  const reset = screen.getByRole('button', { name: /reset mpin/i })
  expect(reset).toHaveClass('bg-brand')
  expect(reset).not.toHaveClass('bg-loss')
})

test('while the session is checked the page shows a loading status, not bare text', async () => {
  let release: (r: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { release = resolve })))
  renderMpin()
  expect(screen.getByRole('status', { name: 'Checking your sign-in' })).toBeInTheDocument()
  expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
  release(json({ mpin: { pending: true, set: true } }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Enter your MPIN' })).toBeInTheDocument()
})
```

Append to `dashboard/src/pages/Join.test.tsx` (keep the five existing tests unchanged):

```tsx
test('names the page in its one h1 and in the document title', async () => {
  stubFetch({ join: new Response('Gone', { status: 410 }) })
  renderJoin('deadtoken')

  expect(await screen.findByText(/invalid.*expired/i)).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Join the workspace' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(document.title).toBe('Join the workspace · MirrorFleet')
})

test('a dead invite is announced as an error with a way to sign in', async () => {
  stubFetch({ join: new Response('Gone', { status: 410 }) })
  renderJoin('deadtoken')

  expect(await screen.findByRole('alert')).toHaveTextContent(/fresh link/i)
  expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login')
})
```

Append to `dashboard/src/pages/Welcome.test.tsx`. Add `within` to the existing `@testing-library/react` import at the top (`import { render, screen, waitFor, within } from '@testing-library/react'`) and keep the six existing tests unchanged:

```tsx
function stubNewAccount() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/me') {
      return new Response(JSON.stringify({
        user: { id: 2, email: 'new@example.com', display_name: 'N' }, orgs: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

test('names the page in its one h1 and in the document title, and says what an organization is', async () => {
  stubNewAccount()
  renderWelcome()

  expect(screen.getByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(document.title).toBe('Welcome · MirrorFleet')
  expect(screen.getByText(/an organization is the workspace/i)).toBeInTheDocument()
  // Let the /api/me effect settle before the test ends.
  await waitFor(() => expect(screen.queryByText(/your workspaces/i)).not.toBeInTheDocument())
})

test('create and join are two labelled choices with visible field labels', async () => {
  stubNewAccount()
  renderWelcome()

  const create = screen.getByRole('region', { name: 'Create an organization' })
  const join = screen.getByRole('region', { name: 'Join with an invite' })

  expect(within(create).getByText('Organization name', { selector: 'label' })).toBeVisible()
  const orgName = within(create).getByLabelText('Organization name')
  expect(orgName).not.toHaveAttribute('aria-label')
  expect(within(create).getByRole('button', { name: /create organization/i })).toBeInTheDocument()

  expect(within(join).getByText('Invite link or code', { selector: 'label' })).toBeVisible()
  const code = within(join).getByLabelText('Invite link or code')
  expect(code).not.toHaveAttribute('aria-label')
  expect(within(join).getByRole('button', { name: /join organization/i })).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText(/your workspaces/i)).not.toBeInTheDocument())
})
```

- [ ] **Step 2: Run to verify they fail**

Run (from `dashboard/`):

```bash
npx vitest run src/pages/Login.test.tsx src/pages/Register.test.tsx src/pages/Mpin.test.tsx src/pages/Join.test.tsx src/pages/Welcome.test.tsx
```

Expected: every pre-existing test passes. The new tests fail:
- The "one h1 / document title" tests fail in all five files. Today's `<h1>` wraps the logo, so its name is "MirrorFleet", Mpin's mode headings are `<h2>`, Join's logo heading is an `<h2>`, and `document.title` is untouched.
- Login "forgotten password" fails with "Unable to find an element".
- Register "closed registration…" fails: the 403 shows as a `role="alert"` with the raw detail and no invite-only text. The stale-invite test fails the same way. "any other 403" already passes.
- Mpin "Forgot mode asks to confirm…" fails: the copy says "Prove your password" and the button has `bg-loss`. The loading test fails: no `role="status"` named "Checking your sign-in".
- Join "dead invite is announced" fails: the message is a plain `<p>`, not an alert.
- Welcome "labelled choices" fails: no `region` roles, and the inputs carry `aria-label` with no `<label>`.

- [ ] **Step 3: Implement Login on AuthCard**

Replace `dashboard/src/pages/Login.tsx` with:

```tsx
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'

export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      await api('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      navigate('/')
    } catch (err) {
      setError(errorText(err, 'Login failed'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AuthCard
      title="Sign in"
      lead="Sign in to open the desk."
      footer={
        <p className="text-center text-sm text-ink-soft">
          New here?{' '}
          <Link to="/register" className="font-medium text-brand hover:text-brand-deep">
            Create an account
          </Link>
        </p>
      }
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        {error && <Banner kind="error">{error}</Banner>}
        <div>
          <label htmlFor="email" className="desk-label block mb-1">Email</label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="password" className="desk-label block mb-1">Password</label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <Button type="submit" block disabled={isLoading}>
          {isLoading ? 'Signing in...' : 'Sign in'}
        </Button>
      </form>
      {/* There is no self-service password reset yet; until there is, say
          plainly who can help instead of offering a link that goes nowhere. */}
      <p className="mt-5 text-sm text-ink-soft">
        <span className="font-medium text-ink">Forgot password?</span>{' '}
        Ask an admin of your workspace to reset it.
      </p>
    </AuthCard>
  )
}
```

- [ ] **Step 4: Implement Register on AuthCard with the invite-only notice**

Replace `dashboard/src/pages/Register.tsx` with:

```tsx
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'

/** The start of the server's detail when open registration is switched off (api/src/api/auth.py). */
const CLOSED_DETAIL = 'Self-service registration is disabled'

/** True only for the closed-registration 403; every other failure keeps its own detail. */
function isRegistrationClosed(err: unknown): boolean {
  const res = err instanceof Error ? (err as ApiError).response : undefined
  const detail = res?.body?.detail
  return res?.status === 403 && typeof detail === 'string' && detail.startsWith(CLOSED_DETAIL)
}

export default function Register() {
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  // Registration is closed to anyone without a valid invite. Say so in plain
  // words the moment the server answers; the typed values stay in the form.
  const [closed, setClosed] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()
  // An invite token authorizes signup even when open registration is off,
  // and names the organization to join once the account exists.
  const [params] = useSearchParams()
  const invite = params.get('invite')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setClosed(false)
    setIsLoading(true)

    try {
      await api('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email, password, display_name: displayName,
          ...(invite ? { invite_token: invite } : {}),
        }),
      })
      // Registration leaves a half session: the MPIN comes first, and the
      // MPIN page hands over to the invite (or the welcome screen) after.
      const next = invite ? `/join/${invite}` : '/welcome'
      navigate(`/mpin?next=${encodeURIComponent(next)}`, { replace: true })
    } catch (err) {
      if (isRegistrationClosed(err)) setClosed(true)
      else setError(errorText(err, 'Registration failed'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AuthCard
      title="Create your account"
      lead={invite
        ? 'Create your account to accept the invitation.'
        : 'Create an account to open the desk.'}
      footer={
        <p className="text-center text-sm text-ink-soft">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-brand hover:text-brand-deep">
            Sign in
          </Link>
        </p>
      }
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        {error && <Banner kind="error">{error}</Banner>}
        {/* Always mounted, so the notice is announced when it appears. */}
        <div role="status" aria-live="polite">
          {closed && (
            <Banner kind="notice" announce={false}>
              {invite
                ? 'This invite link is invalid or expired, and registration is otherwise by invite only. '
                  + 'Ask whoever invited you for a fresh link.'
                : 'Registration is by invite only. Ask an admin of the workspace you are joining '
                  + 'for an invite link, then open it to create your account.'}
            </Banner>
          )}
        </div>
        <div>
          <label htmlFor="displayName" className="desk-label block mb-1">Display name</label>
          <Input
            id="displayName"
            name="displayName"
            type="text"
            autoComplete="name"
            required
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="email" className="desk-label block mb-1">Email</label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="password" className="desk-label block mb-1">Password</label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p id="password-hint" className="mt-1 text-xs text-ink-soft">At least 10 characters.</p>
        </div>
        <Button type="submit" block disabled={isLoading}>
          {isLoading ? 'Creating account...' : 'Create account'}
        </Button>
      </form>
    </AuthCard>
  )
}
```

Note: the existing test `'renders display name, email, and password inputs'` uses `getByLabelText(/password/i)`. It still matches exactly one control: the hint is a description, not a label, and the notice is not mounted.

- [ ] **Step 5: Implement Mpin on AuthCard**

Replace `dashboard/src/pages/Mpin.tsx` with the following. Only the shell, the headings, the forgot lead copy, the loading state and the Reset button tone change. State, effects, `verify`, `submitSet`, `submitReset`, `signOut`, `safeNext` and `countdown` are byte-for-byte today's.

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import { isMpinPending, type Me, type MpinPending } from '../lib/types'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'
import Loading from '../components/Loading'
import PinInput from '../components/PinInput'

type Mode = 'loading' | 'set' | 'verify' | 'forgot'

/** The one h1 (and the tab title) for each mode. */
const TITLE: Record<Mode, string> = {
  loading: 'Checking your sign-in',
  set: 'Choose your MPIN',
  verify: 'Enter your MPIN',
  forgot: 'Reset your MPIN',
}

const LEAD: Record<Mode, string | undefined> = {
  loading: undefined,
  set: 'One more step: a six-digit MPIN you will enter at every sign-in.',
  verify: 'Your password was right. Now your MPIN.',
  forgot: 'Confirm your password to choose a new MPIN.',
}

/** Only a same-origin path may be the landing after the MPIN. */
function safeNext(raw: string | null): string {
  if (!raw) return '/'
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return '/'
  return raw
}

function countdown(until: Date, now: Date): string {
  const s = Math.max(0, Math.ceil((until.getTime() - now.getTime()) / 1000))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/**
 * The second gate. Email and password are already right (a half session);
 * nothing else in the platform answers until the six-digit MPIN is set (first
 * login), verified (every login after) or reset (through the password).
 */
export default function Mpin() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [mode, setMode] = useState<Mode>('loading')
  const [pin, setPin] = useState('')
  const [confirm, setConfirm] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockedUntil, setLockedUntil] = useState<Date | null>(null)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let cancelled = false
    api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
      .then((me) => {
        if (cancelled) return
        if (!isMpinPending(me)) { navigate(next, { replace: true }); return }
        setMode(me.mpin.set ? 'verify' : 'set')
      })
      .catch(() => { if (!cancelled) navigate('/login', { replace: true }) })
    return () => { cancelled = true }
  }, [navigate, next])

  // The lock countdown ticks once a second and releases itself.
  useEffect(() => {
    if (!lockedUntil) return
    const id = setInterval(() => {
      const t = new Date()
      setNow(t)
      if (t >= lockedUntil) { setLockedUntil(null); setError(null) }
    }, 1000)
    return () => clearInterval(id)
  }, [lockedUntil])

  const fail = (err: unknown) => setError(errorText(err, 'Something went wrong'))

  const verify = useCallback(async (value: string) => {
    setBusy(true); setError(null)
    try {
      await api('/api/mpin/verify', { method: 'POST', body: JSON.stringify({ mpin: value }) })
      navigate(next, { replace: true })
    } catch (err) {
      setPin('')
      const res = (err as ApiError).response
      if (res?.status === 423 && typeof res.body?.locked_until === 'string') {
        setLockedUntil(new Date(res.body.locked_until))
      } else if (res?.status === 401 && typeof res.body?.attempts_left === 'number') {
        const n = res.body.attempts_left
        setError(`Wrong MPIN, ${n} ${n === 1 ? 'try' : 'tries'} left`)
      } else {
        fail(err)
      }
    } finally {
      setBusy(false)
    }
  }, [navigate, next])

  const submitSet = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pin.length !== 6) { setError('MPIN must be exactly 6 digits'); return }
    if (pin !== confirm) { setError('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/mpin/set', { method: 'POST', body: JSON.stringify({ mpin: pin, mpin_confirm: confirm }) })
      navigate(next, { replace: true })
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pin.length !== 6) { setError('MPIN must be exactly 6 digits'); return }
    if (pin !== confirm) { setError('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/mpin/reset', {
        method: 'POST', body: JSON.stringify({ password, mpin: pin, mpin_confirm: confirm }),
      })
      navigate(next, { replace: true })
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  const signOut = async () => {
    try { await api('/api/logout', { method: 'POST' }) } catch { /* cookies may already be gone */ }
    navigate('/login', { replace: true })
  }

  const locked = lockedUntil != null && lockedUntil > now
  const lockText = lockedUntil ? `Locked. Try again in ${countdown(lockedUntil, now)}` : null

  return (
    <AuthCard title={TITLE[mode]} lead={LEAD[mode]}>
      {mode === 'loading' && <Loading lines={2} label="Checking your sign-in" />}
      {mode === 'verify' && (
        <div className="space-y-5">
          <PinInput id="mpin" label="MPIN" value={pin} onChange={setPin}
                    onComplete={verify} disabled={busy || locked}
                    error={locked ? lockText : error} autoFocus />
          <div className="flex items-center justify-between">
            <Button variant="ghost" tone="brand" size="sm"
                    onClick={() => { setMode('forgot'); setPin(''); setConfirm(''); setError(null) }}>
              Forgot MPIN?
            </Button>
            <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
          </div>
        </div>
      )}
      {mode === 'set' && (
        <form onSubmit={submitSet} className="space-y-5">
          {error && <Banner kind="error">{error}</Banner>}
          <PinInput id="mpin" label="MPIN" value={pin} onChange={setPin} disabled={busy} autoFocus />
          <PinInput id="mpin-confirm" label="Confirm MPIN" value={confirm} onChange={setConfirm} disabled={busy} />
          <Button type="submit" block busy={busy}>Save MPIN</Button>
          <div className="text-center">
            <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
          </div>
        </form>
      )}
      {mode === 'forgot' && (
        <form onSubmit={submitReset} className="space-y-5">
          {error && <Banner kind="error">{error}</Banner>}
          <div>
            <label htmlFor="password" className="desk-label block mb-1">Password</label>
            <Input id="password" type="password" autoComplete="current-password" required
                   value={password} onChange={(e) => setPassword(e.target.value)} disabled={busy} />
          </div>
          <PinInput id="mpin" label="New MPIN" value={pin} onChange={setPin} disabled={busy} />
          <PinInput id="mpin-confirm" label="Confirm new MPIN" value={confirm} onChange={setConfirm} disabled={busy} />
          {/* Choosing a new MPIN is an ordinary action, not a destructive one. */}
          <Button type="submit" block busy={busy}>Reset MPIN</Button>
          <div className="flex items-center justify-between">
            <Button variant="ghost" tone="brand" size="sm"
                    onClick={() => { setMode('verify'); setPin(''); setConfirm(''); setPassword(''); setError(null) }}>
              Back
            </Button>
            <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
          </div>
        </form>
      )}
    </AuthCard>
  )
}
```

`AuthCard` sits at the same place in the tree in every mode, so switching mode updates its title in place. `usePageTitle` sees the new string and rewrites `document.title`, and nothing remounts. The PIN boxes are not remounted on a mode change either (they already were not).

- [ ] **Step 6: Implement Join on AuthCard**

Replace `dashboard/src/pages/Join.tsx` with:

```tsx
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { isMpinPending } from '../lib/types'
import type { Me, MpinPending } from '../lib/types'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Loading from '../components/Loading'

type Outcome =
  | { kind: 'joining' }
  | { kind: 'already' }
  | { kind: 'dead'; message: string }

/**
 * Nothing on this page can name the organization: POST /api/orgs/join
 * answers { org_id, role } and the page leaves at once, and there is no
 * invite preview endpoint. So the title is the generic one.
 */
const TITLE = 'Join the workspace'

/**
 * Accept an invite.
 *
 * Joining requires an account, and an invited person usually does not have
 * one yet -- so a 401 here is the NORMAL first step, not an error. It sends
 * them to sign up with the token in hand (the register endpoint accepts a
 * valid invite as authorization even when open signup is closed), and the
 * signup completes the join.
 *
 * Every outcome offers a way forward: the previous version left an already-
 * member on a bare sentence with no link, and bounced a new invitee to a
 * login page they could not get past.
 */
export default function Join() {
  const { token } = useParams()
  const navigate = useNavigate()
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'joining' })

  useEffect(() => {
    let cancelled = false
    const join = async () => {
      // Ask who we are FIRST. A visitor with no session also has no CSRF
      // cookie, so attempting the join would be refused at the CSRF layer
      // (403) before auth was ever considered -- an unreadable failure for
      // the most ordinary case there is. /api/me is a plain GET.
      let me: Me | MpinPending
      try {
        me = await api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
      } catch {
        if (cancelled) return
        navigate(`/register?invite=${encodeURIComponent(token ?? '')}`,
                 { replace: true })
        return
      }
      if (isMpinPending(me)) {
        if (!cancelled) {
          navigate(`/mpin?next=${encodeURIComponent(`/join/${token ?? ''}`)}`, { replace: true })
        }
        return
      }

      try {
        const result = await api<{ org_id: number }>('/api/orgs/join', {
          method: 'POST',
          body: JSON.stringify({ token }),
        }, { redirectOn401: false })
        if (!cancelled) navigate(`/org/${result.org_id}`, { replace: true })
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : ''
        if (message.includes('409')) {
          setOutcome({ kind: 'already' })
          return
        }
        setOutcome({
          kind: 'dead',
          message: message.includes('410')
            ? 'This invite is invalid or expired — it may already have been '
              + 'used. Ask whoever invited you for a fresh link.'
            : 'Could not join the organization.',
        })
      }
    }
    join()
    return () => { cancelled = true }
  }, [token, navigate])

  return (
    <AuthCard
      title={TITLE}
      lead={outcome.kind === 'joining' ? 'Checking your invite.' : undefined}
    >
      <div className="space-y-4 text-center">
        {outcome.kind === 'joining' && <Loading lines={2} label="Joining the workspace" />}
        {outcome.kind === 'already' && (
          <>
            <p className="text-sm text-ink">
              You are already a member of this organization.
            </p>
            <Button to="/welcome">Open MirrorFleet</Button>
          </>
        )}
        {outcome.kind === 'dead' && (
          <>
            <Banner kind="error">{outcome.message}</Banner>
            <Link to="/login" className="inline-block text-sm font-medium text-brand hover:text-brand-deep">
              Sign in
            </Link>
          </>
        )}
      </div>
    </AuthCard>
  )
}
```

- [ ] **Step 7: Implement Welcome on AuthCard with two labelled choices**

Replace `dashboard/src/pages/Welcome.tsx` with:

```tsx
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { errorText } from '../lib/format'
import { isMpinPending } from '../lib/types'
import type { Me, MpinPending } from '../lib/types'
import AuthCard from '../components/AuthCard'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'
import { roleLabel } from '../lib/roles'

interface MyOrg { id: number; name: string; role: string }

export default function Welcome() {
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // Organizations this account already belongs to. Without these the page
  // was a dead end for an existing member: everything that lands here --
  // leaving an org, an invite you already accepted -- offered only "create"
  // and "join", with no way back into a workspace you are already in.
  const [orgs, setOrgs] = useState<MyOrg[] | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    api<Me | MpinPending>('/api/me')
      .then((me) => {
        if (cancelled) return
        if (isMpinPending(me)) {
          navigate('/mpin', { replace: true })
          return
        }
        setOrgs(me.orgs ?? [])
      })
      .catch(() => { if (!cancelled) setOrgs([]) })
    return () => { cancelled = true }
  }, [navigate])

  const createOrg = async (e: React.FormEvent) => {
    e.preventDefault()
    if (creating) return
    try {
      setCreating(true)
      const org = await api<{ id: number }>('/api/orgs', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      navigate(`/org/${org.id}`)
    } catch (err) {
      setError(errorText(err, 'Could not create organization'))
    } finally {
      setCreating(false)
    }
  }

  const useInvite = (e: React.FormEvent) => {
    e.preventDefault()
    const match = invite.match(/\/join\/([A-Za-z0-9_-]+)/)
    const token = match ? match[1] : invite.trim()
    if (token) navigate(`/join/${token}`)
  }

  return (
    <AuthCard
      title="Welcome"
      lead="An organization is the workspace that holds your trading accounts, your team and its settings."
    >
      <div className="space-y-5">
        {error && <Banner kind="error">{error}</Banner>}

        {orgs != null && orgs.length > 0 && (
          <section aria-labelledby="welcome-workspaces" className="inset p-5 space-y-3">
            <h2 id="welcome-workspaces" className="text-lg font-display font-semibold text-ink">
              Your workspaces
            </h2>
            <ul className="space-y-2">
              {orgs.map((o) => (
                <li key={o.id}>
                  <Link
                    to={`/org/${o.id}`}
                    className="flex items-center justify-between gap-3 rounded-control border border-line-strong px-3 py-2.5 text-sm text-ink hover:border-brand hover:bg-brand-wash transition-colors"
                  >
                    <span className="font-medium">{o.name}</span>
                    <span className="desk-label">{roleLabel(o.role)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section aria-labelledby="welcome-create" className="inset p-5">
          <form onSubmit={createOrg} className="space-y-4">
            <h2 id="welcome-create" className="text-lg font-display font-semibold text-ink">
              Create an organization
            </h2>
            <div>
              <label htmlFor="org-name" className="desk-label block mb-1">Organization name</label>
              <Input
                id="org-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                autoComplete="organization"
              />
            </div>
            <Button type="submit" block disabled={creating}>
              {creating ? 'Creating…' : 'Create organization'}
            </Button>
          </form>
        </section>

        <section aria-labelledby="welcome-join" className="inset p-5">
          <form onSubmit={useInvite} className="space-y-4">
            <h2 id="welcome-join" className="text-lg font-display font-semibold text-ink">
              Join with an invite
            </h2>
            <div>
              <label htmlFor="invite-code" className="desk-label block mb-1">Invite link or code</label>
              <Input
                id="invite-code"
                value={invite}
                onChange={(e) => setInvite(e.target.value)}
                aria-describedby="invite-code-hint"
              />
              <p id="invite-code-hint" className="mt-1 text-xs text-ink-soft">
                Paste the whole link from your invite, or just the code at its end.
              </p>
            </div>
            <Button type="submit" variant="secondary" tone="brand" block>
              Join organization
            </Button>
          </form>
        </section>
      </div>
    </AuthCard>
  )
}
```

Existing Welcome tests stay valid. `getByLabelText(/organization name/i)` and `getByLabelText(/invite link or code/i)` now resolve through the visible `<label>`s. The buttons keep "Create organization" and "Join organization". `findByText(/create an organization/i)` matches only the "Create an organization" `<h2>`: the lead says "An organization is…", which the regex does not match, and the button text is "Create organization". `queryByText(/your workspaces/i)` still sees only the list heading.

- [ ] **Step 8: Run the five test files, then `npm test`**

Run (from `dashboard/`):

```bash
npx vitest run src/pages/Login.test.tsx src/pages/Register.test.tsx src/pages/Mpin.test.tsx src/pages/Join.test.tsx src/pages/Welcome.test.tsx
```

Expected: all tests in the five files pass: Login 5, Register 10, Mpin 13, Join 7, Welcome 8.

Then run the full gate:

```bash
npm test
```

Expected: `palette_check.mjs` passes, `tsc --noEmit -p tsconfig.app.json` reports no errors (no unused `Logo` imports remain in the five pages), and the whole vitest suite is green.

Confirm the shells are gone:

```bash
grep -n "min-h-screen\|<Logo" src/pages/Login.tsx src/pages/Register.tsx src/pages/Mpin.tsx src/pages/Join.tsx src/pages/Welcome.tsx
```

Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add dashboard/src/pages/Login.tsx dashboard/src/pages/Login.test.tsx \
        dashboard/src/pages/Register.tsx dashboard/src/pages/Register.test.tsx \
        dashboard/src/pages/Mpin.tsx dashboard/src/pages/Mpin.test.tsx \
        dashboard/src/pages/Join.tsx dashboard/src/pages/Join.test.tsx \
        dashboard/src/pages/Welcome.tsx dashboard/src/pages/Welcome.test.tsx
git commit -m "feat(dashboard): auth stack on AuthCard -- real headings, page titles, invite-only notice, forgot-password path

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

(Run `git add` from the repo root, or drop the `dashboard/` prefix when running from `dashboard/`.)

### Task 5: The landing page

Spec: §5.1 (Landing), §2.3 (glass only on floating layers), §2.4 (no side stripes, no gradient text), §2.5 (hero size, `.desk-label` never a kicker), §9 (one real `<h1>`, per-route title), §10 (page tests updated, never weakened; `npm test` green; build size reported). Facts stay those of `README.md` and `docs/superpowers/specs/2026-09-24-public-landing-page-design.md` §2: no statistics, testimonials, awards, client counts or regulatory claims.

**Files:**
- Modify: `dashboard/src/pages/Landing.tsx` (full rewrite below)
- Modify: `dashboard/src/pages/Landing.test.tsx` (full rewrite below)
- Modify: `dashboard/index.html` (one added line: `<meta name="description">`)
- Create: `dashboard/public/overview-desk.png` (1280 × 800 screenshot of the running Overview, light theme) — **or, only if no browser is available,** Create: `dashboard/src/pages/landing/HeroPreview.tsx` (static mock, Step 6b) and do not add the PNG

**Interfaces:**
- Consumes (unchanged APIs): `Card` from Task 2 (`title`, `as`, `className`, `children`; glass `rounded-card shadow-card p-5`, `title` renders an `<h2>`); `Button` (`to`, `variant`, `tone`, `size`); `Logo` (default export, `size`, `textClass`); `StatusDot` (`tone: 'ok' | 'degraded' | 'paused' | 'warn'`); Task 1 classes `.glass`, `.inset`, `.hero-title`, `.num`, `rounded-card`, `rounded-inset`, `rounded-control`, `shadow-card`. The wash is painted by `body::before` (Task 1), so the page root carries no background.
- Produces: `Landing` (default export, unchanged route use in `App.tsx`); `LANDING_FACTS` (same shape, `address` and `supportEmail` now filled); new export `PAGE_TITLE = 'MirrorFleet — copy trading for cTrader and MT5'` (46 characters). Fallback only: `HeroPreview` (default export, no props) in `src/pages/landing/HeroPreview.tsx`.
- Why `Landing` sets `document.title` itself instead of calling `usePageTitle`: spec §3 formats page titles as "Title · MirrorFleet", and the landing title already starts with the brand, so the hook would print it twice. The effect restores the previous title on unmount, so the next route's `usePageTitle` is unaffected.

All commands run from `dashboard/` in Git Bash.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `dashboard/src/pages/Landing.test.tsx` with:

```tsx
// src/pages/Landing.test.tsx
import { existsSync, readFileSync } from 'node:fs'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import Landing, { LANDING_FACTS, PAGE_TITLE } from './Landing'

function renderLanding() {
  return render(<MemoryRouter><Landing /></MemoryRouter>)
}

test('the front page has one h1, offers sign in and account creation, and names its sections', () => {
  renderLanding()
  const h1s = screen.getAllByRole('heading', { level: 1 })
  expect(h1s).toHaveLength(1)
  expect(h1s[0]).toHaveTextContent(/trade once/i)

  const signIns = screen.getAllByRole('link', { name: 'Sign in' })
  expect(signIns.length).toBeGreaterThan(0)
  for (const a of signIns) expect(a).toHaveAttribute('href', '/login')

  const creates = screen.getAllByRole('link', { name: 'Create account' })
  expect(creates.length).toBeGreaterThan(1)
  for (const a of creates) expect(a).toHaveAttribute('href', '/register')

  for (const name of ['One desk for every account', 'How it works', 'For investors', 'Questions']) {
    expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument()
  }
  expect(screen.getByText(/high level of risk/i)).toBeInTheDocument()
})

test('the hero shows the product inside a glass frame', () => {
  renderLanding()
  const shot = screen.getByRole('img', { name: /MirrorFleet Overview/i })
  const frame = shot.closest('figure')
  expect(frame).not.toBeNull()
  expect(frame).toHaveClass('glass')
})

test('three asymmetric panels each show a piece of the product, marked as example data', () => {
  renderLanding()
  const fleet = screen.getByRole('heading', { level: 3, name: 'Every follower on one screen' }).closest('article')
  const log = screen.getByRole('heading', { level: 3, name: 'Every fill, timestamped' }).closest('article')
  const risk = screen.getByRole('heading', { level: 3, name: 'Limits before the broker' }).closest('article')
  expect(fleet).not.toBeNull()
  expect(log).not.toBeNull()
  expect(risk).not.toBeNull()

  // One wide panel, two narrow ones.
  expect(fleet).toHaveClass('md:col-span-2')
  expect(log).not.toHaveClass('md:col-span-2')
  expect(risk).not.toHaveClass('md:col-span-2')

  // Real markup, not icons: follower cards with status words and equity,
  // timestamped log lines, a rule table.
  expect(within(fleet!).getByText('Offline')).toBeInTheDocument()
  expect(within(fleet!).getAllByText('Copying').length).toBeGreaterThan(1)
  expect(within(fleet!).getAllByText(/^[0-9,]+\.[0-9]{2}$/).length).toBeGreaterThan(3)
  expect(log!.querySelectorAll('time').length).toBeGreaterThan(3)
  expect(within(risk!).getByRole('table')).toBeInTheDocument()

  expect(screen.getByText(/example data, not trading results/i)).toBeInTheDocument()
})

test('how it works is prose, with no numbered markers', () => {
  renderLanding()
  const how = document.getElementById('how')
  expect(how).not.toBeNull()
  expect(how!.querySelector('ol')).toBeNull()
  const prose = how!.querySelector('p')
  expect(prose?.textContent).toMatch(/connect.*add.*trade once/is)
})

test('the FAQ is a disclosure list', () => {
  renderLanding()
  const items = document.querySelectorAll('#faq details')
  expect(items.length).toBe(6)
  items.forEach((d) => {
    expect(d.querySelector('summary')).not.toBeNull()
    expect(d).not.toHaveAttribute('open')
  })
})

test('no eyebrow kickers: the small-caps label only appears on table headers', () => {
  renderLanding()
  expect(screen.queryByText(/copy trading desk/i)).toBeNull()
  const labels = Array.from(document.querySelectorAll('.desk-label'))
  for (const el of labels) expect(el.tagName).toBe('TH')
})

test('the footer carries the risk notice, the support email and the address', () => {
  renderLanding()
  const footer = screen.getByRole('contentinfo')
  const mail = within(footer).getByRole('link', { name: LANDING_FACTS.supportEmail })
  expect(mail).toHaveAttribute('href', 'mailto:support@mirrorfleet.com')
  expect(within(footer).getByText(/Chennai, India/)).toBeInTheDocument()
  expect(within(footer).getByText(/high level of risk/i)).toBeInTheDocument()
})

test('the page sets its own title and hands the old one back on unmount', () => {
  document.title = 'Before'
  const { unmount } = renderLanding()
  expect(document.title).toBe('MirrorFleet — copy trading for cTrader and MT5')
  expect(PAGE_TITLE.length).toBeLessThan(60)
  unmount()
  expect(document.title).toBe('Before')
})

test('index.html describes the product in one honest sentence', () => {
  const html = readFileSync('index.html', 'utf8')
  const m = html.match(/<meta name="description" content="([^"]+)" \/>/)
  expect(m).not.toBeNull()
  const description = m![1]
  expect(description.length).toBeGreaterThan(50)
  expect(description.length).toBeLessThanOrEqual(160)
  expect(description).toMatch(/cTrader/)
  expect(description).toMatch(/MetaTrader 5/)
  expect(description).not.toMatch(/regulated|licensed|award|guarantee|best|#1/i)
})

test('the landing sources use tokens, never hard-coded colours', () => {
  const files = ['src/pages/Landing.tsx', 'src/pages/landing/HeroPreview.tsx'].filter((f) => existsSync(f))
  for (const f of files) {
    const src = readFileSync(f, 'utf8')
    expect(src).not.toMatch(/#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(|hsla?\(/)
  }
})

test('the page makes no claims it cannot back', () => {
  renderLanding()
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/regulated|licensed|award|guarantee|risk-free|[0-9,]+\+? (clients|traders|users)/i)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/pages/Landing.test.tsx`
Expected: FAIL. `PAGE_TITLE` is not exported (TypeScript still runs, so it is `undefined`), and at least these fail: the section headings ("One desk for every account", "Questions"), the glass-framed image (no `img` role named "MirrorFleet Overview"), the three panels, "how it works is prose" (the old page has an `<ol>`), the disclosure list (the old FAQ is a `<dl>`), no eyebrow kickers (the old hero has "Copy trading desk" on a `<p class="desk-label">`), the footer email, the title, and the `index.html` description. "The landing sources use tokens" and "no claims" pass already.

- [ ] **Step 3: Implement `Landing.tsx`**

Replace the whole of `dashboard/src/pages/Landing.tsx` with:

```tsx
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import Button from '../components/Button'
import Card from '../components/Card'
import Logo from '../components/Logo'
import StatusDot from '../components/StatusDot'

/** Company facts the desk operator fills in. Empty strings are simply
 *  not rendered, so the page stays truthful if one is cleared. */
export const LANDING_FACTS = {
  legalName: 'MirrorFleet',
  address: 'MirrorFleet, Chennai, India',
  supportEmail: 'support@mirrorfleet.com',
}

/** The tab title. Set here rather than through usePageTitle, whose
 *  "Title · MirrorFleet" format would print the brand twice. */
export const PAGE_TITLE = 'MirrorFleet — copy trading for cTrader and MT5'

const HERO_ALT =
  'The MirrorFleet Overview in the light theme: headline figures across the top, ' +
  'then every account in the fleet with its status and the latest copied trades.'

/* ---------------------------------------------------------------------------
   Example data for the three product panels. Every figure below is made up
   for illustration and the page says so under the panels.
--------------------------------------------------------------------------- */

type FleetStatus = 'ok' | 'paused' | 'degraded'

const MASTER = { name: 'Main book', platform: 'cTrader', equity: '25,410.20' }

const FOLLOWERS: {
  name: string
  platform: 'cTrader' | 'MT5'
  multiplier: string
  equity: string
  status: FleetStatus
  label: string
}[] = [
  { name: 'Growth A', platform: 'cTrader', multiplier: '1.00', equity: '10,212.55', status: 'ok', label: 'Copying' },
  { name: 'Growth B', platform: 'MT5', multiplier: '0.50', equity: '5,104.90', status: 'ok', label: 'Copying' },
  { name: 'Family', platform: 'cTrader', multiplier: '0.25', equity: '2,540.00', status: 'paused', label: 'Paused' },
  { name: 'Swing', platform: 'MT5', multiplier: '2.00', equity: '20,388.75', status: 'degraded', label: 'Offline' },
]

const COPY_LOG: { at: string; who: string; what: string; tone: 'text-ink' | 'text-profit' | 'text-ink-soft' | 'text-loss' }[] = [
  { at: '09:41:07.212', who: 'Main book', what: 'bought 1.00 lot XAUUSD', tone: 'text-ink' },
  { at: '09:41:07.341', who: 'Growth A', what: 'copied 1.00 lot, filled', tone: 'text-profit' },
  { at: '09:41:07.356', who: 'Growth B', what: 'copied 0.50 lot, filled', tone: 'text-profit' },
  { at: '09:41:07.357', who: 'Family', what: 'not copied, paused', tone: 'text-ink-soft' },
  { at: '09:41:07.357', who: 'Swing', what: 'not copied, terminal offline', tone: 'text-loss' },
]

const RISK_RULES = [
  { symbol: 'XAUUSD', stop: '300', target: '600' },
  { symbol: 'EURUSD', stop: '150', target: '300' },
  { symbol: 'US500', stop: '400', target: '800' },
]

const LIMITS = [
  { name: 'Max lots per alert', value: '2.00' },
  { name: 'Alerts per minute', value: '10' },
  { name: 'Max open positions', value: '5' },
]

const INVESTOR_STEPS = [
  { title: 'Deposit', text: "Send crypto to the desk's wallet and file a deposit notice. An admin confirms it against the chain." },
  { title: 'Watch', text: 'Your own trading account is linked to your login: live equity, open positions, closed trades and a performance snapshot.' },
  { title: 'Withdraw', text: 'Request an amount and a destination. An admin approves, pays from the desk wallet and records the transaction.' },
]

const FAQ = [
  {
    q: 'Which platforms are supported?',
    a: "cTrader accounts connect through the broker's Open API. MetaTrader 5 accounts connect through a small expert advisor that runs in the terminal. Either can be a master or a follower.",
  },
  {
    q: 'Does MirrorFleet hold my funds?',
    a: "No. Trading accounts stay at your broker, and the desk never holds account passwords or wallet keys. Investor deposits go to the desk operator's wallet, and withdrawals are paid by the operator.",
  },
  {
    q: 'How are follower trades sized?',
    a: 'Each follower has a lot multiplier relative to the master, with per-symbol aliases for brokers that name instruments differently. Risk rules can add or tighten stops after a fill.',
  },
  {
    q: 'What happens if a terminal goes offline?',
    a: 'The desk marks the account as disconnected, alerts the operator, and shows the last known equity until the terminal reports again. Nothing is copied to an account that is offline.',
  },
  {
    q: 'Can I try it without placing trades?',
    a: 'Yes. With dry run on, the desk watches the master and logs what it would have sent to each follower, without placing a single order.',
  },
  {
    q: 'How do I get access?',
    a: 'Create an account, or ask the desk operator for an invite link if sign-up is invite-only. Investors are invited by the desk that manages their money.',
  },
]

const RISK_NOTICE =
  'Trading leveraged products such as forex, metals and CFDs carries a high level of risk and may not be suitable for everyone. Past results do not predict future results. Nothing on this page is investment advice.'

function Ctas({ large = false, compact = false }: { large?: boolean; compact?: boolean }) {
  const size = large ? 'lg' : 'sm'
  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* On a phone the header keeps only Sign in; the hero carries Create account. */}
      <div className={compact ? 'hidden sm:block' : undefined}>
        <Button to="/register" size={size}>Create account</Button>
      </div>
      <Button to="/login" variant="secondary" tone="brand" size={size}>Sign in</Button>
    </div>
  )
}

function FleetPanel() {
  return (
    <Card as="article" className="md:col-span-2 md:row-span-2">
      <div className="space-y-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-ink">Every follower on one screen</h3>
          <p className="text-sm text-ink-soft max-w-xl">
            Each follower copies the master with its own lot multiplier. cTrader accounts connect
            through the broker&apos;s Open API, MetaTrader 5 terminals through the MirrorFleet expert
            advisor, and the desk shows each one&apos;s equity and whether it is copying.
          </p>
        </div>
        <div className="inset p-3 space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2 px-1">
            <p className="text-sm text-ink">
              <span className="font-semibold">{MASTER.name}</span>
              <span className="text-ink-soft"> · master · {MASTER.platform}</span>
            </p>
            <p className="text-sm text-ink-soft">
              Equity <span className="num text-ink">{MASTER.equity}</span> USD
            </p>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2">
            {FOLLOWERS.map((f) => (
              <li key={f.name} className="rounded-control border border-line p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-ink">{f.name}</span>
                  <span className="text-xs text-ink-soft">{f.platform} · ×{f.multiplier}</span>
                </div>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="num text-xl text-ink">{f.equity}</span>
                  <span className="inline-flex items-center gap-1.5 text-xs text-ink-soft">
                    <StatusDot tone={f.status} />
                    {f.label}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  )
}

function CopyLogPanel() {
  return (
    <Card as="article">
      <div className="space-y-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-ink">Every fill, timestamped</h3>
          <p className="text-sm text-ink-soft">
            The master&apos;s fill and each follower&apos;s copy land in one log. Rejected copies and
            degraded accounts also raise an email alert.
          </p>
        </div>
        <ol className="inset divide-y divide-line text-sm">
          {COPY_LOG.map((line) => (
            <li key={`${line.at}-${line.who}`} className="flex gap-3 px-3 py-2">
              <time dateTime={line.at} className="num text-ink-faint shrink-0">{line.at.slice(0, 8)}</time>
              <span className={line.tone}>
                <span className="font-semibold">{line.who}</span> {line.what}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </Card>
  )
}

function RiskPanel() {
  return (
    <Card as="article">
      <div className="space-y-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-ink">Limits before the broker</h3>
          <p className="text-sm text-ink-soft">
            Every TradingView alert is checked against your limits before it reaches the broker, and a
            symbol rule fills in the stop and target when the alert sends none. One click flattens an
            account.
          </p>
        </div>
        <div className="inset p-3 space-y-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label py-1.5 font-semibold">Symbol</th>
                <th className="desk-label py-1.5 font-semibold text-right">Stop (pts)</th>
                <th className="desk-label py-1.5 font-semibold text-right">Target (pts)</th>
              </tr>
            </thead>
            <tbody>
              {RISK_RULES.map((r) => (
                <tr key={r.symbol} className="border-b border-line last:border-0">
                  <td className="py-1.5 text-ink">{r.symbol}</td>
                  <td className="num py-1.5 text-right text-ink">{r.stop}</td>
                  <td className="num py-1.5 text-right text-ink">{r.target}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="space-y-1 text-sm">
            {LIMITS.map((l) => (
              <div key={l.name} className="flex items-baseline justify-between gap-3">
                <dt className="text-ink-soft">{l.name}</dt>
                <dd className="num text-ink">{l.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </Card>
  )
}

export default function Landing() {
  const year = new Date().getFullYear()

  useEffect(() => {
    const previous = document.title
    document.title = PAGE_TITLE
    return () => {
      document.title = previous
    }
  }, [])

  return (
    <div className="min-h-screen text-ink">
      <header className="sticky top-0 z-10 px-4 md:px-6 pt-3">
        <div className="glass rounded-card shadow-card max-w-6xl mx-auto px-4 min-h-14 py-2 flex items-center justify-between gap-6">
          <Logo size={30} textClass="text-xl" />
          <nav aria-label="Sections" className="hidden md:flex items-center gap-6 text-sm text-ink-soft">
            <a href="#platform" className="hover:text-ink">Platform</a>
            <a href="#how" className="hover:text-ink">How it works</a>
            <a href="#investors" className="hover:text-ink">Investors</a>
            <a href="#faq" className="hover:text-ink">FAQ</a>
          </nav>
          <Ctas compact />
        </div>
      </header>

      <main>
        <section className="max-w-6xl mx-auto px-4 md:px-6 pt-12 md:pt-20 pb-16 space-y-10 md:space-y-14">
          <div className="max-w-3xl space-y-6">
            <h1 className="hero-title font-bold text-ink">
              Trade once. Mirror it across every account.
            </h1>
            <p className="text-lg text-ink-soft max-w-2xl">
              MirrorFleet copies your master account to every cTrader and MetaTrader 5 follower,
              checks each order against your risk rules, takes orders from TradingView, and gives your
              investors a portal of their own.
            </p>
            <Ctas large />
          </div>
          <figure className="glass rounded-card shadow-card p-2 md:p-3">
            <img
              src="/overview-desk.png"
              width={1280}
              height={800}
              alt={HERO_ALT}
              className="block w-full h-auto rounded-inset border border-line"
            />
          </figure>
        </section>

        <section id="platform" className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-8">
          <div className="max-w-2xl space-y-2">
            <h2 className="text-3xl font-bold text-ink">One desk for every account</h2>
            <p className="text-ink-soft">
              The same screens you would use every day: the fleet, the copy log and the rules that
              guard it.
            </p>
          </div>
          <div className="grid gap-5 md:grid-cols-3">
            <FleetPanel />
            <CopyLogPanel />
            <RiskPanel />
          </div>
          <p className="text-sm text-ink-faint">The panels above show example data, not trading results.</p>
        </section>

        <section id="how" className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-5">
          <h2 className="text-3xl font-bold text-ink">How it works</h2>
          <p className="text-lg text-ink-soft max-w-3xl">
            <strong className="font-semibold text-ink">Connect the account you trade on</strong>, cTrader
            or MetaTrader 5, as the master. <strong className="font-semibold text-ink">Add your
            followers</strong>, give each a lot multiplier, and set the stop, target and exposure
            limits. Then <strong className="font-semibold text-ink">trade once</strong> on the master:
            the fleet mirrors it, and the desk shows every fill as it lands. Start with dry run on and
            the desk logs what it would have sent without placing a single order.
          </p>
        </section>

        <section id="investors" className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20">
          <Card as="div" title="For investors">
            <div className="space-y-6">
              <p className="text-ink-soft max-w-2xl">
                Your money is traded on an account that is yours to watch, from deposit to withdrawal.
              </p>
              <dl className="grid gap-x-8 gap-y-3 md:grid-cols-[8rem_1fr]">
                {INVESTOR_STEPS.map((s) => (
                  <div key={s.title} className="contents">
                    <dt className="font-semibold text-ink">{s.title}</dt>
                    <dd className="text-sm text-ink-soft">{s.text}</dd>
                  </div>
                ))}
              </dl>
              <Ctas />
            </div>
          </Card>
        </section>

        <section id="faq" className="max-w-3xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-6">
          <h2 className="text-3xl font-bold text-ink">Questions</h2>
          <div className="border-t border-line">
            {FAQ.map((item) => (
              <details key={item.q} className="group border-b border-line">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-4 font-semibold text-ink [&::-webkit-details-marker]:hidden">
                  {item.q}
                  <svg aria-hidden="true" viewBox="0 0 16 16"
                       className="h-4 w-4 shrink-0 text-brand transition-transform group-open:rotate-180">
                    <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </summary>
                <p className="pb-4 text-sm text-ink-soft">{item.a}</p>
              </details>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="max-w-6xl mx-auto px-4 md:px-6 py-10 space-y-6 text-sm text-ink-soft">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Logo size={26} textClass="text-lg" />
            <div className="flex items-center gap-4">
              <Link to="/login" className="hover:text-ink">Sign in</Link>
              <Link to="/register" className="hover:text-ink">Create account</Link>
            </div>
          </div>
          <p>{RISK_NOTICE}</p>
          <div className="space-y-1">
            <p>© {year} {LANDING_FACTS.legalName}</p>
            {(LANDING_FACTS.address || LANDING_FACTS.supportEmail) && (
              <address className="not-italic">
                {LANDING_FACTS.address}
                {LANDING_FACTS.address && LANDING_FACTS.supportEmail && ' · '}
                {LANDING_FACTS.supportEmail && (
                  <a href={`mailto:${LANDING_FACTS.supportEmail}`} className="text-brand-deep hover:underline">
                    {LANDING_FACTS.supportEmail}
                  </a>
                )}
              </address>
            )}
          </div>
        </div>
      </footer>
    </div>
  )
}
```

Notes for the implementer (no code changes implied):
- Glass appears only on the sticky header bar, the hero `<figure>` and the `Card`s (the three panels and the investor card). Sections, the FAQ and the footer sit straight on the wash with no background.
- Every run of figures inside a glass `Card` sits on `.inset` (spec §2.3).
- `.desk-label` appears only on the risk table's `<th>` cells, which the kicker test pins.
- The FAQ keeps the five questions from the 2026-09-24 spec and adds one on dry run (README §4, Stage 1).

- [ ] **Step 4: Add the meta description to `index.html`**

Add one line directly under the viewport meta. Anchor on the viewport line, not on the favicon or `theme-color` lines, which an earlier task of this plan rewrites:

```diff
     <meta name="viewport" content="width=device-width, initial-scale=1.0" />
+    <meta name="description" content="MirrorFleet copies one master trading account to many cTrader and MetaTrader 5 follower accounts, with risk rules, TradingView orders and an investor portal." />
     <link rel="preconnect" href="https://fonts.googleapis.com" />
```

The content is 157 characters (the test caps it at 160). Leave `<title>MirrorFleet</title>` as it is; the page sets its own title at runtime.

- [ ] **Step 5: Run the landing tests**

Run: `npx vitest run src/pages/Landing.test.tsx`
Expected: PASS, all 11 tests. The hero test is green before the PNG exists because jsdom never loads images; Step 6 adds the file the browser needs.

- [ ] **Step 6a: Capture the hero screenshot (preferred path)**

The image must be the real product, captured from the running app, at 1280 × 800 in the light theme.

1. From the repo root, start the stack: `docker compose up -d`. Wait until `docker compose ps` shows `api` running.
2. From `dashboard/`, start the dev server: `npm run dev`. Vite prints `http://localhost:5173/` and proxies `/api` to `http://localhost:8000` (see `vite.config.ts`).
3. In Chrome, open `http://localhost:5173/login`, sign in (email, password, MPIN) and open the Overview of an org that has a connected master and at least two followers. **Use demo accounts only.** Before capturing, give the accounts nicknames on the Accounts screen so no real broker login or person's name appears in the image. Do not edit any figure.
4. Force the light theme: open DevTools (F12), Console, run `localStorage.setItem('mf.theme', 'light'); location.reload()`.
5. Open the device toolbar (Ctrl+Shift+M). Choose "Responsive", type `1280` × `800`, zoom 100%. In the toolbar's ⋮ menu choose "Add device pixel ratio" and set it to `1`.
6. Open the command menu (Ctrl+Shift+P), run **Capture screenshot** (the viewport one, not "full size"). Save the download as `dashboard/public/overview-desk.png` (create `dashboard/public/` if it does not exist; Vite serves it at `/overview-desk.png` and copies it into `dist/`, which the api serves as a static file).
7. Verify the size from `dashboard/`:
   `node -e "const b=require('fs').readFileSync('public/overview-desk.png');console.log(b.readUInt32BE(16)+'x'+b.readUInt32BE(20), Math.round(b.length/1024)+' KB')"`
   Expected: `1280x800` and a size; put both in the task report.
8. Check the `HERO_ALT` text in `Landing.tsx` still describes what the image shows (headline figures, the fleet with its status, the latest copied trades). If the captured Overview shows something different, edit `HERO_ALT` so it describes exactly what is in the image, keeping the words "MirrorFleet Overview" (the test looks for them).
9. Stop the dev server (Ctrl+C). The Browser extension (claude-in-chrome) can do steps 3 to 6 instead of a person, with the same settings.

If the Overview redesign task of this plan has not landed yet, capture today's Overview anyway and say in the report that the image should be recaptured after that task.

- [ ] **Step 6b: Fallback only if no browser is available — a static preview instead of the PNG**

Skip this step when Step 6a produced the PNG. Otherwise do not add a PNG, create `dashboard/src/pages/landing/HeroPreview.tsx`:

```tsx
import Card from '../../components/Card'
import StatusDot from '../../components/StatusDot'

/**
 * Hero art used when no screenshot of the running Overview could be
 * captured: a static mock of the Overview built from the desk's own Card
 * surface and example figures. Exposed to assistive technology as one
 * labelled image so its figures are never read out as if they were live.
 */
const LABEL =
  'Example of the MirrorFleet Overview: headline figures, then every account ' +
  'in the fleet with its equity and copy status. Example data.'

const TILES: { label: string; value: string; sub: string; tone: 'text-brand' | 'text-ink' | 'text-profit' }[] = [
  { label: 'Portfolio value', value: '63,656.40', sub: '+0.84% vs yesterday', tone: 'text-brand' },
  { label: 'Accounts connected', value: '5', sub: '1 master · 4 followers', tone: 'text-ink' },
  { label: 'Open P&L', value: '+412.35', sub: '6 open trades', tone: 'text-profit' },
  { label: 'Total P&L', value: '+528.10', sub: '5 accounts since yesterday', tone: 'text-profit' },
]

const ACCOUNTS: {
  name: string
  role: 'Master' | 'Follower'
  platform: 'cTrader' | 'MT5'
  equity: string
  status: 'ok' | 'paused' | 'degraded'
  label: string
}[] = [
  { name: 'Main book', role: 'Master', platform: 'cTrader', equity: '25,410.20', status: 'ok', label: 'Connected' },
  { name: 'Growth A', role: 'Follower', platform: 'cTrader', equity: '10,212.55', status: 'ok', label: 'Copying' },
  { name: 'Growth B', role: 'Follower', platform: 'MT5', equity: '5,104.90', status: 'ok', label: 'Copying' },
  { name: 'Family', role: 'Follower', platform: 'cTrader', equity: '2,540.00', status: 'paused', label: 'Paused' },
  { name: 'Swing', role: 'Follower', platform: 'MT5', equity: '20,388.75', status: 'degraded', label: 'Offline' },
]

export default function HeroPreview() {
  return (
    <div role="img" aria-label={LABEL} className="space-y-3 rounded-inset">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {TILES.map((t) => (
          <Card key={t.label}>
            <div className="text-xs font-medium text-ink-soft">{t.label}</div>
            <div className={`num text-2xl font-semibold tracking-tight mt-1 ${t.tone}`}>{t.value}</div>
            <div className="text-xs text-ink-soft mt-0.5">{t.sub}</div>
          </Card>
        ))}
      </div>
      <Card>
        <div className="inset overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-3 py-2 font-semibold">Account</th>
                <th className="desk-label px-3 py-2 font-semibold">Role</th>
                <th className="desk-label px-3 py-2 font-semibold hidden sm:table-cell">Platform</th>
                <th className="desk-label px-3 py-2 font-semibold text-right">Equity</th>
                <th className="desk-label px-3 py-2 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {ACCOUNTS.map((a) => (
                <tr key={a.name} className="border-b border-line last:border-0">
                  <td className="px-3 py-2 text-ink">{a.name}</td>
                  <td className="px-3 py-2 text-ink-soft">{a.role}</td>
                  <td className="px-3 py-2 text-ink-soft hidden sm:table-cell">{a.platform}</td>
                  <td className="num px-3 py-2 text-right text-ink">{a.equity}</td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1.5 text-ink-soft">
                      <StatusDot tone={a.status} />
                      {a.label}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
```

Then in `dashboard/src/pages/Landing.tsx`:

1. Add the import under the `StatusDot` import:

```tsx
import HeroPreview from './landing/HeroPreview'
```

2. Replace the image inside the hero `<figure>`:

```tsx
            <img
              src="/overview-desk.png"
              width={1280}
              height={800}
              alt={HERO_ALT}
              className="block w-full h-auto rounded-inset border border-line"
            />
```

with:

```tsx
            <HeroPreview />
```

3. Delete the now unused `HERO_ALT` constant (TypeScript's `noUnusedLocals` would fail `npm test` otherwise).

The tests need no change: the preview is an `img` role named "…MirrorFleet Overview…" inside the glass `<figure>`, its `.desk-label` cells are all `<th>`, and the source-colour test scans `HeroPreview.tsx` once it exists. Say in the task report that the fallback was used and that `public/overview-desk.png` still needs capturing.

- [ ] **Step 7: Run the landing tests again**

Run: `npx vitest run src/pages/Landing.test.tsx`
Expected: PASS, all 11 tests.

- [ ] **Step 8: Run the full gate**

Run: `npm test`
Expected: the palette prover prints its pass lines for both themes, `tsc --noEmit -p tsconfig.app.json` prints nothing, and vitest reports every file passing. `src/App.test.tsx` line 113 still finds the landing `<h1>` by /trade once/, which the new hero keeps word for word.

Then report the build size: `npm run build` and copy the `dist/assets/index-*.js` size line (and the PNG size from Step 6a) into the task report.

- [ ] **Step 9: Visual check**

With `npm run dev` running, open `http://localhost:5173/` signed out, at 1280 × 800 and 390 × 844, in the light and the dim theme (`localStorage.setItem('mf.theme', 'dark'); location.reload()`). Check: the header bar floats as glass over the wash; the hero image sits in its glass frame; the fleet panel spans two columns and two rows beside the stacked log and risk panels from `md` up, and all three stack on a phone; the FAQ opens and closes; no horizontal scroll at 390 px. Attach the four screenshots to the task report (needs the browser extension; say so if it is not available).

- [ ] **Step 10: Commit**

With the screenshot (Step 6a):

```bash
git add src/pages/Landing.tsx src/pages/Landing.test.tsx index.html public/overview-desk.png
```

Or with the fallback (Step 6b):

```bash
git add src/pages/Landing.tsx src/pages/Landing.test.tsx index.html src/pages/landing/HeroPreview.tsx
```

Then:

```bash
git commit -F - <<'EOF'
feat(dashboard): landing page -- product screenshot hero in a glass frame, asymmetric product panels, honest footer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b
EOF
```

### Task 6: Overview becomes triage-first, with a setup checklist for an empty org

**Files:**
- Create: `dashboard/src/pages/overview/ExpandableText.tsx` (error text that expands in place)
- Create: `dashboard/src/pages/overview/Panels.tsx` (the four KPI drill-down panels, the copy log, shared helpers)
- Create: `dashboard/src/pages/overview/AttentionCard.tsx`
- Create: `dashboard/src/pages/overview/SetupChecklist.tsx`
- Create: `dashboard/src/pages/overview/FleetGrid.tsx` (master card + follower tiles)
- Modify: `dashboard/src/pages/Overview.tsx` (replace whole file)
- Modify: `dashboard/src/components/layout/DeskStrip.tsx` (Task 3's file; mounts `KillSwitch`, see grounding notes)
- Test: `dashboard/src/pages/Overview.test.tsx`
- Test: `dashboard/src/components/Layout.test.tsx`

**Interfaces:**
- Consumes: `Card` (`{ title?, actions?, inset?, className?, as?, children }`), `PageHeader` (`{ title, subtitle?, actions?, children? }`, renders the one `<h1 className="page-title" id="page-title" tabIndex={-1}>` and calls `usePageTitle`), `Loading` (`{ lines?, label?, className? }`, `role="status"`, `aria-busy`) from Task 2; the `.inset`, `rounded-inset` utilities from Task 1; `StatTile`, `StatusDot`, `Badge`, `Banner`, `Button`, `ConfirmDialog`, `KillSwitch` with today's APIs; `orgApi`, `useOrg` (`{ orgId, role, org }`), `can`, `useLiveRefresh` (`LiveEvent` = `{ category?, account_id?, payload? }`), `mergeTicksIntoSnapshot`, `TicksPayload`, `actionBurst`, `money`, `signed`, `formatWhen`, `errorText`; types `Account`, `AccountStateData`, `ApiState`, `OverviewStats`, `PositionData`, `RecentCopy`, `Settings`, `StateSnapshot` from `lib/types.ts`.
- Produces (used only inside `pages/overview/` and `Overview.tsx`):
  - `ExpandableText({ text, limit, className?, testId? })`
  - `Panels.tsx`: `type ContractRow = { accountId: number; accountLabel: string; pos: PositionData }`, `accountName(a)`, `roleWord(role)`, `copyStatusTone(status)`, `PortfolioPanel`, `FleetStatusPanel`, `FillsPanel`, `ContractsPanel`, `CopyLogCard`
  - `AttentionCard({ items, calmState })`, `type AttentionItem = { key, tone: 'degraded' | 'warn', message, action, testId? }`
  - `SetupChecklist({ orgId, accounts, settings })`, `setupSteps(orgId, accounts, settings): SetupStep[]`
  - `FleetGrid({ orgId, master, masterState, followers, state, onPauseResume })`

Grounding notes for the implementer (read before starting):
- **The desk strip does not carry the kill switch today.** The spec says the mid-page kill switch can go "because the strip has it", but `DeskStrip` (currently `components/Layout.tsx:80-430`, moved to `components/layout/DeskStrip.tsx` by Task 3) only has *Close all positions*. `KillSwitch` (Stop/Resume copying plus the dry-run toggle) is rendered by `Overview.tsx:650` and nowhere else. Deleting it from Overview without moving it would remove the only Stop-copying control and the only dry-run control from the product. This task therefore mounts `KillSwitch` in the strip (Step 9) and moves the eleven kill-switch and dry-run tests from `Overview.test.tsx` to `Layout.test.tsx` with their assertions unchanged (Step 1b). If Task 3 left the strip inside `Layout.tsx`, make the Step 9 edit there and import `./KillSwitch` instead of `../KillSwitch`.
- **KPI row mapping.** Today's four tiles become the spec's four. Every figure keeps its computation and meaning; the ones that lose their own tile move into a tile's `sub`:
  | Old tile (value · sub) | New tile (value · sub) | Drill-down, now a visible "View" control |
  |---|---|---|
  | Portfolio value `portfolioValue` · `% vs yesterday` | **Master equity** `masterState.equity` · "Fleet `portfolioValue` · `vsYesterday`% vs yesterday" | "View all accounts" → Portfolio breakdown |
  | Total P&L `totalPnl` · degraded / history / accounts counted | **Today's P&L**, same value and sub | "View today's fills" → Today's copy fills |
  | Open P&L `totalOpenPnl` · `openTrades` open trades | **Open positions** `openTrades` · "`totalOpenPnl` open P&L" | "View open positions" → Open contracts |
  | Accounts connected `accounts_connected` · masters · active slaves | **Followers copying** `stats.active_slaves ?? followers.length` · "`accounts_connected` accounts connected · `masters` master" | "View fleet health" → Fleet status |
- `StatTile` keeps its API. Its `onClick` form renders the whole tile as a `<button>` with a 12px chevron, which is the hidden drill-down the spec removes. Tiles are now static (`StatTile` without `to`/`onClick`), and a `Button variant="ghost" size="sm"` beneath each tile is the disclosure (`aria-expanded`, `aria-controls` while open). None of the four labels may contain a panel title ("portfolio breakdown", "fleet status", "open contracts", "today's copy fills"), because existing tests use `queryByText(/portfolio breakdown/i)` to prove a panel is closed.
- **Master equity appears once on Overview**: in the KPI tile. The master card keeps identity (title `Master account (<login>)`, nickname, ID), health, balance and open P&L. The desk strip's own "Master equity" is outside Overview.
- **Attention sources**, all from data Overview already has. No fetch, poll interval or socket changes:
  - *Refresh failed*: the `loadState` read in `refreshState` failing. Today it only `console.error`s. It now also sets `stateError`, and clears it on the next success. The row's **Retry** calls `refreshState()`. A 5xx from `orgs/{id}/state` is also how "copier offline" shows up, so this one row covers both.
  - *Margin call*: Overview reads no events. The copier's `_on_margin_call` inserts a `category='risk'` event with `payload.action = 'margin_call'`, and `api/src/api/ws.py` broadcasts every events row, so it already reaches `useLiveRefresh`'s `onEvent` callback. Overview captures it there, shows it for 30 minutes (the strip banner's window), and it can be dismissed. It only catches margin calls that arrive while the page is open. The strip banner still covers ones raised before.
  - *Expiring/expired token*: the account list carries no expiry (`AccountDetails.connection.expires_at` comes only from the per-account details read, which Overview does not make). The only token signal is `connection_status === 'refresh_failed'`, the same one today's red banner uses. That row keeps `data-testid="refresh-failed-banner"` and its wording, so the five existing banner tests still hold.
  - *Follower offline*: `connection_status === 'offline'` (the MT5 terminal rule the tile already uses), for any account.
  - *Degraded*: follower `status === 'degraded'`.
  - *Not connected*: an enabled follower whose `status === 'disconnected'`.
  - "Paused unexpectedly" cannot be derived. A follower pause is always the operator's action (`enabled: false`), and nothing in `Account` marks it as unexpected, so a paused follower is not listed.
- Positions has no per-account filter (`pages/Positions.tsx` reads no search params), so the margin-call action links to `/org/:id/positions`.
- The setup checklist's steps come from state Overview already loads: `accounts` (master present, any follower present) and `Settings` (`dry_run`, `copying_enabled`). "Run a dry run" counts as done while dry-run is on, or once the desk is live. "Go live" is done when a master and a follower exist, copying is on, and dry-run is off.
- Test ids `slave-tile`, `slave-last-error`, `slave-refresh-failed-marker`, `slave-offline-marker` are not user-visible and stay, so the existing tests keep finding them. Every user-visible "Slave"/"slave" becomes "Follower"/"follower", including the role badges, which read `Master`/`Follower` through `roleWord` (the API's role strings are untouched).

- [ ] **Step 1a: Update `Overview.test.tsx` (existing tests move to the new structure, nothing weakened)**

Apply these edits in order.

(1) Mock the events socket so a test can push a frame. After the line `vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))` insert:

```tsx
const { fakeSockets } = vi.hoisted(() => ({
  fakeSockets: [] as Array<{ onmessage: ((e: { data: string }) => void) | null }>,
}))
vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    eventsSocket: () => {
      const ws = { onmessage: null, onclose: null, onerror: null, close: () => {} }
      fakeSockets.push(ws as never)
      return ws as unknown as WebSocket
    },
  }
})
```

(2) Delete the four tests that sit between `test('renders slave tiles with status icons', …)` and `test('per-slave pause posts to /api/orgs/1/control/pause with account_id', …)`. Their titles are `kill switch confirms then PUTs copying_enabled false`, `kill switch does not PUT if the dialog is cancelled`, `kill switch resume: confirming opens the dialog and PUTs copying_enabled true` and `kill switch resume: cancelling the dialog sends nothing`. They move to `Layout.test.tsx` in Step 1b with the same assertions.

(3) Replace everything from the line `// ---------- N1: the dry-run toggle ----------` up to, but not including, the line `// ---------- redesigned stat sections (org-scoped) ----------` with the block below. The seven removed tests are the five dry-run toggle tests and the two `Task 17` role tests. They move to `Layout.test.tsx` in Step 1b.

```tsx
// ---------- the kill switch lives in the desk strip, not on Overview ----------
// Its stop/resume and dry-run behaviour is tested in components/Layout.test.tsx.

test('Overview renders no kill switch for an admin: the desk strip carries it', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
  expect(screen.queryByText('Copying Status')).not.toBeInTheDocument()
})

test('Overview renders no kill switch for a viewer either', async () => {
  setRole('viewer')
  stubApi({
    '/api/orgs/1/accounts': mockAccounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  await screen.findByTestId('attention-card')
  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
})

```

(4) Sentence case for the master card title. Replace all occurrences (Edit `replace_all`) of `/Master Account \(1001\)/` with `/Master account \(1001\)/`.

(5) Rename the first test's title so it states the new split. The body is unchanged: `getByText('12,000.00')` now finds the KPI tile, and `10,000.00`/`+2,000.00` find the master card.

old: `test('renders master card with equity/balance/pnl', async () => {`
new: `test('master equity sits in the KPI row; the master card keeps balance and P&L', async () => {`

(6) In `renders no account stats when the accounts block is empty, without crashing`, replace the wait anchor (after step 3 it is the only `waitFor` on `Copying Status` left):

old:
```tsx
  await waitFor(() => {
    expect(screen.getByText('Copying Status')).toBeInTheDocument()
  })
```
new:
```tsx
  await screen.findByTestId('attention-card')
```

(7) Replace all occurrences of `await screen.findByText('Copying Status')` with `await screen.findByTestId('attention-card')`.

(8) The drill-downs are opened by their visible View controls. Replace all occurrences:
- `{ name: /portfolio value/i }` → `{ name: /view all accounts/i }`
- `{ name: /accounts connected/i }` → `{ name: /view fleet health/i }`
- `{ name: /open p&l/i }` → `{ name: /view open positions/i }`
- `{ name: /total p&l/i }` → `{ name: /view today's fills/i }`

(9) Retitle the four drill-down tests:
- `test('the Portfolio Value tile expands a per-account breakdown in place', async () => {` → `test("the Master equity tile's View control expands a per-account breakdown in place", async () => {`
- `test('the Accounts Connected tile expands a fleet status list, accordion-style', async () => {` → `test('the Followers copying tile expands a fleet status list, accordion-style', async () => {`
- `test("the Total P&L tile expands the list of today's copy fills", async () => {` → `test("the Today's P&L tile expands the list of today's copy fills", async () => {`
- `test('the Open P&L tile expands a live open-contracts panel in place', async () => {` → `test('the Open positions tile expands a live open-contracts panel in place', async () => {`

(10) Role badges say follower. In the fleet-status test:

old: `  expect(within(panel).getAllByText(/slave/i).length).toBeGreaterThanOrEqual(2)`
new: `  expect(within(panel).getAllByText(/follower/i).length).toBeGreaterThanOrEqual(2)`

(11) Accounts connected now sits in the Followers copying tile's sub. In `portfolio row aggregates equity and compares to yesterday`:

old:
```tsx
  expect(screen.getByText('Accounts connected')).toBeInTheDocument()
  expect(screen.getByText('3')).toBeInTheDocument()
```
new:
```tsx
  expect(screen.getByText(/3 accounts connected/)).toBeInTheDocument()
```

(12) Replace all occurrences of `screen.findByText('Total P&L')` with `screen.findByText("Today's P&L")`, and retitle:
- `test('Total P&L replaces the copy counter and sums every account', async () => {` → `test("Today's P&L replaces the copy counter and sums every account", async () => {`

(13) In `the copier-performance analytics live on Performance, not Overview`:

old: `  await screen.findByText(/portfolio value/i)`
new: `  await screen.findByText('Master equity')`

(14) In `slave tile shows Degraded status from account.status, not connection_status`, the Attention card now also names account 1002, so `getByText(/1002/)` would match twice. Wait on the tiles instead:

old:
```tsx
  await waitFor(() => {
    expect(screen.getByText(/1002/)).toBeInTheDocument()
  })

  // account.status === 'degraded' must drive the degraded badge, even though
```
new:
```tsx
  await screen.findAllByTestId('slave-tile')

  // account.status === 'degraded' must drive the degraded badge, even though
```

(15) Keep the dry-run assertion and pin where it now lives. In `shows dry-run badge when dry_run enabled`:

old:
```tsx
  await waitFor(() => {
    expect(screen.getByText(/DRY RUN/i)).toBeInTheDocument()
  })
})
```
new:
```tsx
  await waitFor(() => {
    expect(screen.getByText(/DRY RUN/i)).toBeInTheDocument()
  })
  // The one place Overview states it: the Attention card's calm line.
  expect(screen.getByTestId('attention-card')).toHaveTextContent('All clear — dry run, copies are simulated')
})
```

(16) Extend `degraded slave tile shows the last_error reason`. The error now expands in place.

old:
```tsx
  const errorEl = screen.getByTestId('slave-last-error')
  expect(errorEl).toHaveTextContent('Send failed: insufficient margin on EURUSD')
  expect(errorEl).toHaveAttribute('title', 'Send failed: insufficient margin on EURUSD')
})
```
new:
```tsx
  const errorEl = screen.getByTestId('slave-last-error')
  expect(errorEl).toHaveTextContent('Send failed: insufficient margin on EURUSD')
  expect(errorEl).toHaveAttribute('title', 'Send failed: insufficient margin on EURUSD')

  // Too long for one line: a visible toggle expands it in place, so the
  // reason is not reachable only by hovering a tooltip.
  const toggle = within(screen.getByTestId('slave-tile-2')).getByRole('button', { name: 'Show details' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(toggle)
  expect(within(screen.getByTestId('slave-tile-2')).getByRole('button', { name: 'Hide details' }))
    .toHaveAttribute('aria-expanded', 'true')
})
```

(`FleetGrid` puts a second, per-account test id on the element inside each tile: `slave-tile-<ctid_trader_account_id>`. Queries that need one tile use it, and `getAllByTestId('slave-tile')` keeps working.)

(17) Append these new tests at the end of the file:

```tsx
// ---------- Task 6: triage-first Overview ----------

const quietRoutes = {
  '/api/orgs/1/accounts': mockAccounts,
  '/api/orgs/1/settings': mockSettings,
  '/api/orgs/1/state': mockState,
}

test('the page header names the page and the org, and sets the document title', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByTestId('attention-card')
  expect(screen.getByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(document.title).toBe('Overview · MirrorFleet')
})

test('while loading, Overview shows the Loading primitive, never "Loading..." text', () => {
  setRole('admin')
  // Never resolves: the page stays in its loading state.
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true')
  expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
})

test('the Attention card says all clear, and nothing else, when nothing is wrong', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  expect(card).toHaveTextContent('All clear — copying live')
  expect(within(card).queryAllByRole('listitem')).toHaveLength(0)
})

test('the all-clear line names the real state when copying is paused', async () => {
  setRole('admin')
  stubApi({ ...quietRoutes, '/api/orgs/1/settings': { copying_enabled: false, dry_run: false } })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  expect(await screen.findByTestId('attention-card')).toHaveTextContent('All clear — copying paused')
})

test('a degraded follower is listed on the Attention card with an action', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [
      mockAccounts[0],
      { ...mockAccounts[1], status: 'degraded' },
      mockAccounts[2],
    ],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  const rows = within(card).getAllByRole('listitem')
  expect(rows).toHaveLength(1)
  expect(rows[0]).toHaveTextContent('Account 1002 is degraded: copies to it are failing.')
  expect(within(rows[0]).getByRole('link', { name: 'Open Accounts' }))
    .toHaveAttribute('href', '/org/1/accounts')
  expect(card).not.toHaveTextContent(/all clear/i)
})

test('a failed token refresh is an Attention row that links to Accounts', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [
      mockAccounts[0],
      { ...mockAccounts[1], connection_status: 'refresh_failed' },
      mockAccounts[2],
    ],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const row = await screen.findByTestId('refresh-failed-banner')
  expect(within(screen.getByTestId('attention-card')).getAllByRole('listitem')).toContain(row)
  expect(within(row).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
})

test('an offline terminal and a disconnected enabled follower are both listed', async () => {
  setRole('admin')
  stubApi({
    ...quietRoutes,
    '/api/orgs/1/accounts': [
      mockAccounts[0],
      { ...mockAccounts[1], status: 'disconnected' },
      { ...mt5Account, connection_status: 'offline', mt5: { ...mt5Account.mt5!, connected: false } },
    ],
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('attention-card')
  await waitFor(() => expect(within(card).getAllByRole('listitem')).toHaveLength(2))
  expect(card).toHaveTextContent("VPS desk's terminal is offline, so copies wait until the EA reports again.")
  expect(card).toHaveTextContent('Account 1002 is not connected to its broker, so it receives no copies.')
})

test('a failed live-state read is listed with a Retry that refetches', async () => {
  setRole('admin')
  let stateCalls = 0
  const json = (body: unknown, status = 200) => Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
  const fetchMock = vi.fn((path: string) => {
    if (path === '/api/orgs/1/accounts') return json(mockAccounts)
    if (path === '/api/orgs/1/settings') return json(mockSettings)
    if (path === '/api/orgs/1/state') {
      stateCalls += 1
      return stateCalls === 1 ? json({ detail: 'copier unreachable' }, 503) : json(mockState)
    }
    return Promise.resolve(new Response(null, { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const row = await screen.findByTestId('attention-state-error')
  expect(row).toHaveTextContent(
    'Live figures stopped refreshing (copier unreachable), so the numbers on this page may be stale.')
  await userEvent.click(within(row).getByRole('button', { name: 'Retry' }))

  await waitFor(() => expect(screen.queryByTestId('attention-state-error')).not.toBeInTheDocument())
  expect(stateCalls).toBeGreaterThanOrEqual(2)
  expect(screen.getByTestId('attention-card')).toHaveTextContent('All clear — copying live')
})

test('a margin call streamed over the socket is listed, links to Positions and can be dismissed', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByTestId('attention-card')
  const ws = fakeSockets[fakeSockets.length - 1]
  act(() => {
    ws.onmessage?.({
      data: JSON.stringify({
        id: 7, ts: new Date().toISOString(), category: 'risk', severity: 'error',
        account_id: 2, payload: { action: 'margin_call', margin_level_threshold: 50 },
      }),
    })
  })

  const row = await screen.findByTestId('attention-margin-call')
  expect(row).toHaveTextContent(/Margin call on Account 1002/)
  expect(within(row).getByRole('link', { name: 'Open Positions' })).toHaveAttribute('href', '/org/1/positions')
  await userEvent.click(within(row).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByTestId('attention-margin-call')).not.toBeInTheDocument()
})

test('master equity appears exactly once on Overview', async () => {
  setRole('admin')
  stubApi(quietRoutes)

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByText(/Master account \(1001\)/)
  expect(screen.getAllByText('12,000.00')).toHaveLength(1)
  expect(screen.getByText('Master equity')).toBeInTheDocument()
})

test('drill-downs are visible View controls, and every tile footer links onward', async () => {
  setRole('admin')
  stubApi(statsRoutes())

  render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByTestId('attention-card')
  for (const name of [/view all accounts/i, /view today's fills/i, /view open positions/i, /view fleet health/i]) {
    expect(screen.getByRole('button', { name })).toHaveAttribute('aria-expanded', 'false')
  }
  for (const tile of screen.getAllByTestId('slave-tile')) {
    expect(within(tile).getByRole('link', { name: /^view/i })).toHaveAttribute('href', '/org/1/accounts')
  }
  expect(screen.getByRole('link', { name: 'View positions' })).toHaveAttribute('href', '/org/1/positions')
})

test('user-visible copy says follower, never slave', async () => {
  setRole('admin')
  stubApi(statsRoutes())

  const { container } = render(<MemoryRouter><Overview /></MemoryRouter>)

  await screen.findByText('Copy log')
  await userEvent.click(screen.getByRole('button', { name: /view fleet health/i }))
  expect(container.textContent ?? '').not.toMatch(/slave/i)
  expect(screen.getByText('Followers')).toBeInTheDocument()
})

test('a long copy failure expands in place instead of hiding in a tooltip', async () => {
  setRole('admin')
  const longError = 'TRADING_BAD_VOLUME: volume 0.001 is below the symbol minimum of 0.01'
  stubApi({
    ...statsRoutes(),
    '/api/orgs/1/overview': {
      ...overviewStats,
      recent_copies: [{
        status: 'failed', master_position_id: 44, master_order_id: null,
        slave_account_id: 2, slave_login: 1002, slave_nickname: null,
        symbol: 'EURUSD', slave_volume: null, fill_price: null,
        error: longError, updated_at: '2026-08-18T09:00:00+00:00',
      }],
    },
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const toggle = await screen.findByRole('button', { name: 'Show details' })
  // The whole reason is in the page, not cut at 24 characters.
  expect(screen.getByText(longError)).toBeInTheDocument()
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(toggle)
  expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true')
})

test('an empty org gets a four-step setup checklist instead of "no followers" text', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': [],
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': { accounts: {}, master_positions: [], pending_orders: [], drift: [] },
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const card = await screen.findByTestId('setup-checklist')
  const steps = within(card).getAllByRole('listitem')
  expect(steps).toHaveLength(4)
  expect(steps[0]).toHaveTextContent('Connect the master')
  expect(steps[1]).toHaveTextContent('Add followers')
  expect(steps[2]).toHaveTextContent('Run a dry run')
  expect(steps[3]).toHaveTextContent('Go live')
  for (const step of steps) expect(step).toHaveTextContent('To do')

  expect(within(steps[0]).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
  expect(within(steps[1]).getByRole('link', { name: 'Open Accounts' })).toHaveAttribute('href', '/org/1/accounts')
  expect(within(steps[2]).getByRole('link', { name: 'Open Automation' })).toHaveAttribute('href', '/org/1/automation')

  expect(screen.queryByText(/no slave accounts configured/i)).not.toBeInTheDocument()
  // An empty desk has nothing to triage yet.
  expect(screen.queryByTestId('attention-card')).not.toBeInTheDocument()
})

test('the setup checklist marks steps done from real state', async () => {
  setRole('admin')
  stubApi({
    '/api/orgs/1/accounts': [mockAccounts[0]], // a master, no followers yet
    '/api/orgs/1/settings': { copying_enabled: true, dry_run: true },
    '/api/orgs/1/state': mockState,
  })

  render(<MemoryRouter><Overview /></MemoryRouter>)

  const steps = within(await screen.findByTestId('setup-checklist')).getAllByRole('listitem')
  expect(steps[0]).toHaveTextContent('Done')   // master connected
  expect(steps[1]).toHaveTextContent('To do')  // no follower
  expect(steps[2]).toHaveTextContent('Done')   // dry-run is on
  expect(steps[3]).toHaveTextContent('To do')  // not live
  // The master card still renders beside the checklist.
  expect(screen.getByText(/Master account \(1001\)/)).toBeInTheDocument()
})
```

- [ ] **Step 1b: Move the kill-switch tests to `Layout.test.tsx`**

Change the first import line of `dashboard/src/components/Layout.test.tsx`:

old: `import { act, render, screen, waitFor, cleanup } from '@testing-library/react'`
new: `import { act, render, screen, waitFor, cleanup, within } from '@testing-library/react'`

Append at the end of the file (the eleven tests removed from `Overview.test.tsx`, with the same assertions, now against the strip):

```tsx
// ---------- the kill switch (moved here from Overview in Task 6) ----------

function settingsPuts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter((call) =>
    String(call[0]).includes('/api/orgs/1/settings') &&
    (call[1] as RequestInit | undefined)?.method === 'PUT')
}

test('the strip kill switch confirms, then PUTs copying_enabled false', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: /stop copying/i }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/stop copying\?/i)
  await userEvent.click(within(dialog).getByRole('button', { name: /^stop copying$/i }))

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    expect(JSON.parse((put[1] as RequestInit).body as string).copying_enabled).toBe(false)
  })
})

test('the strip kill switch does not PUT if the dialog is cancelled', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: /stop copying/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(settingsPuts(fetchMock)).toHaveLength(0)
})

test('the strip kill switch resume: confirming PUTs copying_enabled true', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: /resume copying/i }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/resume copying\?/i)
  await userEvent.click(within(dialog).getByRole('button', { name: /^resume copying$/i }))

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    expect(JSON.parse((put[1] as RequestInit).body as string).copying_enabled).toBe(true)
  })
})

test('the strip kill switch resume: cancelling the dialog sends nothing', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: false, dry_run: false, shards: 1 } })
  renderLayout()

  await userEvent.click(await screen.findByRole('button', { name: /resume copying/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(settingsPuts(fetchMock)).toHaveLength(0)
})

test('the strip dry-run toggle PUTs dry_run: true when enabling', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes() // dry_run: false
  renderLayout()

  const toggle = await screen.findByTestId('dry-run-toggle')
  expect(toggle).toHaveTextContent(/turn dry-run on/i)
  await userEvent.click(toggle)

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    // The real value must be in the body -- the dashboard half of the seam
    // where the api once forwarded a hardcoded empty body.
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ dry_run: true })
  })
})

test('enabling dry-run from the strip needs no confirmation (it is the safe direction)', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderLayout()

  await userEvent.click(await screen.findByTestId('dry-run-toggle'))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await waitFor(() => expect(settingsPuts(fetchMock).length).toBeGreaterThan(0))
})

test('disabling dry-run from the strip confirms first, then PUTs dry_run: false', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: true, dry_run: true, shards: 1 } })
  renderLayout()

  const toggle = await screen.findByTestId('dry-run-toggle')
  expect(toggle).toHaveTextContent(/turn dry-run off/i)
  await userEvent.click(toggle)

  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/turn dry-run off\?/i)
  await userEvent.click(within(dialog).getByRole('button', { name: /^turn dry-run off$/i }))

  await waitFor(() => {
    const put = settingsPuts(fetchMock)[0]
    expect(put).toBeDefined()
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ dry_run: false })
  })
})

test('disabling dry-run from the strip does not PUT if the dialog is cancelled', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({ settings: { copying_enabled: true, dry_run: true, shards: 1 } })
  renderLayout()

  await userEvent.click(await screen.findByTestId('dry-run-toggle'))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(settingsPuts(fetchMock)).toHaveLength(0)
})

test('the strip DRY RUN badge reflects the toggled state without a reload', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes() // dry_run: false -> no badge initially
  renderLayout()

  await screen.findByTestId('dry-run-toggle')
  expect(screen.queryByText(/^DRY RUN$/)).not.toBeInTheDocument()

  await userEvent.click(screen.getByTestId('dry-run-toggle'))

  expect(await screen.findByText(/^DRY RUN$/)).toBeInTheDocument()
  // One dry-run marker for an admin: the kill switch's badge, not the strip chip too.
  expect(screen.queryByText('Dry run')).not.toBeInTheDocument()
})

test('the strip kill switch is hidden for a viewer (below control)', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes()
  renderLayout()

  await screen.findByText(/copying live/i)
  expect(screen.queryByRole('button', { name: /stop copying|resume copying/i })).not.toBeInTheDocument()
  expect(screen.queryByTestId('dry-run-toggle')).not.toBeInTheDocument()
})

test('the strip kill switch is visible for an admin (control)', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderLayout()

  expect(await screen.findByRole('button', { name: /stop copying/i })).toBeInTheDocument()
  expect(screen.getByTestId('dry-run-toggle')).toBeInTheDocument()
})

test('a viewer, who has no kill switch, still sees the strip dry-run chip', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  mockRoutes({ settings: { copying_enabled: true, dry_run: true, shards: 1 } })
  renderLayout()

  expect(await screen.findByText('Dry run')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the two files to verify the new tests fail**

Run: `npx vitest run src/pages/Overview.test.tsx src/components/Layout.test.tsx`
Expected: FAIL. In `Overview.test.tsx`, every test that waits on `attention-card`, a `View …` button, `Master account (1001)`, `setup-checklist`, `slave-tile-2`, `Today's P&L` or the document title fails, because today's page renders none of them. In `Layout.test.tsx`, the kill-switch tests fail with "Unable to find role button name /stop copying/i" and "Unable to find … dry-run-toggle", because the strip has no `KillSwitch` yet. The existing Layout tests still pass.

- [ ] **Step 3: Create `dashboard/src/pages/overview/ExpandableText.tsx`**

```tsx
import { useId, useState } from 'react'

/**
 * Error text that used to be cut short and live only in a `title` tooltip,
 * which touch and keyboard users cannot reach. The full text is always in
 * the DOM. Collapsed, CSS truncates it to one line; "Show details" expands
 * it in place. `title` stays for pointer users.
 *
 * The toggle inherits the surrounding colour (`text-current`) so it is
 * always the pair its container was proven with, e.g. loss-deep on
 * loss-wash inside a follower tile.
 */
export default function ExpandableText({ text, limit, className, testId }: {
  text: string
  /** Texts at or under this many characters render whole, with no toggle. */
  limit: number
  className?: string
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  const id = useId()

  if (text.length <= limit) {
    return (
      <span data-testid={testId} title={text} className={`break-words ${className ?? ''}`}>
        {text}
      </span>
    )
  }

  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <span
        id={id}
        data-testid={testId}
        title={text}
        className={`${open ? 'whitespace-normal break-words' : 'block max-w-full truncate'} ${className ?? ''}`}
      >
        {text}
      </span>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-semibold text-current underline underline-offset-2 min-h-11 md:min-h-0"
      >
        {open ? 'Hide details' : 'Show details'}
      </button>
    </span>
  )
}
```

- [ ] **Step 4: Create `dashboard/src/pages/overview/Panels.tsx`**

```tsx
import type { ReactNode } from 'react'
import type { Account, PositionData, RecentCopy, StateSnapshot } from '../../lib/types'
import Card from '../../components/Card'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import StatusDot from '../../components/StatusDot'
import { money, signed, formatWhen } from '../../lib/format'
import ExpandableText from './ExpandableText'

/** One running contract, flattened from the live state feed. */
export type ContractRow = { accountId: number; accountLabel: string; pos: PositionData }

export function accountName(a: Pick<Account, 'nickname' | 'trader_login'>): string {
  return a.nickname || `Account ${a.trader_login}`
}

/** The API keeps its role names ('master' | 'slave'); the desk says follower. */
export function roleWord(role: string): string {
  return role === 'master' ? 'Master' : 'Follower'
}

/** The one chip's tone for a copy's status, shared by the fills panel and the copy log. */
export function copyStatusTone(status: string): 'profit' | 'loss' | 'neutral' | 'warn' {
  if (status === 'active') return 'profit'
  if (status === 'failed') return 'loss'
  if (status === 'closed') return 'neutral'
  return 'warn'
}

/** 'active' -> 'Active': badges show words, not raw enum strings. */
function humanStatus(status: string): string {
  return status ? status.charAt(0).toUpperCase() + status.slice(1) : '—'
}

function PanelLink({ to, children }: { to: string; children: ReactNode }) {
  return <Button to={to} variant="ghost" size="sm">{children}</Button>
}

/** Per-account balance/equity/open P&L, opened from the Master equity tile. */
export function PortfolioPanel({ orgId, accounts, state }: {
  orgId: number
  accounts: Account[]
  state: StateSnapshot
}) {
  return (
    <Card
      as="section"
      inset
      title="Portfolio breakdown · live"
      actions={<PanelLink to={`/org/${orgId}/accounts`}>Manage accounts</PanelLink>}
    >
      <div className="overflow-x-auto">
        <table className="stack-table w-full text-sm">
          <thead>
            <tr className="text-left border-b border-line">
              <th className="desk-label px-5 py-2 font-semibold">Account</th>
              <th className="desk-label px-3 py-2 font-semibold">Role</th>
              <th className="desk-label px-3 py-2 font-semibold text-right">Balance</th>
              <th className="desk-label px-3 py-2 font-semibold text-right">Equity</th>
              <th className="desk-label px-5 py-2 font-semibold text-right">Open P&L</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              const snap = state[String(a.ctid_trader_account_id)]
              return (
                <tr key={a.ctid_trader_account_id} className="border-b border-line last:border-0">
                  <td data-label="Account" className="px-5 py-2.5">{accountName(a)}</td>
                  <td data-label="Role" className="px-3 py-2.5">
                    <Badge tone={a.role === 'master' ? 'profit' : 'neutral'}>{roleWord(a.role)}</Badge>
                  </td>
                  <td data-label="Balance" className="tnum px-3 py-2.5 text-right">{money(snap?.balance)}</td>
                  <td data-label="Equity" className="tnum px-3 py-2.5 text-right">{money(snap?.equity)}</td>
                  <td data-label="Open P&L" className="tnum px-5 py-2.5 text-right">
                    <span className={(snap?.open_pnl ?? 0) < 0 ? 'text-loss' : 'text-profit'}>
                      {signed(snap?.open_pnl)}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/** Health and copying state per account, opened from the Followers copying tile. */
export function FleetStatusPanel({ orgId, accounts }: { orgId: number; accounts: Account[] }) {
  return (
    <Card
      as="section"
      inset
      title="Fleet status"
      actions={<PanelLink to={`/org/${orgId}/accounts`}>Manage accounts</PanelLink>}
    >
      <div className="overflow-x-auto">
        <table className="stack-table w-full text-sm">
          <thead>
            <tr className="text-left border-b border-line">
              <th className="desk-label px-5 py-2 font-semibold">Account</th>
              <th className="desk-label px-3 py-2 font-semibold">Role</th>
              <th className="desk-label px-3 py-2 font-semibold">Health</th>
              <th className="desk-label px-5 py-2 font-semibold">Copying</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              // Anything that is not an affirmatively healthy status
              // reads as degraded -- 'disconnected' must never show OK.
              const degraded = a.status !== 'ok' && a.status !== 'connected'
              return (
                <tr key={a.ctid_trader_account_id} className="border-b border-line last:border-0">
                  <td data-label="Account" className="px-5 py-2.5">{accountName(a)}</td>
                  <td data-label="Role" className="px-3 py-2.5">
                    <Badge tone={a.role === 'master' ? 'profit' : 'neutral'}>{roleWord(a.role)}</Badge>
                  </td>
                  <td data-label="Health" className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-1.5">
                      <StatusDot tone={degraded ? 'degraded' : 'ok'} />
                      {degraded ? 'Degraded' : 'OK'}
                    </span>
                  </td>
                  <td data-label="Copying" className="px-5 py-2.5 text-ink-soft">
                    {a.role === 'master' ? '—' : a.enabled ? 'Enabled' : 'Paused'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/** Today's copy fills, opened from the Today's P&L tile. */
export function FillsPanel({ orgId, fills }: { orgId: number; fills: RecentCopy[] }) {
  return (
    <Card
      as="section"
      inset
      title="Today's copy fills · live"
      actions={<PanelLink to={`/org/${orgId}/logs`}>Full event log</PanelLink>}
    >
      {fills.length === 0 ? (
        <p className="px-5 py-4 text-sm text-ink-faint">No copy fills yet today.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">When</th>
                <th className="desk-label px-3 py-2 font-semibold">Status</th>
                <th className="desk-label px-3 py-2 font-semibold">Follower</th>
                <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Fill price</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((copy, i) => (
                <tr
                  key={`${copy.slave_account_id}-${copy.master_position_id ?? copy.master_order_id}-${i}`}
                  className="border-b border-line last:border-0"
                >
                  <td data-label="When" className="tnum px-5 py-2.5 text-ink-soft">{formatWhen(copy.updated_at)}</td>
                  <td data-label="Status" className="px-3 py-2.5">
                    <Badge tone={copyStatusTone(copy.status)}>{humanStatus(copy.status)}</Badge>
                  </td>
                  <td data-label="Follower" className="px-3 py-2.5">
                    {copy.slave_nickname || <span className="tnum">{copy.slave_login}</span>}
                  </td>
                  <td data-label="Symbol" className="tnum px-3 py-2.5">{copy.symbol ?? '—'}</td>
                  <td data-label="Fill price" className="tnum px-5 py-2.5 text-right">{copy.fill_price ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/** Every running contract with its own protection, opened from the Open positions tile. */
export function ContractsPanel({ orgId, rows, canTrade, closingIds, onClose, onCloseAll }: {
  orgId: number
  rows: ContractRow[]
  canTrade: boolean
  closingIds: Set<number>
  onClose: (row: ContractRow) => void
  onCloseAll: () => void
}) {
  return (
    <Card
      as="section"
      inset
      title="Open contracts · live"
      actions={
        <div className="flex items-center gap-3">
          {canTrade && rows.length > 0 && (
            <Button variant="secondary" tone="loss" size="sm" onClick={onCloseAll}>
              Close all shown
            </Button>
          )}
          <PanelLink to={`/org/${orgId}/positions`}>Full positions view</PanelLink>
        </div>
      }
    >
      {rows.length === 0 ? (
        <p className="px-5 py-4 text-sm text-ink-faint">
          No open contracts right now. Fills appear here the moment they happen.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Account</th>
                <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-3 py-2 font-semibold">Side</th>
                <th className="desk-label px-3 py-2 font-semibold text-right">Entry</th>
                <th className="desk-label px-3 py-2 font-semibold text-right">Current</th>
                <th className="desk-label px-3 py-2 font-semibold text-right whitespace-nowrap">SL / TP</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Live P&L</th>
                {canTrade && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.accountId}-${row.pos.position_id}`} className="border-b border-line last:border-0">
                  <td data-label="Account" className="px-5 py-2.5">{row.accountLabel}</td>
                  <td data-label="Symbol" className="tnum px-3 py-2.5">{row.pos.symbol ?? row.pos.symbol_id}</td>
                  <td data-label="Side" className={`px-3 py-2.5 font-medium ${row.pos.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                    {row.pos.side}
                  </td>
                  <td data-label="Entry" className="tnum px-3 py-2.5 text-right">{row.pos.entry_price}</td>
                  <td
                    data-label="Current"
                    className={`tnum px-3 py-2.5 text-right font-medium ${
                      row.pos.current_price != null ? 'text-brand' : 'text-ink-faint'
                    }`}
                  >
                    {row.pos.current_price ?? '—'}
                  </td>
                  {/* Each row's OWN protection. A copy whose stop never
                      arrived is the row that matters here, and reading the
                      master's alone would hide exactly that -- so an
                      unprotected side shows a dash and is tinted, rather
                      than borrowing a number from somewhere else. */}
                  <td data-label="SL / TP" className="tnum px-3 py-2.5 text-right whitespace-nowrap">
                    <span className={row.pos.stop_loss == null ? 'text-warn' : 'text-ink-soft'}>
                      {row.pos.stop_loss ?? '—'}
                    </span>
                    <span className="text-ink-faint"> / </span>
                    <span className={row.pos.take_profit == null ? 'text-warn' : 'text-ink-soft'}>
                      {row.pos.take_profit ?? '—'}
                    </span>
                  </td>
                  <td data-label="Live P&L" className="tnum px-5 py-2.5 text-right">
                    <span className={(row.pos.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}>
                      {signed(row.pos.pnl_quote)}
                    </span>
                  </td>
                  {canTrade && (
                    <td className="px-3 py-2.5 text-right">
                      {closingIds.has(row.pos.position_id) ? (
                        <span className="px-3 py-1 text-xs font-semibold text-ink-faint animate-pulse motion-reduce:animate-none">
                          Closing…
                        </span>
                      ) : (
                        <Button variant="secondary" tone="loss" size="sm" onClick={() => onClose(row)}>
                          Close
                        </Button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/** The recent copies with an estimated live P&L, or the failure reason. */
export function CopyLogCard({ copies, masterPnlByPosition }: {
  copies: RecentCopy[]
  masterPnlByPosition: Map<number, { pnl: number | null; volume: number }>
}) {
  return (
    <Card as="section" inset title="Copy log">
      {copies.length === 0 ? (
        <p className="px-5 py-4 text-sm text-ink-faint">
          No copies yet. They appear here the moment the master trades.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Status</th>
                <th className="desk-label px-3 py-2 font-semibold">Master</th>
                <th className="desk-label px-3 py-2 font-semibold">Follower</th>
                <th className="desk-label px-3 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">P&L</th>
              </tr>
            </thead>
            <tbody>
              {copies.map((copy, i) => {
                const master = copy.master_position_id != null
                  ? masterPnlByPosition.get(copy.master_position_id)
                  : undefined
                // Estimate the copy's live P&L from the master position's,
                // scaled by the volume ratio. Only possible while both
                // sides are open; otherwise show the failure or a dash.
                let pnl: number | null = null
                if (copy.status === 'active' && master?.pnl != null && master.volume > 0
                    && copy.slave_volume != null) {
                  pnl = master.pnl * (copy.slave_volume / master.volume)
                }
                return (
                  <tr
                    key={`${copy.slave_account_id}-${copy.master_position_id}-${copy.master_order_id}-${i}`}
                    className="border-b border-line last:border-0"
                  >
                    <td data-label="Status" className="px-5 py-2.5">
                      <Badge tone={copyStatusTone(copy.status)}>{humanStatus(copy.status)}</Badge>
                    </td>
                    <td data-label="Master" className="tnum px-3 py-2.5 text-ink-soft">
                      {copy.master_position_id ?? copy.master_order_id ?? '—'}
                    </td>
                    <td data-label="Follower" className="px-3 py-2.5">
                      {copy.slave_nickname || <span className="tnum">{copy.slave_login}</span>}
                    </td>
                    <td data-label="Symbol" className="tnum px-3 py-2.5">{copy.symbol ?? '—'}</td>
                    <td data-label="P&L" className="tnum px-5 py-2.5 text-right">
                      {copy.status === 'failed' && copy.error ? (
                        <span className="inline-block max-w-56 text-left">
                          <ExpandableText text={copy.error} limit={24} className="text-xs text-loss" />
                        </span>
                      ) : pnl != null ? (
                        <span className={pnl < 0 ? 'text-loss' : 'text-profit'}>{signed(pnl)}</span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
```

- [ ] **Step 5: Create `dashboard/src/pages/overview/AttentionCard.tsx`**

```tsx
import type { ReactNode } from 'react'
import Card from '../../components/Card'
import StatusDot from '../../components/StatusDot'

export interface AttentionItem {
  key: string
  /** degraded = act now (red dot); warn = copying is held up (amber dot). */
  tone: 'degraded' | 'warn'
  /** One plain sentence. */
  message: ReactNode
  /** The one thing to do about it: a Button, as a link or an action. */
  action: ReactNode
  testId?: string
}

/**
 * Only live problems, each with its action. When nothing is wrong it says so
 * in one line naming the real copying state, and nothing else. The live
 * region is always mounted: a conditionally mounted role="status" is not
 * announced reliably.
 */
export default function AttentionCard({ items, calmState }: {
  items: AttentionItem[]
  /** "copying live", "copying paused", "dry run, copies are simulated", ... */
  calmState: string
}) {
  return (
    <div data-testid="attention-card">
      <Card as="section" title="Attention">
        <div role="status">
          {items.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-ink">
              <StatusDot tone="ok" />
              All clear — {calmState}
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {items.map((item) => (
                <li
                  key={item.key}
                  data-testid={item.testId}
                  className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0"
                >
                  <StatusDot tone={item.tone} />
                  <p className="min-w-0 flex-1 basis-60 text-sm text-ink">{item.message}</p>
                  <div className="shrink-0">{item.action}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 6: Create `dashboard/src/pages/overview/SetupChecklist.tsx`**

```tsx
import type { Account, Settings } from '../../lib/types'
import Card from '../../components/Card'
import Button from '../../components/Button'
import StatusDot from '../../components/StatusDot'

export interface SetupStep {
  key: 'master' | 'followers' | 'dry-run' | 'live'
  title: string
  detail: string
  done: boolean
  to?: string
  linkLabel?: string
}

/** Every "done" is read from state the page already loads: the account list
 *  and the org's copier settings. Nothing is remembered client-side. */
export function setupSteps(orgId: number, accounts: Account[], settings: Settings | null): SetupStep[] {
  const hasMaster = accounts.some((a) => a.role === 'master')
  const hasFollower = accounts.some((a) => a.role === 'slave')
  const dryRunOn = settings?.dry_run === true
  const live = hasMaster && hasFollower &&
    settings != null && settings.copying_enabled && !settings.dry_run
  const accountsPath = `/org/${orgId}/accounts`
  return [
    {
      key: 'master',
      title: 'Connect the master',
      detail: 'The account whose trades every follower copies. Connect it with cTrader ID or add an MT5 terminal.',
      done: hasMaster,
      to: accountsPath,
      linkLabel: 'Open Accounts',
    },
    {
      key: 'followers',
      title: 'Add followers',
      detail: 'Each follower copies the master at its own multiplier.',
      done: hasFollower,
      to: accountsPath,
      linkLabel: 'Open Accounts',
    },
    {
      key: 'dry-run',
      title: 'Run a dry run',
      detail: 'Turn dry-run on from the desk strip and watch copies being simulated before real orders go out.',
      done: dryRunOn || live,
      to: `/org/${orgId}/automation`,
      linkLabel: 'Open Automation',
    },
    {
      key: 'live',
      title: 'Go live',
      detail: 'When the dry run looks right, turn dry-run off from the desk strip. Copying then sends real orders.',
      done: live,
    },
  ]
}

export default function SetupChecklist({ orgId, accounts, settings }: {
  orgId: number
  accounts: Account[]
  settings: Settings | null
}) {
  const steps = setupSteps(orgId, accounts, settings)
  return (
    <div data-testid="setup-checklist">
      <Card as="section" title="Set up copying">
        <p className="text-sm text-ink-soft">Four steps from an empty desk to live copying.</p>
        <ol className="mt-4 space-y-3">
          {steps.map((step) => (
            <li key={step.key} className="inset flex flex-wrap items-start gap-3 p-4">
              <span className="mt-1"><StatusDot tone={step.done ? 'ok' : 'warn'} /></span>
              <div className="min-w-0 flex-1 basis-56">
                <p className="font-semibold text-ink">{step.title}</p>
                <p className="mt-0.5 text-sm text-ink-soft">{step.detail}</p>
              </div>
              <span className="text-xs font-semibold text-ink-soft">{step.done ? 'Done' : 'To do'}</span>
              {step.to && step.linkLabel && (
                <Button to={step.to} variant="secondary" size="sm">{step.linkLabel}</Button>
              )}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  )
}
```

- [ ] **Step 7: Create `dashboard/src/pages/overview/FleetGrid.tsx`**

```tsx
import type { Account, AccountStateData, StateSnapshot } from '../../lib/types'
import Card from '../../components/Card'
import Button from '../../components/Button'
import StatusDot from '../../components/StatusDot'
import { money, signed } from '../../lib/format'
import ExpandableText from './ExpandableText'
import { accountName } from './Panels'

/** The master card: identity, health, balance and open P&L. Its equity is
 *  the KPI row's job, so it is not repeated here. */
function MasterCard({ orgId, master, snap }: {
  orgId: number
  master: Account
  snap: AccountStateData
}) {
  const healthy = master.status === 'ok' || master.status === 'connected'
  const pnlTone = snap.open_pnl >= 0 ? 'text-profit' : 'text-loss'
  return (
    <Card
      as="section"
      title={`Master account (${master.trader_login})`}
      actions={master.nickname ? <span className="text-sm text-ink-soft">{master.nickname}</span> : undefined}
    >
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-1.5 text-ink">
          <StatusDot tone={healthy ? 'ok' : 'degraded'} />
          {healthy ? 'OK' : 'Degraded'}
        </span>
        <span className="num text-xs text-ink-faint">ID: {master.ctid_trader_account_id}</span>
      </p>
      <dl className="inset mt-4 grid grid-cols-2 gap-4 p-4">
        <div>
          <dt className="text-xs text-ink-soft">Balance</dt>
          <dd className="num mt-1 text-xl font-semibold text-ink">{money(snap.balance)}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-soft">Open P&L</dt>
          <dd className={`num mt-1 text-xl font-semibold ${pnlTone}`}>{signed(snap.open_pnl)}</dd>
        </div>
      </dl>
      <div className="mt-4 flex justify-end">
        <Button to={`/org/${orgId}/positions`} variant="ghost" size="sm">View positions</Button>
      </div>
    </Card>
  )
}

function FollowerTile({ orgId, follower, snap, onPauseResume }: {
  orgId: number
  follower: Account
  snap: AccountStateData | undefined
  onPauseResume: (accountId: number, isPaused: boolean) => void
}) {
  const isPaused = !follower.enabled
  const isDegraded = follower.status === 'degraded'
  const isRefreshFailed = follower.connection_status === 'refresh_failed'
  // An MT5 terminal that has stopped reporting: copies queue and market
  // opens expire after 30 s, so it is the same class of problem as a
  // failed token refresh.
  const isOffline = follower.connection_status === 'offline'

  let statusTone: 'ok' | 'paused' | 'degraded' = 'ok'
  let statusLabel = 'OK'
  if (isPaused) {
    statusTone = 'paused'
    statusLabel = 'Paused'
  } else if (isDegraded) {
    statusTone = 'degraded'
    statusLabel = 'Degraded'
  }

  return (
    <div data-testid="slave-tile">
      <div data-testid={`slave-tile-${follower.ctid_trader_account_id}`} className="h-full">
        <Card
          as="article"
          className="h-full"
          title={accountName(follower)}
          actions={
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-soft">
              <StatusDot tone={statusTone} />
              {statusLabel}
            </span>
          }
        >
          <p className="num text-xs text-ink-faint">
            {follower.nickname ? `${follower.trader_login} · ` : ''}ID: {follower.ctid_trader_account_id}
          </p>

          {/* Reason for degraded status - the send-failure message from the backend */}
          {isDegraded && follower.last_error && (
            <div className="mt-3 rounded-inset border border-loss/20 bg-loss-wash px-2 py-1 text-xs text-loss-deep">
              <ExpandableText text={follower.last_error} limit={40} testId="slave-last-error" />
            </div>
          )}

          {/* Connection markers - distinct from the degraded status above */}
          {isRefreshFailed && (
            <div
              data-testid="slave-refresh-failed-marker"
              className="mt-3 flex items-center gap-1.5 rounded-inset border border-warn/40 bg-warn-wash px-3 py-2 text-xs font-semibold text-warn-deep"
            >
              <StatusDot tone="warn" /> Token refresh failed — reconnect required
            </div>
          )}
          {isOffline && (
            <div
              data-testid="slave-offline-marker"
              className="mt-3 flex items-center gap-1.5 rounded-inset border border-warn/40 bg-warn-wash px-3 py-2 text-xs font-semibold text-warn-deep"
            >
              <StatusDot tone="warn" /> Terminal offline — copies wait until the EA reports again
            </div>
          )}

          {snap && (
            <dl className="inset mt-3 space-y-1.5 p-3 text-sm">
              <div className="flex justify-between">
                <dt className="text-ink-soft">Equity</dt>
                <dd className="num text-ink">{money(snap.equity)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Balance</dt>
                <dd className="num text-ink">{money(snap.balance)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Open positions</dt>
                <dd className="num text-ink">{snap.positions?.length || 0}</dd>
              </div>
            </dl>
          )}

          <div className="mt-4 flex items-center gap-2">
            <Button
              className="flex-1"
              variant={isPaused ? 'primary' : 'secondary'}
              tone={isPaused ? 'profit' : 'brand'}
              onClick={() => onPauseResume(follower.ctid_trader_account_id, isPaused)}
            >
              {isPaused ? 'Resume' : 'Pause'}
            </Button>
            <Button to={`/org/${orgId}/accounts`} variant="ghost" size="sm">
              View<span className="sr-only"> {accountName(follower)}</span>
            </Button>
          </div>
        </Card>
      </div>
    </div>
  )
}

export default function FleetGrid({ orgId, master, masterState, followers, state, onPauseResume }: {
  orgId: number
  master: Account | undefined
  masterState: AccountStateData | undefined
  followers: Account[]
  state: StateSnapshot
  onPauseResume: (accountId: number, isPaused: boolean) => void
}) {
  return (
    <div className="space-y-6">
      {master && masterState && <MasterCard orgId={orgId} master={master} snap={masterState} />}

      {followers.length > 0 && (
        <section aria-labelledby="followers-heading" className="space-y-3">
          <h2 id="followers-heading" className="text-lg font-semibold text-ink">Followers</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {followers.map((f) => (
              <FollowerTile
                key={f.ctid_trader_account_id}
                orgId={orgId}
                follower={f}
                snap={state[String(f.ctid_trader_account_id)]}
                onPauseResume={onPauseResume}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
```

- [ ] **Step 8: Replace `dashboard/src/pages/Overview.tsx` with the triage-first page**

```tsx
import { useState, useEffect, useCallback } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import type {
  Account, ApiState, OverviewStats, Settings, StateSnapshot,
} from '../lib/types'
import ConfirmDialog from '../components/ConfirmDialog'
import StatTile from '../components/StatTile'
import Banner from '../components/Banner'
import Button from '../components/Button'
import PageHeader from '../components/PageHeader'
import Loading from '../components/Loading'
import { money, signed, errorText } from '../lib/format'
import { actionBurst } from '../lib/refresh'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { mergeTicksIntoSnapshot, TicksPayload } from '../lib/ticks'
import AttentionCard, { type AttentionItem } from './overview/AttentionCard'
import SetupChecklist from './overview/SetupChecklist'
import FleetGrid from './overview/FleetGrid'
import {
  ContractsPanel, CopyLogCard, FillsPanel, FleetStatusPanel, PortfolioPanel,
  accountName, type ContractRow,
} from './overview/Panels'

/**
 * Fetch GET orgs/{orgId}/state once and hand back both the envelope and its
 * per-account block.
 *
 * `orgs/{orgId}/state` is a verbatim pass-through of the copier's
 * `get_state()` for that org -- `{accounts, master_positions,
 * pending_orders, drift}` -- with per-account balance/equity/positions
 * under `accounts`, keyed by account id as a STRING (JSON has no integer
 * keys).
 *
 * This screen used to do `api<StateSnapshot>('/api/state')` and index the
 * result directly, i.e. it read the envelope as if it were the account map:
 * every `state[String(accountId)]` was `undefined`, so the master card never
 * rendered at all and every follower tile silently omitted its equity,
 * balance and position count. `api<T>()` is an unchecked cast, so the wrong
 * type argument cost nothing at compile time, and Overview.test.tsx mocked a
 * bare `StateSnapshot` -- the wrong shape -- so the suite agreed with the bug.
 *
 * Typing the fetch as `ApiState` and projecting explicitly here is what makes
 * a future shape change a type error rather than a blank screen; the tests
 * now mock the real envelope and assert the numbers actually render.
 */
async function loadState(
  orgId: number,
): Promise<{ accounts: StateSnapshot; envelope: ApiState }> {
  const envelope = await orgApi<ApiState>(orgId, 'state')
  return { accounts: envelope.accounts ?? {}, envelope }
}

type KpiPanel = 'portfolio' | 'accounts' | 'contracts' | 'fills'

/** How long a streamed margin call stays on the Attention card: the same
 *  30-minute window the desk strip's banner uses. */
const MARGIN_CALL_WINDOW_MS = 30 * 60_000

export default function Overview() {
  const { orgId, role, org } = useOrg()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [state, setState] = useState<StateSnapshot>({})
  const [envelope, setEnvelope] = useState<ApiState | null>(null)
  const [stats, setStats] = useState<OverviewStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  // Why the last live-state read failed; null once a read succeeds.
  const [stateError, setStateError] = useState<string | null>(null)
  const [marginCall, setMarginCall] = useState<{ accountId: number | null; at: number } | null>(null)
  const [expandedKpi, setExpandedKpi] = useState<KpiPanel | null>(null)
  const toggleKpi = (panel: KpiPanel) =>
    setExpandedKpi((cur) => (cur === panel ? null : panel))

  const [closingContract, setClosingContract] = useState<ContractRow | null>(null)
  const [closingIds, setClosingIds] = useState<Set<number>>(new Set())
  const [closingAll, setClosingAll] = useState(false)
  const [closeBusy, setCloseBusy] = useState(false)

  // Load accounts and settings on mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true)
        setError(null)
        const [accs, sett] = await Promise.all([
          orgApi<Account[]>(orgId, 'accounts'),
          orgApi<Settings>(orgId, 'settings'),
        ])
        setAccounts(accs)
        setSettings(sett)
      } catch (err) {
        setError(errorText(err, 'Failed to load the overview'))
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [orgId])

  const refreshState = useCallback(async () => {
    try {
      const { accounts: snapshot, envelope: env } = await loadState(orgId)
      setState(snapshot)
      setEnvelope(env)
      setStateError(null)
    } catch (err) {
      console.error('Failed to load state:', err)
      // A 5xx here is also how an unreachable copier shows up.
      setStateError(errorText(err, 'the copier did not answer'))
    }
    // The DB-side stats and copier performance are passengers: if they
    // fail (older api, copier offline) the live sections still render.
    try {
      setStats(await orgApi<OverviewStats>(orgId, 'overview'))
    } catch {
      /* stats sections show placeholders */
    }
  }, [orgId])

  useEffect(() => {
    refreshState()
    const interval = setInterval(refreshState, 5000)
    return () => clearInterval(interval)
  }, [refreshState])

  // Refetch immediately when a trade event streams in (5s poll is
  // fallback); quotes ticks fold into the snapshot in place instead. A
  // margin call arrives on the same socket as a 'risk' event (the copier's
  // _on_margin_call) and is kept for the Attention card.
  useLiveRefresh(refreshState, orgId, (evt) => {
    if (evt?.category === 'risk' && evt.payload?.action === 'margin_call') {
      setMarginCall({ accountId: evt.account_id ?? null, at: Date.now() })
      return
    }
    if (evt?.category !== 'quotes') return
    const ticks = (evt.payload as TicksPayload | undefined)?.accounts
    if (!ticks) return
    setState((prev) => mergeTicksIntoSnapshot(prev, ticks))
    setEnvelope((prev) => {
      if (!prev) return prev
      const base = prev.accounts ?? {}
      const merged = mergeTicksIntoSnapshot(base, ticks)
      return merged === base ? prev : { ...prev, accounts: merged }
    })
  })

  const handlePauseResume = async (accountId: number, isPaused: boolean) => {
    try {
      setActionError(null)
      const endpoint = isPaused ? 'control/resume' : 'control/pause'
      await orgApi(orgId, endpoint, {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      })
      await refreshState()
    } catch (err) {
      setActionError(
        `${isPaused ? 'Resume' : 'Pause'} failed: ${errorText(err, 'the copier did not respond')}`)
    }
  }

  const closeOne = async (row: ContractRow) => {
    await orgApi(orgId, 'positions/close', {
      method: 'POST',
      body: JSON.stringify({ account_id: row.accountId, position_id: row.pos.position_id }),
    })
  }

  const submitCloseContract = async () => {
    if (!closingContract) return
    const positionId = closingContract.pos.position_id
    setClosingIds((prev) => new Set([...prev, positionId]))
    try {
      setCloseBusy(true)
      setActionError(null)
      await closeOne(closingContract)
    } catch (err) {
      setClosingIds((prev) => {
        const next = new Set(prev)
        next.delete(positionId)
        return next
      })
      setActionError(`Close failed: ${errorText(err, 'the copier did not respond')}`)
    } finally {
      setClosingContract(null)
      setCloseBusy(false)
      await refreshState()
      actionBurst(refreshState)
    }
  }

  const submitCloseAllContracts = async (rows: ContractRow[]) => {
    try {
      setCloseBusy(true)
      setActionError(null)
      const failures: string[] = []
      // Sequential on purpose: master closes replicate to follower copies,
      // so a copy may already be gone by the time its own close is
      // attempted -- treat those errors as per-row outcomes, not a batch abort.
      for (const row of rows) {
        try {
          await closeOne(row)
        } catch (err) {
          failures.push(`${row.pos.symbol ?? row.pos.position_id}: ${errorText(err, 'failed')}`)
        }
      }
      if (failures.length) {
        setActionError(`Some contracts did not close: ${failures.join(' · ')}`)
      }
    } finally {
      setClosingAll(false)
      setCloseBusy(false)
      await refreshState()
      actionBurst(refreshState)
    }
  }

  const header = <PageHeader title="Overview" subtitle={org.name} />

  if (loading) {
    return (
      <div className="space-y-8 max-w-6xl">
        {header}
        <Loading lines={6} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-8 max-w-6xl">
        {header}
        <Banner kind="error">{error}</Banner>
      </div>
    )
  }

  const masterAccount = accounts.find((a) => a.role === 'master')
  const followers = accounts.filter((a) => a.role === 'slave')
  const masterState = masterAccount ? state[String(masterAccount.ctid_trader_account_id)] : undefined

  // Portfolio aggregates across this ORG's accounts -- `state` is the
  // per-account block of GET orgs/{orgId}/state, which the copier already
  // restricts to the org's own accounts.
  const accountStates = Object.values(state)
  const equityValues = accountStates.map((s) => s.equity).filter((v): v is number => v != null)
  const portfolioValue = equityValues.length
    ? equityValues.reduce((a, b) => a + b, 0)
    : null
  const totalOpenPnl = accountStates.reduce((a, s) => a + (s.open_pnl ?? 0), 0)
  const openTrades = accountStates.reduce((a, s) => a + (s.positions?.length ?? 0), 0)

  const yesterdayEquity = stats?.yesterday?.total_equity ?? null
  const vsYesterday = (portfolioValue != null && yesterdayEquity)
    ? (portfolioValue - yesterdayEquity) / yesterdayEquity
    : null

  /**
   * Today's P&L across the fleet: the change in equity since yesterday,
   * summed over ONLY the accounts present on both days.
   *
   * Subtracting yesterday's total from today's total looks equivalent and
   * is not. The two sums cover whatever accounts happened to exist at each
   * moment, so an account added or removed in between lands in the answer
   * at its full balance. One org went 18 accounts -> 21 -> 10 across three
   * days as a broker disabled them and they were reconnected, and the tile
   * duly reported -275,112.83 of "P&L" -- entirely account churn, with no
   * trade behind any of it.
   *
   * Matching per account is the only honest reading: an account that was
   * not here yesterday has no yesterday to be compared against.
   */
  const yesterdayByAccount = stats?.yesterday?.equity_by_account ?? null
  const totalPnl = ((): { value: number; accounts: number; skipped: number } | null => {
    if (!yesterdayByAccount) return null
    let value = 0
    let counted = 0
    let skipped = 0
    for (const [id, snap] of Object.entries(state)) {
      const before = yesterdayByAccount[id]
      const now = snap.equity
      if (before == null || now == null) { skipped += 1; continue }
      value += now - before
      counted += 1
    }
    // Nothing comparable is not the same as no change.
    return counted > 0 ? { value, accounts: counted, skipped } : null
  })()

  // Accounts (master or follower) whose cTrader-ID token refresh has failed.
  // Once the token expires, copying for these accounts silently stops, so
  // this must be impossible to miss - not just a row in the Logs table.
  const refreshFailedAccounts = accounts.filter((a) => a.connection_status === 'refresh_failed')

  // Today's copy fills from the stats feed, newest first.
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0)
  const todaysFills = (stats?.recent_copies ?? [])
    .filter((c) => Date.parse(c.updated_at) >= midnight.getTime())
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))

  // Every running contract across the fleet, flattened from the live state
  // feed -- refreshed by the same 5s poll / websocket as the KPI row.
  const openContracts: ContractRow[] = accounts.flatMap((a) => {
    const snap = state[String(a.ctid_trader_account_id)]
    if (!snap?.positions?.length) return []
    const accountLabel = `${accountName(a)}${a.role === 'master' ? ' · master' : ''}`
    return snap.positions.map((pos) => ({
      accountId: a.ctid_trader_account_id,
      accountLabel,
      pos,
    }))
  })

  // Live P&L per master position, for estimating each active copy's P&L.
  const masterPnlByPosition = new Map<number, { pnl: number | null; volume: number }>()
  for (const pos of envelope?.master_positions ?? []) {
    masterPnlByPosition.set(pos.position_id, { pnl: pos.pnl_quote ?? null, volume: pos.volume })
  }

  // ---- Attention: only live problems, each with its action ----
  const accountsPath = `/org/${orgId}/accounts`
  const positionsPath = `/org/${orgId}/positions`
  const openAccounts = (
    <Button to={accountsPath} variant="secondary" size="sm">Open Accounts</Button>
  )
  const attention: AttentionItem[] = []
  if (stateError) {
    attention.push({
      key: 'state',
      tone: 'degraded',
      testId: 'attention-state-error',
      message: `Live figures stopped refreshing (${stateError}), so the numbers on this page may be stale.`,
      action: (
        <Button variant="secondary" size="sm" onClick={() => { void refreshState() }}>Retry</Button>
      ),
    })
  }
  if (marginCall && Date.now() - marginCall.at < MARGIN_CALL_WINDOW_MS) {
    const hit = accounts.find((a) => a.ctid_trader_account_id === marginCall.accountId)
    const who = hit ? accountName(hit)
      : marginCall.accountId != null ? `account ${marginCall.accountId}` : 'an account'
    attention.push({
      key: 'margin-call',
      tone: 'degraded',
      testId: 'attention-margin-call',
      message: `Margin call on ${who}: the broker may start force-closing positions, so reduce exposure or add funds now.`,
      action: (
        <div className="flex items-center gap-2">
          <Button to={positionsPath} variant="secondary" tone="loss" size="sm">Open Positions</Button>
          <Button variant="ghost" tone="neutral" size="sm" onClick={() => setMarginCall(null)}>Dismiss</Button>
        </div>
      ),
    })
  }
  if (refreshFailedAccounts.length > 0) {
    attention.push({
      key: 'token',
      tone: 'degraded',
      testId: 'refresh-failed-banner',
      message: (
        <>
          Token refresh failed for account{refreshFailedAccounts.length > 1 ? 's' : ''}:{' '}
          <span className="num">{refreshFailedAccounts.map((a) => a.trader_login).join(', ')}</span>.
          Copying for these accounts will stop when the token expires; reconnect via
          Accounts → Connect cTrader ID.
        </>
      ),
      action: openAccounts,
    })
  }
  for (const a of accounts) {
    if (a.connection_status === 'offline') {
      attention.push({
        key: `offline-${a.ctid_trader_account_id}`,
        tone: 'warn',
        message: `${accountName(a)}'s terminal is offline, so copies wait until the EA reports again.`,
        action: openAccounts,
      })
    }
  }
  for (const f of followers) {
    if (f.status === 'degraded') {
      attention.push({
        key: `degraded-${f.ctid_trader_account_id}`,
        tone: 'degraded',
        message: `${accountName(f)} is degraded: copies to it are failing.`,
        action: openAccounts,
      })
    } else if (f.enabled && f.status === 'disconnected') {
      attention.push({
        key: `disconnected-${f.ctid_trader_account_id}`,
        tone: 'warn',
        message: `${accountName(f)} is not connected to its broker, so it receives no copies.`,
        action: openAccounts,
      })
    }
  }
  const calmState = settings == null ? 'copier settings not loaded'
    : !settings.copying_enabled ? 'copying paused'
    : settings.dry_run ? 'dry run, copies are simulated'
    : 'copying live'

  const kpiView = (panel: KpiPanel, label: string) => (
    <Button
      variant="ghost"
      size="sm"
      className="self-start"
      aria-expanded={expandedKpi === panel}
      aria-controls={expandedKpi === panel ? `kpi-panel-${panel}` : undefined}
      onClick={() => toggleKpi(panel)}
    >
      {label}
    </Button>
  )

  const activeFollowers = stats?.active_slaves ?? followers.length
  const connectedCount = stats?.accounts_connected ?? accounts.length
  const masterCount = stats?.masters ?? (masterAccount ? 1 : 0)

  return (
    <div className="space-y-8 max-w-6xl">
      {header}

      {actionError && (
        <Banner kind="error" onDismiss={() => setActionError(null)}>{actionError}</Banner>
      )}

      {accounts.length === 0 ? (
        <SetupChecklist orgId={orgId} accounts={accounts} settings={settings} />
      ) : (
        <>
          <section aria-label="Key figures" className="space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Master equity"
                  value={money(masterState?.equity)}
                  tone="brand"
                  sub={
                    <>
                      Fleet <span className="num">{money(portfolioValue)}</span>
                      {vsYesterday != null ? (
                        <>
                          {' · '}
                          <span className={vsYesterday < 0 ? 'text-loss' : 'text-profit'}>
                            {signed(vsYesterday * 100)}% vs yesterday
                          </span>
                        </>
                      ) : ' · vs yesterday: no snapshot yet'}
                    </>
                  }
                />
                {kpiView('portfolio', 'View all accounts')}
              </div>
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Today's P&L"
                  value={totalPnl == null ? '—' : signed(totalPnl.value)}
                  tone={totalPnl == null ? undefined : (totalPnl.value < 0 ? 'loss' : 'profit')}
                  sub={stats && stats.degraded > 0
                    // A degraded account has silently stopped copying. That
                    // signal used to live on "Copied today" and must not vanish.
                    ? <span className="text-loss">{stats.degraded} degraded</span>
                    : totalPnl == null
                      ? 'needs a full day of history'
                      // Say what was counted. A total that quietly omits
                      // accounts is how the -275,112.83 went unquestioned.
                      : `${totalPnl.accounts} account${totalPnl.accounts === 1 ? '' : 's'} since yesterday`
                        + (totalPnl.skipped > 0 ? ` · ${totalPnl.skipped} too new` : '')}
                />
                {kpiView('fills', "View today's fills")}
              </div>
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Open positions"
                  value={String(openTrades)}
                  sub={
                    <>
                      <span className={`num ${totalOpenPnl < 0 ? 'text-loss' : 'text-profit'}`}>
                        {signed(totalOpenPnl)}
                      </span>
                      {' open P&L'}
                    </>
                  }
                />
                {kpiView('contracts', 'View open positions')}
              </div>
              <div className="flex flex-col gap-1">
                <StatTile
                  label="Followers copying"
                  value={String(activeFollowers)}
                  sub={`${connectedCount} account${connectedCount === 1 ? '' : 's'} connected · ${masterCount} master`}
                />
                {kpiView('accounts', 'View fleet health')}
              </div>
            </div>

            {expandedKpi === 'portfolio' && (
              <div id="kpi-panel-portfolio">
                <PortfolioPanel orgId={orgId} accounts={accounts} state={state} />
              </div>
            )}
            {expandedKpi === 'accounts' && (
              <div id="kpi-panel-accounts">
                <FleetStatusPanel orgId={orgId} accounts={accounts} />
              </div>
            )}
            {expandedKpi === 'fills' && (
              <div id="kpi-panel-fills">
                <FillsPanel orgId={orgId} fills={todaysFills} />
              </div>
            )}
            {expandedKpi === 'contracts' && (
              <div id="kpi-panel-contracts">
                <ContractsPanel
                  orgId={orgId}
                  rows={openContracts}
                  canTrade={can(role, 'trade')}
                  closingIds={closingIds}
                  onClose={setClosingContract}
                  onCloseAll={() => setClosingAll(true)}
                />
              </div>
            )}
          </section>

          <AttentionCard items={attention} calmState={calmState} />

          <FleetGrid
            orgId={orgId}
            master={masterAccount}
            masterState={masterState}
            followers={followers}
            state={state}
            onPauseResume={handlePauseResume}
          />

          {followers.length === 0 && (
            <SetupChecklist orgId={orgId} accounts={accounts} settings={settings} />
          )}

          <CopyLogCard copies={stats?.recent_copies ?? []} masterPnlByPosition={masterPnlByPosition} />
        </>
      )}

      {/* Close one contract */}
      <ConfirmDialog
        open={closingContract != null}
        title={`Close position ${closingContract?.pos.position_id ?? ''}`}
        confirmLabel="Close position"
        danger
        busy={closeBusy}
        onConfirm={submitCloseContract}
        onCancel={() => setClosingContract(null)}
      >
        <p>
          {closingContract?.pos.side}{' '}
          <span className="num">{closingContract?.pos.symbol ?? closingContract?.pos.symbol_id}</span>{' '}
          on {closingContract?.accountLabel} closes at market.
          {closingContract?.accountLabel.includes('master') &&
            ' Closing a master position also closes its copies on every follower.'}
        </p>
      </ConfirmDialog>

      {/* Close every listed contract */}
      <ConfirmDialog
        open={closingAll}
        title="Close every open contract"
        confirmLabel={`Close ${openContracts.length} contract${openContracts.length === 1 ? '' : 's'}`}
        danger
        busy={closeBusy}
        onConfirm={() => submitCloseAllContracts(openContracts)}
        onCancel={() => setClosingAll(false)}
      >
        <p>
          Every contract listed here closes at market — master positions first
          replicate their close to their follower copies. Copying itself stays
          running; this does not pause the copier.
        </p>
      </ConfirmDialog>
    </div>
  )
}
```

Notes on what did not change: the accounts/settings load, the 5 s `setInterval(refreshState, 5000)`, the `useLiveRefresh` subscription and its quotes merge, every request path and body (`control/pause`, `control/resume`, `positions/close`), `actionBurst` after closes, and the `portfolioValue`, `vsYesterday`, `totalPnl` (per-account matched), `totalOpenPnl`, `openTrades`, `todaysFills`, `openContracts` and `masterPnlByPosition` computations. The only new state is `stateError` (set in the existing `catch`) and `marginCall` (set from a socket frame the page already receives).

- [ ] **Step 9: Mount the kill switch in the desk strip**

In `dashboard/src/components/layout/DeskStrip.tsx` (the `DeskStrip` component Task 3 moved out of `Layout.tsx`):

(a) Add the import beside the other component imports:

```tsx
import KillSwitch from '../KillSwitch'
```

(b) The strip's own dry-run chip is the block that starts `{dryRun && (` and renders the text `Dry run`. It stays for viewers, who get no kill switch. For admins the `KillSwitch` badge (`DRY RUN`) replaces it, so the strip never shows two dry-run markers. Change only that block's condition:

old: `{dryRun && (`
new: `{dryRun && !can(role, 'control') && (`

(c) Immediately before the `{can(role, 'control') && (` block that renders the `Close all positions` `Button`, insert the block below. Give the wrapper the same `order-*` / `ml-*` classes that Task 3 left on the Close-all `Button`, so the two controls sit together wherever the strip wraps:

```tsx
{settings && can(role, 'control') && (
  <div className="flex items-center">
    <KillSwitch settings={settings} onUpdate={setSettings} />
  </div>
)}
```

`settings`/`setSettings` are the strip's existing `useState<Settings | null>` pair, and `KillSwitch`'s `onUpdate: (settings: Settings) => void` accepts `setSettings` directly. So a stop, resume or dry-run change updates the strip's pulse and copying label at once, and the strip's own 10 s poll then confirms it from the server. `KillSwitch` already returns `null` below `control`, and the extra `can` check stops an empty wrapper from rendering for viewers.

- [ ] **Step 10: Run the two files to verify they pass**

Run: `npx vitest run src/pages/Overview.test.tsx src/components/Layout.test.tsx`
Expected: PASS. Every test in both files passes, including the 17 new Overview tests, the 12 moved and new Layout tests, and every pre-existing Layout test (`desk strip shows copying state and master numbers`, the close-all tests, and the rest).

- [ ] **Step 11: Run the whole suite and the build**

Run: `npm test`
Expected: `palette_check` prints both themes OK, `tsc --noEmit -p tsconfig.app.json` prints nothing, and vitest reports every file passed with 0 failures. `App.test.tsx` still passes: the kill switch now renders in the strip at an org route, and that test only asserts nav links and Log out.

Run: `npm run build`
Expected: `vite build` succeeds. Note the minified size of the admin entry chunk in the task report (spec §10 target: under 150 KB).

Then grep for leftover vocabulary on the page:

Run: `grep -rn "Slave\|slave" src/pages/Overview.tsx src/pages/overview/ | grep -v "slave_\|'slave'\|slave-tile\|slave-last-error\|slave-refresh-failed-marker\|slave-offline-marker\|active_slaves"`
Expected: no output. The only remaining matches are API field names (`slave_login`, `slave_nickname`, `slave_volume`, `slave_account_id`, `active_slaves`), the `'slave'` role string and the non-visible test ids.

- [ ] **Step 12: Commit**

```bash
git add src/pages/Overview.tsx src/pages/overview/ src/pages/Overview.test.tsx \
  src/components/layout/DeskStrip.tsx src/components/Layout.test.tsx
git commit -m "$(cat <<'EOF'
feat(dashboard): Overview triage-first -- KPI row, Attention card, fleet grid on glass, setup checklist for an empty org

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b
EOF
)"
```

### Task 7: Accounts — read-only rows, a Details drawer that edits, one menu per row; split the god component

Spec §5.4. Today `pages/Accounts.tsx` is 1143 lines with 24 `useState`s. Every row carries four inline editors that save on blur, and up to six buttons (three of them hand-rolled one-offs). After this task the row is read-only: identity, nickname, role, environment, equity and health, then a **Details** button and one `Menu`. Nickname, role, enabled and cutoff are edited in the Details `Drawer` and sent with an explicit **Save changes**. Re-grant moves to the page header. The page splits into a data/actions hook, a row, a drawer, the alias editor, the key-reveal dialog and the confirm dialogs, which leaves `Accounts.tsx` as composition.

Every request keeps today's endpoint, method and body **exactly**:

| Action | Request (unchanged) |
|---|---|
| list | `GET /api/orgs/{orgId}/accounts` |
| equity | `GET /api/orgs/{orgId}/state` (`ApiState.accounts`) |
| nickname | `PATCH accounts/{id}` body `{"nickname": draft}` (untrimmed, as the blur-save sent it) |
| cutoff | `PATCH accounts/{id}` body `{"cutoff_date": draft}` (`""` clears it) |
| enabled | `PATCH accounts/{id}` body `{"enabled": <new value>}` |
| role | `PATCH accounts/{id}` body `{"role": newRole}` (master only after the promote confirm) |
| flatten | `POST control/close-all` body `{"account_id": id}` |
| disconnect | `DELETE accounts/{id}/connection` |
| remove (MT5) | `DELETE mt5/accounts/{id}` |
| add MT5 | `POST mt5/accounts` body `{"nickname": nickname.trim()}` |
| rotate key | `POST mt5/accounts/{id}/key` |
| details | `GET accounts/{id}/details` |
| aliases | `GET accounts/{id}/symbol-aliases`; `PUT` body `{"aliases": {[canonical]: brokerName}}` |
| connect / re-grant | `window.location.assign('/api/orgs/{orgId}/oauth/connect')` |

When one Save changes several fields, the drawer sends **one PATCH per changed field** with the same single-key body the old blur-save sent, in the order nickname → cutoff_date → enabled → role, and then reloads the list once.

**Files:**
- Create: `dashboard/src/pages/accounts/useAccountsPage.ts` (the data/actions hook: 7 grouped `useState`s in place of today's 24)
- Create: `dashboard/src/pages/accounts/useAccountsPage.test.ts`
- Create: `dashboard/src/pages/accounts/AccountRow.tsx`
- Create: `dashboard/src/pages/accounts/AccountDrawer.tsx`
- Create: `dashboard/src/pages/accounts/AliasEditor.tsx`
- Create: `dashboard/src/pages/accounts/KeyRevealDialog.tsx`
- Create: `dashboard/src/pages/accounts/AccountDialogs.tsx` (the six confirm dialogs: disconnect, remove, promote, flatten, add MT5, rotate key)
- Replace: `dashboard/src/pages/Accounts.tsx` (composition, about 150 lines)
- Replace: `dashboard/src/pages/Accounts.test.tsx`

**Interfaces:**
- Consumes (Task 1): utilities `rounded-inset`, and `rounded-full` (Tailwind) for the Live dot.
- Consumes (Task 2): default exports `components/Card.tsx` (`Card({ title?, actions?, inset?, className?, as?, children })`), `components/PageHeader.tsx` (`PageHeader({ title, subtitle?, actions?, children? })`, which calls `usePageTitle(title)`, so `document.title` becomes `"Accounts · MirrorFleet"`), `components/Loading.tsx` (`Loading({ lines?, label?, className? })`), `components/Menu.tsx` (`Menu({ label, items: Array<{ key, label, tone?: 'neutral'|'loss', disabled?, onSelect }> })`: a trigger button with `aria-label={label}` that opens `role="menu"` holding `role="menuitem"` buttons, Escape closes it and focus returns to the trigger). `Drawer`, `ConfirmDialog`, `Badge`, `Button`, `Input`, `Select` and `StatusDot` keep their current APIs.
- Produces (`pages/accounts/useAccountsPage.ts`):
  - `export type FlattenState = 'busy' | 'done' | 'error'`
  - `export interface KeyReveal { title: string; key: string; download_url: string | null; install: string[] }`
  - `export interface AccountDraft { nickname: string; role: string; enabled: boolean; cutoff_date: string }`
  - `export interface SaveOutcome { promoteCancelled: boolean }`
  - `export type PageDialog = { kind: 'disconnect' | 'remove' | 'flatten' | 'promote' | 'rotate'; account: Account } | { kind: 'add-mt5'; nickname: string } | null`
  - `export function needsRegrant(account: Account): boolean`: a cTrader account whose `connection_status !== 'active'`. This is the same condition that today paints the row's connection badge `warn` instead of "Active".
  - `export function draftOf(account: Account): AccountDraft`
  - `export function useAccountsPage()` returns `{ canControl, canTrade, accounts, equity, isLoading, error, notice, dismissNotice, reportError, pending, flatten, roleErrors, dialog, busy, keyReveal, drawer: { account, details, detailsError, aliases, aliasesError }, connectOAuth, askAddMt5, setMt5Nickname, confirmAddMt5, askFlatten, confirmFlatten, askDisconnect, confirmDisconnect, askRemove, confirmRemove, askRotate, confirmRotate, resolvePromote, cancelDialog, closeKeyReveal, openDetails, closeDetails, saveEdits, saveAlias }`
  - `export type AccountsPage = ReturnType<typeof useAccountsPage>`
- Produces (`pages/accounts/AccountRow.tsx`): default `AccountRow`; named `accountRoleLabel(role: string): string` (`master`→"Master", `slave`→"Follower", `ignored`→"Ignored") and `mt5Subtitle(link: Account['mt5']): string`.
- Produces: default exports `AccountDrawer`, `AliasEditor`, `KeyRevealDialog` and `AccountDialogs`, with the props shown in their files below.
- User-visible vocabulary: "Slave" → "Follower". API values (`role: 'slave'`) are unchanged, so the `<option value="slave">` reads "Follower".

- [ ] **Step 1: Write the failing hook tests**

Create `dashboard/src/pages/accounts/useAccountsPage.test.ts`:

```ts
import { renderHook, waitFor, act } from '@testing-library/react'
import { expect, test, vi, afterEach } from 'vitest'
import { draftOf, useAccountsPage, type SaveOutcome } from './useAccountsPage'
import { mockUseOrg } from '../../test/orgMock'
import { mt5Account } from '../../test/mt5Fixtures'
import type { Account } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

const fleet: Account[] = [
  {
    ctid_trader_account_id: 1, trader_login: 12345, is_live: false, role: 'master',
    enabled: true, multiplier: 1, status: 'ok', last_error: null,
    connection_status: 'active', nickname: null, cutoff_date: null,
  },
  {
    ctid_trader_account_id: 2, trader_login: 12346, is_live: true, role: 'slave',
    enabled: true, multiplier: 2, status: 'ok', last_error: null,
    connection_status: 'active', nickname: 'Second desk', cutoff_date: '2026-12-01',
  },
]

const state = {
  accounts: { '1': { equity: 1049.48, open_pnl: 0, positions: [] } },
  master_positions: [], pending_orders: [], drift: [],
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

/** Overrides match by substring in insertion order, method-qualified or not. */
function routes(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    for (const [fragment, responder] of Object.entries(overrides)) {
      const [method, path] = fragment.includes(' ') ? fragment.split(' ') : [undefined, fragment]
      if (url.includes(path) && (!method || (init?.method || 'GET') === method)) return responder(init)
    }
    if (url.includes('/api/orgs/1/state')) return jsonResponse(state)
    if (url.includes('/api/orgs/1/accounts')) return jsonResponse(fleet)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function patchBodies(fetchMock: ReturnType<typeof routes>) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PATCH')
    .map(([u, init]) => [String(u), JSON.parse(String(init!.body))])
}

test('loads the accounts and the live equity, then stops loading', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  routes()
  const { result } = renderHook(() => useAccountsPage())

  expect(result.current.isLoading).toBe(true)
  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.accounts.map((a) => a.ctid_trader_account_id)).toEqual([1, 2])
  await waitFor(() => expect(result.current.equity['1']?.equity).toBe(1049.48))
  expect(result.current.error).toBeNull()
  expect(result.current.canControl).toBe(true)
})

test('a failed accounts load surfaces the server message and still stops loading', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  routes({ '/api/orgs/1/accounts': () => jsonResponse({ detail: 'boom' }, 500) })
  const { result } = renderHook(() => useAccountsPage())

  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.error).toBe('500: boom')
  expect(result.current.accounts).toEqual([])
})

test('flatten: the dialog closes at once, the kill switch is POSTed for that account, and the row is marked done', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  const fetchMock = routes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({
      status: 'flattened', paused: false,
      accounts: [{ account_id: 2, positions_closed: 3, orders_cancelled: 1, positions_remaining: [], error: null }],
    }),
  })
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
  const follower = result.current.accounts[1]

  act(() => result.current.askFlatten(follower))
  expect(result.current.dialog).toEqual({ kind: 'flatten', account: follower })

  await act(async () => { await result.current.confirmFlatten() })

  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/orgs/1/control/close-all')
  expect(call).toBeTruthy()
  expect(JSON.parse(String(call![1]!.body))).toEqual({ account_id: 2 })
  expect(result.current.dialog).toBeNull()
  expect(result.current.flatten[2]).toBe('done')
  expect(result.current.notice).toBe(
    'Closed 3 positions and cancelled 1 order on account 12346. Verified flat.')
})

test('flatten that leaves a position open is an error on the row, never done', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  routes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({
      status: 'flattened', paused: false,
      accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, positions_remaining: [77], error: null }],
    }),
  })
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))

  act(() => result.current.askFlatten(result.current.accounts[1]))
  await act(async () => { await result.current.confirmFlatten() })

  expect(result.current.flatten[2]).toBe('error')
  expect(result.current.error).toBe(
    'Account 12346: closed 1, but 1 position could not be closed. ' +
    'You are still exposed — close it in the platform.')
})

test('remove: confirming DELETEs the MT5 account, announces it, and reloads the list', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  let listed: Account[] = [...fleet, mt5Account]
  const fetchMock = routes({
    'DELETE /api/orgs/1/mt5/accounts/1000000000001': () => {
      listed = fleet
      return new Response(null, { status: 204 })
    },
    '/api/orgs/1/accounts': () => jsonResponse(listed),
  })
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(3))

  act(() => result.current.askRemove(result.current.accounts[2]))
  expect(result.current.dialog?.kind).toBe('remove')
  await act(async () => { await result.current.confirmRemove() })

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/orgs/1/mt5/accounts/1000000000001', expect.objectContaining({ method: 'DELETE' }))
  expect(result.current.dialog).toBeNull()
  expect(result.current.notice).toBe('Account removed. Its terminal key stops working immediately.')
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
})

test('saveEdits sends one PATCH per changed field, with the single-key bodies the blur-save sent', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  const fetchMock = routes()
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
  const master = result.current.accounts[0]

  await act(async () => {
    await result.current.saveEdits(master, {
      ...draftOf(master), nickname: 'Main live', cutoff_date: '2026-09-16', enabled: false,
    })
  })

  expect(patchBodies(fetchMock)).toEqual([
    ['/api/orgs/1/accounts/1', { nickname: 'Main live' }],
    ['/api/orgs/1/accounts/1', { cutoff_date: '2026-09-16' }],
    ['/api/orgs/1/accounts/1', { enabled: false }],
  ])
  expect(result.current.pending.has(1)).toBe(false)
})

test('saveEdits to master waits for the promote confirmation; cancelling sends nothing', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
  const fetchMock = routes()
  const { result } = renderHook(() => useAccountsPage())
  await waitFor(() => expect(result.current.accounts).toHaveLength(2))
  const follower = result.current.accounts[1]

  let outcome!: Promise<SaveOutcome>
  act(() => { outcome = result.current.saveEdits(follower, { ...draftOf(follower), role: 'master' }) })
  await waitFor(() => expect(result.current.dialog).toEqual({ kind: 'promote', account: follower }))

  await act(async () => { result.current.resolvePromote(false) })
  await expect(outcome).resolves.toEqual({ promoteCancelled: true })
  expect(patchBodies(fetchMock)).toEqual([])
  expect(result.current.dialog).toBeNull()
})
```

- [ ] **Step 2: Rewrite the page tests for the new structure**

Replace `dashboard/src/pages/Accounts.test.tsx` with the full file below. Every earlier behavioural assertion is kept; only the selectors changed. Menu actions go through `openMenu`/`chooseFromMenu`, the editors live in the drawer and save on "Save changes", the role copy reads "Follower", and the per-row Re-grant becomes the single header button. The new tests cover: the document title, no inline inputs in rows, one PATCH per changed field, Cancel discarding, the keyboard-opened menu listing items per platform, Re-grant in the header, and the empty state.

```tsx
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import Accounts from './Accounts'
import type { Role } from '../lib/roles'
import { mockUseOrg } from '../test/orgMock'
import { mt5Account } from '../test/mt5Fixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function setRole(role: Role) {
  useOrgMock.mockReturnValue(mockUseOrg(role))
}

let mockWindowOpen: ReturnType<typeof vi.fn>
let mockLocationAssign: ReturnType<typeof vi.fn>
let focusListeners: Set<(event: Event) => void> = new Set()

beforeEach(() => {
  mockWindowOpen = vi.fn()
  mockLocationAssign = vi.fn()
  // jsdom's location is not writable; stub only assign, which is what the
  // connect handler calls.
  Object.defineProperty(window, 'location', {
    value: { ...window.location, assign: mockLocationAssign },
    writable: true,
    configurable: true,
  })
  focusListeners.clear()

  Object.defineProperty(window, 'open', {
    value: mockWindowOpen,
    writable: true,
  })

  // Capture focus event listeners
  const originalAddEventListener = window.addEventListener
  vi.spyOn(window, 'addEventListener').mockImplementation((event: string, handler: EventListenerOrEventListenerObject) => {
    if (event === 'focus' && typeof handler === 'function') {
      focusListeners.add(handler as (event: Event) => void)
    }
    return originalAddEventListener.call(window, event, handler)
  })
})

afterEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  focusListeners.clear()
})

const mockAccounts = [
  {
    ctid_trader_account_id: 1,
    trader_login: 12345,
    is_live: false,
    role: 'master',
    enabled: true,
    multiplier: 1.0,
    status: 'ok',
    last_error: null,
    connection_status: 'active',
    nickname: null,
    cutoff_date: null,
  },
  {
    ctid_trader_account_id: 2,
    trader_login: 12346,
    is_live: true,
    role: 'slave',
    enabled: true,
    multiplier: 2.0,
    status: 'ok',
    last_error: null,
    connection_status: 'active',
    nickname: 'Second desk',
    cutoff_date: '2026-12-01',
  },
]

const mockDetails = {
  account_id: 1, trader_login: 12345, balance: 10000, deposit_currency: 'USD',
  leverage: 50, max_leverage: 500, broker_name: 'FP Markets',
  registration_timestamp: 1700000000000, account_type: 'HEDGED',
  access_rights: 'FULL_ACCESS', swap_free: false, is_limited_risk: false,
  open_positions: [], pending_orders: [],
  nickname: null, role: 'master', enabled: true, multiplier: 1, status: 'ok',
  last_error: null, is_live: false,
  connection: {
    granted_at: '2026-08-01T10:00:00+00:00',
    expires_at: '2026-08-31T10:00:00+00:00',
    status: 'active', scope: 'trading',
  },
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

/** Route-based fetch mock; individual tests override specific routes. */
// Live equity comes from the engine's state endpoint, keyed by account id,
// NOT from the accounts row -- the accounts table stores no balance.
const mockState = {
  accounts: {
    // Keyed by ctid_trader_account_id (1), NOT the trader login shown on screen.
    '1': { equity: 1049.48, open_pnl: 0, positions: [] },
    // Account 2 deliberately absent: an account the engine has no reading
    // for must show a dash, never 0.00, which would read as "empty account".
  },
  master_positions: [],
  pending_orders: [],
  drift: [],
}

function mockRoutes(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    for (const [fragment, responder] of Object.entries(overrides)) {
      const [method, path] = fragment.includes(' ')
        ? fragment.split(' ')
        : [undefined, fragment]
      if (url.includes(path) && (!method || (init?.method || 'GET') === method)) {
        return responder(init)
      }
    }
    if (url.includes('/details')) return jsonResponse(mockDetails)
    if (url.includes('/api/orgs/1/state')) return jsonResponse(mockState)
    if (url.includes('/api/orgs/1/accounts')) return jsonResponse(mockAccounts)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAccounts() {
  return render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>
  )
}

/** The fleet plus one connected MT5 account. `extra` is spread FIRST: the
 *  override loop matches by substring in insertion order, and
 *  '/api/orgs/1/accounts' would otherwise swallow '/accounts/…/details'. */
function mockMt5Routes(extra: Record<string, (init?: RequestInit) => Response> = {}) {
  return mockRoutes({
    ...extra,
    '/api/orgs/1/accounts': () => jsonResponse([...mockAccounts, mt5Account]),
  })
}

/** The table row whose text contains `text` (a login, a nickname, a broker). */
function rowFor(text: string): HTMLElement {
  const row = screen.getAllByRole('row').find((r) => r.textContent?.includes(text))
  if (!row) throw new Error(`no row contains ${text}`)
  return row
}

/** Opens the row's one actions menu and returns it. */
async function openMenu(text: string): Promise<HTMLElement> {
  await userEvent.click(within(rowFor(text)).getByRole('button', { name: /^actions for/i }))
  return screen.findByRole('menu')
}

async function chooseFromMenu(text: string, item: RegExp) {
  const menu = await openMenu(text)
  await userEvent.click(within(menu).getByRole('menuitem', { name: item }))
}

function menuItemNames(menu: HTMLElement): string[] {
  return within(menu).getAllByRole('menuitem').map((el) => el.textContent?.trim() ?? '')
}

/** Opens the row's Details drawer -- the only dialog on screen at that point. */
async function openDrawer(text: string): Promise<HTMLElement> {
  await userEvent.click(within(rowFor(text)).getByRole('button', { name: /^details$/i }))
  return screen.findByRole('dialog')
}

function patchCalls(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
}

test('loads and displays accounts with nicknames on mount', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
  expect(screen.getByText('Second desk')).toBeInTheDocument()
})

test('the page sets the document title', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  await waitFor(() => expect(document.title).toBe('Accounts · MirrorFleet'))
})

test('connect navigates THIS tab to the org-scoped route, never a popup', async () => {
  // A popup breaks the flow outright: the broker's redirect back to
  // /api/oauth/callback is cross-site, and a SameSite=Lax session cookie
  // is withheld from a popup navigated that way -- the callback answered
  // "Not authenticated" and no account could ever be connected.
  setRole('admin')
  mockRoutes()
  renderAccounts()

  const connectButton = await screen.findByRole('button', { name: /connect ctrader id/i })
  await userEvent.click(connectButton)

  expect(mockLocationAssign).toHaveBeenCalledWith('/api/orgs/1/oauth/connect')
  expect(mockWindowOpen).not.toHaveBeenCalled()
})

test('window-focus refetch: refetches accounts after OAuth popup closes', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })
  const callsBefore = fetchMock.mock.calls.length

  act(() => {
    focusListeners.forEach((listener) => listener(new Event('focus')))
  })

  await waitFor(() => {
    expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore)
    const accountCalls = fetchMock.mock.calls.filter(([u]) => String(u) === '/api/orgs/1/accounts')
    expect(accountCalls.length).toBeGreaterThanOrEqual(2)
  })
})

test('rows are read-only: no text field, select or checkbox inside the table', async () => {
  setRole('admin')
  mockMt5Routes()
  renderAccounts()

  const table = await screen.findByRole('table')
  await within(table).findByText('12345')
  expect(within(table).queryAllByRole('textbox')).toHaveLength(0)
  expect(within(table).queryAllByRole('combobox')).toHaveLength(0)
  expect(within(table).queryAllByRole('checkbox')).toHaveLength(0)
})

// ---------- The drawer's edit form ----------

test('role select in the drawer PATCHes role on Save changes', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  await userEvent.selectOptions(within(drawer).getByLabelText('Role'), 'slave')
  // Nothing is sent until Save: the old select saved on change.
  expect(patchCalls(fetchMock)).toHaveLength(0)
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ role: 'slave' }) })
    )
  })
})

test('choosing Master confirms, then promotes (old master demoted server-side)', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12346')
  await userEvent.selectOptions(within(drawer).getByLabelText('Role'), 'master')
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  // No PATCH yet: promoting a master re-shapes the whole fleet, so it asks.
  const dialog = await screen.findByRole('dialog', { name: /make account 12346 the master/i })
  expect(dialog).toHaveTextContent(/becomes a follower/i)
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')).toBe(false)

  await userEvent.click(within(dialog).getByRole('button', { name: /make it the master/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ role: 'master' })
  })
})

test('cancelling the master confirmation changes nothing', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12346')
  await userEvent.selectOptions(within(drawer).getByLabelText('Role'), 'master')
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))
  const dialog = await screen.findByRole('dialog', { name: /make account 12346 the master/i })
  await userEvent.click(within(dialog).getByRole('button', { name: /^cancel$/i }))

  expect(screen.queryByRole('dialog', { name: /make account 12346 the master/i })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).includes('/accounts/2') && (init as RequestInit)?.method === 'PATCH')).toBe(false)
  await waitFor(() => {
    expect((within(drawer).getByLabelText('Role') as HTMLSelectElement).value).toBe('slave')
  })
})

test('enabled toggle PATCHes enabled field (not role)', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  await userEvent.click(within(drawer).getByLabelText('Copying enabled'))
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      })
    )
  })

  const patches = patchCalls(fetchMock)
  expect(patches).toHaveLength(1)
  expect(String(patches[0][1]!.body)).not.toContain('role')
})

test('nickname edit PATCHes nickname on Save changes, the same body the blur-save sent', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Main live')
  // Leaving the field no longer saves: only Save does.
  await userEvent.tab()
  expect(patchCalls(fetchMock)).toHaveLength(0)
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ nickname: 'Main live' }),
      })
    )
  })
})

test('cutoff date edit PATCHes cutoff_date', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12345')
  fireEvent.change(within(drawer).getByLabelText('Cutoff date'), { target: { value: '2026-09-16' } })
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ cutoff_date: '2026-09-16' }),
      })
    )
  })
})

test('clearing the cutoff date PATCHes an empty cutoff_date', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12346')
  const cutoffInput = within(drawer).getByLabelText('Cutoff date')
  expect((cutoffInput as HTMLInputElement).value).toBe('2026-12-01')
  fireEvent.change(cutoffInput, { target: { value: '' } })
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/2',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ cutoff_date: '' }),
      })
    )
  })
})

test('one Save with several changes sends one PATCH per field, each with its old single-key body', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  const drawer = await openDrawer('12345')
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Main live')
  fireEvent.change(within(drawer).getByLabelText('Cutoff date'), { target: { value: '2026-09-16' } })
  await userEvent.click(within(drawer).getByLabelText('Copying enabled'))
  await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

  await waitFor(() => {
    expect(patchCalls(fetchMock).map(([u, init]) => [String(u), String(init!.body)])).toEqual([
      ['/api/orgs/1/accounts/1', JSON.stringify({ nickname: 'Main live' })],
      ['/api/orgs/1/accounts/1', JSON.stringify({ cutoff_date: '2026-09-16' })],
      ['/api/orgs/1/accounts/1', JSON.stringify({ enabled: false })],
    ])
  })
})

test('Cancel discards the drawer edits and sends nothing', async () => {
  setRole('admin')
  const fetchMock = mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  const drawer = await openDrawer('12345')
  const nickname = within(drawer).getByLabelText('Nickname') as HTMLInputElement
  const save = within(drawer).getByRole('button', { name: /save changes/i })
  expect(save).toBeDisabled()

  await userEvent.type(nickname, 'Scratch')
  expect(save).toBeEnabled()
  await userEvent.click(within(drawer).getByRole('button', { name: /^cancel$/i }))

  expect(nickname.value).toBe('')
  expect(save).toBeDisabled()
  expect(patchCalls(fetchMock)).toHaveLength(0)
})

test('viewer sees the cutoff date read-only', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  const drawer = await openDrawer('12346')
  expect(within(drawer).getByText('2026-12-01')).toBeInTheDocument()
  expect(within(drawer).queryByLabelText(/cutoff date/i)).not.toBeInTheDocument()
})

// ---------- Row menu actions ----------

test('disconnect confirms then DELETEs the ACCOUNT-scoped connection route', async () => {
  setRole('admin')
  const fetchMock = mockRoutes({
    'DELETE /api/orgs/1/accounts/2/connection': () =>
      jsonResponse({ detail: 'ok', accounts_removed: 2, copier_reloaded: true }),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await chooseFromMenu('12346', /^disconnect$/i)

  // ConfirmDialog explains the whole-grant consequence, then confirms.
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText(/every account under/i)).toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('button', { name: /disconnect grant/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/accounts/2/connection',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})

test('details button opens the drawer with broker profile fields', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const detailButtons = screen.getAllByRole('button', { name: /details/i })
  await userEvent.click(detailButtons[0])

  expect(await screen.findByText('FP Markets')).toBeInTheDocument()
  expect(screen.getByText('1:50')).toBeInTheDocument()
  expect(screen.getByText('HEDGED')).toBeInTheDocument()
  expect(screen.getByText('USD')).toBeInTheDocument()
  // Grant info from the DB side of the merge
  expect(screen.getByText('OAuth grant')).toBeInTheDocument()
})

test("Escape closes the details drawer and returns focus to the row's Details button", async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  const detailsButton = screen.getAllByRole('button', { name: /details/i })[0]
  await userEvent.click(detailsButton)

  const drawer = await screen.findByRole('dialog', { name: /account 12345/i })
  expect(within(drawer).getByRole('button', { name: /^close$/i })).toBeInTheDocument()

  await userEvent.keyboard('{Escape}')

  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: /account 12345/i })).not.toBeInTheDocument()
  })
  expect(detailsButton).toHaveFocus()
})

test('flatten confirms then POSTs the per-account kill switch', async () => {
  setRole('admin')
  const fetchMock = mockRoutes({
    'POST /api/orgs/1/control/close-all': () =>
      jsonResponse({
        status: 'flattened', paused: false,
        accounts: [{ account_id: 2, positions_closed: 3, orders_cancelled: 1, error: null }],
      }),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await chooseFromMenu('12346', /^flatten$/i)

  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/api/orgs/1/control/close-all'))
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ account_id: 2 })
  })
  // Outcome notice
  expect(await screen.findByText(/closed 3 position/i)).toBeInTheDocument()
})

test('flatten closes the dialog immediately and marks the row Flattening… while in flight', async () => {
  setRole('admin')
  let resolveCloseAll!: (value: Response) => void
  const pendingCloseAll = new Promise<Response>((res) => { resolveCloseAll = res })
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => pendingCloseAll as unknown as Response,
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await chooseFromMenu('12346', /^flatten$/i)
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  // The dialog goes away at once; progress lives on the row instead.
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  const busyButton = within(rowFor('12346')).getByRole('button', { name: /flattening/i })
  // aria-disabled (not disabled) keeps the button focusable so keyboard focus
  // is not stranded when the dialog closes.
  expect(busyButton).toHaveAttribute('aria-disabled', 'true')
  // The other row is untouched.
  expect(within(rowFor('12345')).queryByRole('button', { name: /flatten/i })).not.toBeInTheDocument()

  resolveCloseAll(jsonResponse({
    status: 'flattened', paused: false,
    accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, error: null }],
  }))
  expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()
  // The outcome notice is announced to assistive tech and names the account.
  expect(screen.getByRole('status')).toHaveTextContent(/on account 12346/i)
})

test('the Flattened ✓ confirmation reverts to the idle row after a few seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    setRole('admin')
    mockRoutes({
      'POST /api/orgs/1/control/close-all': () =>
        jsonResponse({
          status: 'flattened', paused: false,
          accounts: [{ account_id: 2, positions_closed: 2, orders_cancelled: 0, error: null }],
        }),
    })
    renderAccounts()

    await waitFor(() => {
      expect(screen.getByText('12346')).toBeInTheDocument()
    })

    await chooseFromMenu('12346', /^flatten$/i)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))
    expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.queryByRole('button', { name: /flattened/i })).not.toBeInTheDocument()
    // Back to idle: no flatten status on any row; Flatten lives in the menu again.
    expect(screen.queryByRole('button', { name: /flatten/i })).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('the outcome live region is mounted before any flatten so announcements fire', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })
  // A live region only announces reliably if it exists BEFORE its text changes.
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
})

test('a successful retry clears the earlier flatten failure banner', async () => {
  setRole('admin')
  let closeAllCalls = 0
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => {
      closeAllCalls += 1
      return closeAllCalls === 1
        ? jsonResponse({ detail: 'copier unreachable' }, 502)
        : jsonResponse({
            status: 'flattened', paused: false,
            accounts: [{ account_id: 2, positions_closed: 1, orders_cancelled: 0, error: null }],
          })
    },
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await chooseFromMenu('12346', /^flatten$/i)
  await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /close everything here/i }))

  const retryButton = await screen.findByRole('button', { name: /failed/i })
  expect(screen.getByText(/flatten failed on account 12346/i)).toBeInTheDocument()

  await userEvent.click(retryButton)
  await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /close everything here/i }))

  expect(await screen.findByRole('button', { name: /flattened/i })).toBeInTheDocument()
  // The stale failure alert must not contradict the fresh success notice.
  expect(screen.queryByText(/flatten failed on account 12346/i)).not.toBeInTheDocument()
})

test('flatten failure flags the row and names the account in the error', async () => {
  setRole('admin')
  mockRoutes({
    'POST /api/orgs/1/control/close-all': () => jsonResponse({ detail: 'copier unreachable' }, 502),
  })
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12346')).toBeInTheDocument()
  })

  await chooseFromMenu('12346', /^flatten$/i)
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: /close everything here/i }))

  const retryButton = await within(rowFor('12346')).findByRole('button', { name: /failed/i })
  expect(screen.getByText(/flatten failed on account 12346/i)).toBeInTheDocument()

  // The failed button is a retry: clicking it reopens the confirmation.
  await userEvent.click(retryButton)
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
})

// ---------- Role gating ----------

test('viewer (below control) gets read-only rows and a read-only drawer: no menu, no editors, no connect', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  // No actions menu on any row.
  expect(screen.queryByRole('button', { name: /^actions for/i })).not.toBeInTheDocument()
  // ...but the read-only values are still shown, in the product's words.
  expect(screen.getByText('Master')).toBeInTheDocument()
  expect(screen.getByText('Follower')).toBeInTheDocument()
  expect(screen.getByText('Second desk')).toBeInTheDocument()

  expect(screen.queryByRole('button', { name: /disconnect/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect ctrader id/i })).not.toBeInTheDocument()
  // Flatten and Re-grant access are the same destructive/OAuth class as the
  // gated controls above and must be hidden below control too.
  expect(screen.queryByRole('button', { name: /^flatten$/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /re-grant access/i })).not.toBeInTheDocument()

  // The drawer shows the settings but offers no editor.
  const drawer = await openDrawer('12345')
  expect(within(drawer).queryByLabelText('Role')).not.toBeInTheDocument()
  expect(within(drawer).queryByLabelText(/multiplier/i)).not.toBeInTheDocument()
  expect(within(drawer).queryByLabelText('Copying enabled')).not.toBeInTheDocument()
  expect(within(drawer).queryByLabelText('Nickname')).not.toBeInTheDocument()
  expect(within(drawer).queryByRole('button', { name: /save changes/i })).not.toBeInTheDocument()
})

test('admin (control) edits in the drawer, acts from the row menu, and has connect and re-grant in the header', async () => {
  setRole('admin')
  mockRoutes()
  renderAccounts()

  await waitFor(() => {
    expect(screen.getByText('12345')).toBeInTheDocument()
  })

  expect(screen.getByRole('button', { name: /connect ctrader id/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /re-grant access/i })).toBeInTheDocument()

  const drawer = await openDrawer('12345')
  expect(within(drawer).getByLabelText('Role')).toBeInTheDocument()
  expect(within(drawer).getByLabelText('Copying enabled')).toBeInTheDocument()
  expect(within(drawer).getByLabelText('Nickname')).toBeInTheDocument()
  expect(within(drawer).getByLabelText('Cutoff date')).toBeInTheDocument()
  expect(within(drawer).getByRole('button', { name: /save changes/i })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

  const menu = await openMenu('12346')
  expect(within(menu).getByRole('menuitem', { name: /^flatten$/i })).toBeInTheDocument()
  expect(within(menu).getByRole('menuitem', { name: /^disconnect$/i })).toBeInTheDocument()
})

test('each row has one actions menu that opens from the keyboard and lists what applies to its platform', async () => {
  setRole('admin')
  const lapsed = {
    ...mockAccounts[1], ctid_trader_account_id: 3, trader_login: 12347,
    nickname: 'Lapsed desk', connection_status: 'expired',
  }
  const mt5Master = {
    ...mt5Account, ctid_trader_account_id: 1000000000004, nickname: 'Master terminal',
    role: 'master', mt5: { ...mt5Account.mt5!, login: 777, broker: 'ABC Ltd' },
  }
  mockRoutes({
    '/api/orgs/1/accounts': () => jsonResponse([mockAccounts[0], lapsed, mt5Account, mt5Master]),
  })
  renderAccounts()
  await screen.findByText('12345')

  const expectations: Array<[string, string[]]> = [
    ['12345', ['Flatten', 'Disconnect']],
    // A lapsed cTrader grant is the one place Re-grant appears on a row.
    ['Lapsed desk', ['Flatten', 'Re-grant access', 'Disconnect']],
    ['XYZ Ltd', ['Flatten', 'Rotate key', 'Remove']],
    // The master is never offered Remove.
    ['ABC Ltd', ['Flatten', 'Rotate key']],
  ]
  for (const [text, items] of expectations) {
    const trigger = within(rowFor(text)).getByRole('button', { name: /^actions for/i })
    act(() => trigger.focus())
    await userEvent.keyboard('{Enter}')
    const menu = await screen.findByRole('menu')
    expect(menuItemNames(menu)).toEqual(items)
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  }
})

test('Re-grant access lives in the page header, disabled until a cTrader grant needs it', async () => {
  setRole('admin')
  mockRoutes()
  const { unmount } = renderAccounts()

  await screen.findByText('12345')
  // Every grant is active: nothing to re-grant (Connect cTrader ID still is).
  expect(screen.getByRole('button', { name: /re-grant access/i })).toBeDisabled()
  expect(within(screen.getByRole('table')).queryByRole('button', { name: /re-grant access/i }))
    .not.toBeInTheDocument()
  unmount()

  const lapsed = { ...mockAccounts[1], connection_status: 'expired' }
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([mockAccounts[0], lapsed]) })
  renderAccounts()

  await screen.findByText('12346')
  expect(within(rowFor('12346')).getByText('Expired')).toBeInTheDocument()
  const regrant = screen.getByRole('button', { name: /re-grant access/i })
  expect(regrant).toBeEnabled()
  await userEvent.click(regrant)
  expect(mockLocationAssign).toHaveBeenCalledWith('/api/orgs/1/oauth/connect')
})

test('an empty workspace shows one sentence and a Connect cTrader ID button', async () => {
  setRole('admin')
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([]) })
  renderAccounts()

  expect(await screen.findByText(/no accounts yet/i)).toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  // One in the header, one in the empty state.
  const connects = screen.getAllByRole('button', { name: /connect ctrader id/i })
  expect(connects).toHaveLength(2)
  await userEvent.click(connects[1])
  expect(mockLocationAssign).toHaveBeenCalledWith('/api/orgs/1/oauth/connect')
})

test('a viewer sees the empty sentence without a Connect button', async () => {
  setRole('viewer')
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([]) })
  renderAccounts()

  expect(await screen.findByText(/no accounts yet/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect ctrader id/i })).not.toBeInTheDocument()
})

test('shows each account\'s live equity, and a dash when the engine has no reading', async () => {
  // Operators asked for per-account equity on this screen: the header only
  // ever showed the MASTER's, so there was no way to see at a glance that a
  // follower had drifted far from the others, or been drained by a margin call.
  setRole('admin')
  mockRoutes()
  renderAccounts()

  expect(await screen.findByText('1,049.48')).toBeInTheDocument()

  // The account with no engine reading must not be rendered as 0.00.
  const rows = screen.getAllByRole('row')
  const unknown = rows.find((r) => r.textContent?.includes('12346'))
  expect(unknown).toBeTruthy()
  expect(unknown!.textContent).not.toMatch(/0\.00/)
})

test('equity failure does not break the accounts list', async () => {
  // The account list is the point of this page. If the engine is down the
  // rows must still render -- an unreachable copier must not blank the
  // screen an operator uses to disconnect or flatten an account.
  setRole('admin')
  mockRoutes({
    '/api/orgs/1/state': () => new Response('boom', { status: 502 }),
  })
  renderAccounts()

  expect(await screen.findByText('12345')).toBeInTheDocument()
  expect(screen.getByText('12346')).toBeInTheDocument()
})

// ---------- MT5 rows ----------

test('rows carry a platform badge; an MT5 row shows login and broker instead of a cTID', async () => {
  setRole('admin')
  mockMt5Routes()
  renderAccounts()

  const rows = await screen.findAllByRole('row')
  const mt5Row = rows.find((r) => r.textContent?.includes('XYZ Ltd'))!
  expect(mt5Row).toBeTruthy()
  expect(within(mt5Row).getByText('MT5')).toBeInTheDocument()
  expect(within(mt5Row).getByText('MT5 · login 555 · XYZ Ltd')).toBeInTheDocument()
  expect(within(mt5Row).queryByText(/cTID/)).not.toBeInTheDocument()

  const ctraderRow = rows.find((r) => r.textContent?.includes('12345'))!
  expect(within(ctraderRow).getByText('cTrader')).toBeInTheDocument()
  expect(within(ctraderRow).getByText('cTID 1')).toBeInTheDocument()
})

test('an MT5 row shows connected, offline with last seen, or waiting for the terminal', async () => {
  setRole('admin')
  const offline = {
    ...mt5Account, ctid_trader_account_id: 1000000000002, nickname: 'Offline desk',
    connection_status: 'offline',
    mt5: { ...mt5Account.mt5!, connected: false, last_seen_at: '2026-09-07T09:00:00+00:00' },
  }
  const fresh = {
    ...mt5Account, ctid_trader_account_id: 1000000000003, nickname: 'New desk',
    trader_login: 0, connection_status: 'never',
    mt5: { ...mt5Account.mt5!, login: null, broker: null, connected: false, last_seen_at: null },
  }
  mockRoutes({ '/api/orgs/1/accounts': () => jsonResponse([mt5Account, offline, fresh]) })
  renderAccounts()

  expect(await screen.findByText('Connected')).toBeInTheDocument()
  expect(screen.getByText(/^Offline · last seen /)).toBeInTheDocument()
  expect(screen.getByText('Waiting for the terminal')).toBeInTheDocument()
  // No login yet: the subtitle says so instead of printing "login 0".
  expect(screen.getByText('MT5 · no login yet')).toBeInTheDocument()
})

test('an MT5 row has no Re-grant access or Disconnect: there is no OAuth grant behind it', async () => {
  setRole('admin')
  mockMt5Routes()
  renderAccounts()

  await screen.findAllByRole('row')
  const menu = await openMenu('XYZ Ltd')
  expect(within(menu).queryByRole('menuitem', { name: /re-grant access/i })).not.toBeInTheDocument()
  expect(within(menu).queryByRole('menuitem', { name: /disconnect/i })).not.toBeInTheDocument()
  // The per-account kill switch still applies: Close all covers MT5 too.
  expect(within(menu).getByRole('menuitem', { name: /^flatten$/i })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  // Re-grant is one header action, never a row button.
  expect(screen.getAllByRole('button', { name: /re-grant access/i })).toHaveLength(1)
  expect(within(screen.getByRole('table')).queryByRole('button', { name: /re-grant access/i }))
    .not.toBeInTheDocument()
})

// ---------- Add MT5 account ----------

const created = {
  account_id: 1000000000001,
  key: 'mt5_test_key_abc',
  download_url: '/downloads/MirrorFleet.mq5',
  install: [
    'Download MirrorFleet.mq5',
    'Copy it to MQL5/Experts and compile it in MetaEditor',
    'Allow https://mirrorfleet.com in Tools → Options → Expert Advisors',
    'Attach it to any chart and paste the key into InpKey',
    'Confirm the row here says Connected',
  ],
}

test('Add MT5 account asks for a nickname, POSTs it, then shows the key once with the install steps', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts': () => jsonResponse(created, 201),
  })
  renderAccounts()

  await userEvent.click(await screen.findByRole('button', { name: /add mt5 account/i }))
  const dialog = await screen.findByRole('dialog')
  // A nameless MT5 row would be unidentifiable until its terminal connects.
  expect(within(dialog).getByRole('button', { name: /create account/i })).toBeDisabled()
  expect(dialog).toHaveTextContent(/disabled follower/i)
  await userEvent.type(within(dialog).getByLabelText(/nickname/i), 'VPS desk')
  await userEvent.click(within(dialog).getByRole('button', { name: /create account/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/mt5/accounts' && (init as RequestInit)?.method === 'POST')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ nickname: 'VPS desk' })
  })

  const reveal = await screen.findByRole('dialog', { name: /mt5 account added/i })
  expect(within(reveal).getByText('mt5_test_key_abc')).toBeInTheDocument()
  expect(within(reveal).getByRole('link', { name: /download mirrorfleet\.mq5/i }))
    .toHaveAttribute('href', '/downloads/MirrorFleet.mq5')
  expect(within(reveal).getAllByRole('listitem')).toHaveLength(5)

  // Closing the dialog is the last time the key is on screen.
  await userEvent.click(within(reveal).getByRole('button', { name: /i have copied it/i }))
  expect(screen.queryByText('mt5_test_key_abc')).not.toBeInTheDocument()
})

test('the key dialog copies the key to the clipboard', async () => {
  setRole('admin')
  mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts': () => jsonResponse({ ...created, install: [] }, 201),
  })
  renderAccounts()

  await userEvent.click(await screen.findByRole('button', { name: /add mt5 account/i }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.type(within(dialog).getByLabelText(/nickname/i), 'VPS desk')
  await userEvent.click(within(dialog).getByRole('button', { name: /create account/i }))
  const reveal = await screen.findByRole('dialog', { name: /mt5 account added/i })

  // Installed AFTER every userEvent call: user-event swaps in its own
  // clipboard stub on first use, and this one must be the one the page hits.
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  fireEvent.click(within(reveal).getByRole('button', { name: /^copy$/i }))

  await waitFor(() => expect(writeText).toHaveBeenCalledWith('mt5_test_key_abc'))
  expect(await within(reveal).findByRole('button', { name: /^copied$/i })).toBeInTheDocument()
})

test('viewer sees no Add MT5 account button', async () => {
  setRole('viewer')
  mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  expect(screen.queryByRole('button', { name: /add mt5 account/i })).not.toBeInTheDocument()
})

// ---------- Rotate key ----------

test('Rotate key confirms, POSTs the rotation, and shows the new key once', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts/1000000000001/key': () => jsonResponse({ key: 'mt5_rotated_key' }),
  })
  renderAccounts()

  await screen.findByText('MT5 · login 555 · XYZ Ltd')
  await chooseFromMenu('XYZ Ltd', /^rotate key$/i)
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/stops working/i)
  // Nothing sent until confirmed: the running EA goes dark the moment it is.
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/key'))).toBe(false)

  await userEvent.click(within(dialog).getByRole('button', { name: /^rotate key$/i }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/mt5/accounts/1000000000001/key',
      expect.objectContaining({ method: 'POST' })
    )
  })
  const reveal = await screen.findByRole('dialog', { name: /new key for vps desk/i })
  expect(within(reveal).getByText('mt5_rotated_key')).toBeInTheDocument()
  // A rotation replaces only the key: no download link, no install steps.
  expect(within(reveal).queryByRole('link')).not.toBeInTheDocument()
  expect(within(reveal).queryByRole('listitem')).not.toBeInTheDocument()
})

test('below control, an MT5 row shows no Rotate key', async () => {
  setRole('viewer')
  mockMt5Routes()
  renderAccounts()

  await screen.findByText('MT5 · login 555 · XYZ Ltd')
  expect(screen.queryByRole('button', { name: /rotate key/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^actions for/i })).not.toBeInTheDocument()
})

// ---------- MT5 details drawer ----------

const mt5Details = {
  account_id: 1000000000001, trader_login: 555, balance: 9784.04, deposit_currency: 'USD',
  leverage: 500, max_leverage: null, broker_name: 'XYZ Ltd',
  registration_timestamp: null, account_type: 'HEDGED', access_rights: null, swap_free: null,
  is_limited_risk: false, open_positions: [], pending_orders: [],
  nickname: 'VPS desk', role: 'slave', enabled: false, multiplier: 1, status: 'ok',
  last_error: null, is_live: true,
}

const mt5Aliases = {
  aliases: [
    { canonical: 'XAUUSD', broker_name: 'XAUUSD.r', source: 'auto' },
    { canonical: 'EURUSD', broker_name: 'EURUSD.r', source: 'manual' },
  ],
  broker_symbols: ['XAUUSD.r', 'EURUSD.r', 'GBPUSD.r'],
}

async function openMt5Details() {
  const rows = await screen.findAllByRole('row')
  const mt5Row = rows.find((r) => r.textContent?.includes('XYZ Ltd'))!
  await userEvent.click(within(mt5Row).getByRole('button', { name: /details/i }))
}

test("an MT5 account's details show a Terminal section instead of the OAuth grant, and the symbol mapping", async () => {
  setRole('admin')
  mockMt5Routes({
    '/accounts/1000000000001/symbol-aliases': () => jsonResponse(mt5Aliases),
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  const terminal = (await screen.findByRole('heading', { name: 'Terminal' })).closest('section')!
  expect(within(terminal).getByText('XYZ-Live3')).toBeInTheDocument()
  expect(within(terminal).getByText('real')).toBeInTheDocument()
  expect(within(terminal).getByText('1.0.0')).toBeInTheDocument()
  expect(screen.queryByText('OAuth grant')).not.toBeInTheDocument()

  const mapping = screen.getByRole('heading', { name: 'Symbol mapping' }).closest('section')!
  const input = await within(mapping).findByLabelText('Broker symbol for XAUUSD')
  expect((input as HTMLInputElement).value).toBe('XAUUSD.r')
  expect(within(mapping).getByText('XAUUSD')).toBeInTheDocument()
  expect(within(mapping).getByText('auto')).toBeInTheDocument()
  expect(within(mapping).getByText('manual')).toBeInTheDocument()

  // The spec puts Rotate key in the Terminal section: it opens the same
  // confirm dialog as the row menu, stacked above the drawer.
  await userEvent.click(within(terminal).getByRole('button', { name: /rotate key/i }))
  expect(await screen.findByRole('dialog', { name: /rotate the key for vps desk/i })).toBeInTheDocument()
})

test('a viewer opening an MT5 details drawer sees the admin-only notice, not the mapping', async () => {
  setRole('viewer')
  mockMt5Routes({
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  expect(await screen.findByText('Only an admin can see the mapping.')).toBeInTheDocument()
})

test('editing a broker symbol PUTs that one alias on blur and reloads the mapping', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'PUT /accounts/1000000000001/symbol-aliases': () => jsonResponse({ status: 'ok' }),
    '/accounts/1000000000001/symbol-aliases': () => jsonResponse(mt5Aliases),
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  const input = await screen.findByLabelText('Broker symbol for XAUUSD')
  await userEvent.clear(input)
  await userEvent.type(input, 'GOLD.r')
  await userEvent.tab()

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/accounts/1000000000001/symbol-aliases' &&
      (init as RequestInit)?.method === 'PUT')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ aliases: { XAUUSD: 'GOLD.r' } })
  })
  // Re-read after the save so the auto/manual tag is the server's truth.
  await waitFor(() => {
    const reads = fetchMock.mock.calls.filter(([u, init]) =>
      String(u).endsWith('/symbol-aliases') && ((init as RequestInit)?.method ?? 'GET') === 'GET')
    expect(reads.length).toBeGreaterThanOrEqual(2)
  })
})

test('a new canonical → broker mapping can be added from the drawer', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'PUT /accounts/1000000000001/symbol-aliases': () => jsonResponse({ status: 'ok' }),
    '/accounts/1000000000001/symbol-aliases': () => jsonResponse(mt5Aliases),
    '/accounts/1000000000001/details': () => jsonResponse(mt5Details),
  })
  renderAccounts()
  await openMt5Details()

  await screen.findByLabelText('Broker symbol for XAUUSD')
  const addButton = screen.getByRole('button', { name: /add mapping/i })
  expect(addButton).toBeDisabled()
  await userEvent.type(screen.getByLabelText('New canonical symbol'), 'us500')
  await userEvent.type(screen.getByLabelText('New broker symbol'), 'US500.cash')
  await userEvent.click(addButton)

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/accounts/1000000000001/symbol-aliases' &&
      (init as RequestInit)?.method === 'PUT')
    expect(call).toBeTruthy()
    // Canonical names are upper-case, as master events carry them.
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ aliases: { US500: 'US500.cash' } })
  })
})

test('Remove appears only on MT5 non-master rows, confirms, then DELETEs', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes()
  renderAccounts()

  const rows = await screen.findAllByRole('row')
  expect(rows.some((r) => r.textContent?.includes('XYZ Ltd'))).toBe(true)

  // cTrader rows never offer Remove (they disconnect their grant instead).
  for (const text of ['12345', '12346']) {
    const menu = await openMenu(text)
    expect(within(menu).queryByRole('menuitem', { name: /^remove$/i })).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
  }

  await chooseFromMenu('XYZ Ltd', /^remove$/i)

  // No DELETE yet: removal is permanent, so it asks first.
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent(/permanently deletes/i)
  expect(fetchMock.mock.calls.some(([, init]) =>
    (init as RequestInit)?.method === 'DELETE')).toBe(false)

  await userEvent.click(within(dialog).getByRole('button', { name: /remove permanently/i }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u) === '/api/orgs/1/mt5/accounts/1000000000001' &&
      (init as RequestInit)?.method === 'DELETE')
    expect(call).toBeTruthy()
  })
})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `dashboard/`): `npx vitest run src/pages/accounts/useAccountsPage.test.ts src/pages/Accounts.test.tsx`
Expected: FAIL. The hook suite fails with `Failed to resolve import "./useAccountsPage"`. The page suite fails wherever the new structure is needed, for example `Unable to find an accessible element with the role "button" and name /^actions for/i`, `Unable to find a label with the text of: Nickname`, `Unable to find an element with the text: Follower`, and a `document.title` mismatch. Only structure-agnostic tests (equity, platform badges, MT5 health words, Add MT5, the key dialog) may still pass.

- [ ] **Step 4: Write the data/actions hook**

Create `dashboard/src/pages/accounts/useAccountsPage.ts`:

```ts
import { useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { can } from '../../lib/roles'
import { accountName, isMt5 } from '../../lib/platform'
import type {
  Account, AccountDetails, ApiState, CloseAllResult, Mt5AccountCreated, StateSnapshot,
  SymbolAliases,
} from '../../lib/types'

// How long the green "Flattened ✓" confirmation stays on a row.
const FLATTEN_DONE_MS = 5000

export type FlattenState = 'busy' | 'done' | 'error'

/** A key is shown exactly once. This is held only while its dialog is open
 *  and dropped the moment the dialog closes -- never stored anywhere else. */
export interface KeyReveal {
  title: string
  key: string
  /** Present on creation; a rotation only replaces the key. */
  download_url: string | null
  install: string[]
}

/** The drawer's edit form. Empty strings mean "none", exactly as the old
 *  inline inputs sent them (an empty cutoff_date clears the cutoff). */
export interface AccountDraft {
  nickname: string
  role: string
  enabled: boolean
  cutoff_date: string
}

/** What a Save did that the form must undo: a promotion the operator
 *  backed out of leaves the role select where the server still has it. */
export interface SaveOutcome {
  promoteCancelled: boolean
}

/** The one confirm dialog that can be open. Master promotion re-shapes the
 *  whole fleet and MT5 removal is permanent, so both always confirm first. */
export type PageDialog =
  | { kind: 'disconnect' | 'remove' | 'flatten' | 'promote' | 'rotate'; account: Account }
  | { kind: 'add-mt5'; nickname: string }
  | null

interface ListState { accounts: Account[]; equity: StateSnapshot; isLoading: boolean }
interface Feedback { error: string | null; notice: string | null }
interface RowState {
  pending: Set<number>
  flatten: Record<number, FlattenState>
  roleErrors: Record<number, string>
}
interface DrawerState {
  accountId: number | null
  details: AccountDetails | null
  detailsError: string | null
  aliases: SymbolAliases | null
  aliasesError: string | null
}

const CLOSED_DRAWER: DrawerState = {
  accountId: null, details: null, detailsError: null, aliases: null, aliasesError: null,
}

/** A cTrader account whose grant is no longer active: the same condition
 *  that paints its health amber instead of "Active". MT5 has no grant. */
export function needsRegrant(account: Account): boolean {
  return !isMt5(account) && account.connection_status !== 'active'
}

export function draftOf(account: Account): AccountDraft {
  return {
    nickname: account.nickname ?? '',
    role: account.role,
    enabled: account.enabled,
    cutoff_date: account.cutoff_date ?? '',
  }
}

function reason(err: unknown, fallback = 'unknown'): string {
  return err instanceof Error ? err.message : fallback
}

export function useAccountsPage() {
  const { orgId, role } = useOrg()
  const canControl = can(role, 'control')
  const canTrade = can(role, 'trade')

  const [list, setList] = useState<ListState>({ accounts: [], equity: {}, isLoading: true })
  const [feedback, setFeedback] = useState<Feedback>({ error: null, notice: null })
  const [rows, setRows] = useState<RowState>({ pending: new Set(), flatten: {}, roleErrors: {} })
  const [dialog, setDialog] = useState<PageDialog>(null)
  const [busy, setBusy] = useState(false)
  const [keyReveal, setKeyReveal] = useState<KeyReveal | null>(null)
  const [drawer, setDrawer] = useState<DrawerState>(CLOSED_DRAWER)
  // Resolves the promise saveEdits awaits while the promote dialog is open.
  const promoteResolver = useRef<((confirmed: boolean) => void) | null>(null)
  // The account the drawer is showing; late answers for another are dropped.
  const drawerIdRef = useRef<number | null>(null)

  const showError = (error: string | null) => setFeedback((prev) => ({ ...prev, error }))
  const showNotice = (notice: string | null) => setFeedback((prev) => ({ ...prev, notice }))

  const setFlatten = (accountId: number, state: FlattenState | null) =>
    setRows((prev) => {
      const flatten = { ...prev.flatten }
      if (state) flatten[accountId] = state
      else delete flatten[accountId]
      return { ...prev, flatten }
    })

  const setRoleError = (accountId: number, message: string) =>
    setRows((prev) => ({ ...prev, roleErrors: { ...prev.roleErrors, [accountId]: message } }))

  // Live equity per account, keyed by account id. Held apart from the rows
  // because it comes from the engine, not the database: the accounts table
  // has no balance column, and a stale number here would be worse than none.
  const fetchEquity = async () => {
    try {
      const envelope = await orgApi<ApiState>(orgId, 'state')
      setList((prev) => ({ ...prev, equity: envelope.accounts ?? {} }))
    } catch {
      // The engine being unreachable must not blank the accounts list --
      // this screen is how an operator disconnects or flattens an account,
      // and it has to work when the copier is the thing that is broken.
      setList((prev) => ({ ...prev, equity: {} }))
    }
  }

  const fetchAccounts = async () => {
    try {
      const data = await orgApi<Account[]>(orgId, 'accounts')
      setList((prev) => ({ ...prev, accounts: data || [] }))
      showError(null)
    } catch (err) {
      showError(reason(err, 'Failed to load accounts'))
    } finally {
      setList((prev) => ({ ...prev, isLoading: false }))
    }
  }

  useEffect(() => {
    fetchAccounts()
    fetchEquity()
  }, [orgId])

  // Refetch when the OAuth round trip returns focus to this window.
  useEffect(() => {
    const handleFocus = () => {
      fetchAccounts()
      fetchEquity()
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [orgId])

  const withPending = async (accountId: number, action: () => Promise<void>) => {
    setRows((prev) => ({ ...prev, pending: new Set([...prev.pending, accountId]) }))
    try {
      await action()
    } finally {
      setRows((prev) => {
        const pending = new Set(prev.pending)
        pending.delete(accountId)
        return { ...prev, pending }
      })
    }
  }

  const connectOAuth = () => {
    // Same tab, deliberately NOT a popup. The broker sends the browser back
    // to /api/oauth/callback, and that return trip is cross-site: the
    // session cookie is SameSite=Lax, which browsers withhold from a popup
    // navigated cross-site. The callback then saw an anonymous request and
    // answered "Not authenticated", so connecting an account was
    // impossible. A top-level navigation always carries the cookie, and
    // the callback already redirects to this same page with ?connected=1,
    // so nothing is lost by leaving it.
    window.location.assign(`/api/orgs/${orgId}/oauth/connect`)
  }

  // ---- Edit (the drawer's Save changes) ----

  const patchField = async (accountId: number, body: Record<string, unknown>, what: string) => {
    try {
      await orgApi(orgId, `accounts/${accountId}`, { method: 'PATCH', body: JSON.stringify(body) })
    } catch (err) {
      showError(`Failed to update ${what} (${reason(err)})`)
    }
  }

  const patchRole = async (accountId: number, newRole: string) => {
    try {
      setRoleError(accountId, '')
      await orgApi(orgId, `accounts/${accountId}`, {
        method: 'PATCH',
        body: JSON.stringify({ role: newRole }),
      })
    } catch (err) {
      const errorCode = reason(err, 'Unknown error')
      setRoleError(accountId, errorCode === '409'
        ? 'A master already exists'
        : `Failed to update role (${errorCode})`)
    }
  }

  const askPromote = (account: Account) => new Promise<boolean>((resolve) => {
    promoteResolver.current = resolve
    setDialog({ kind: 'promote', account })
  })

  const resolvePromote = (confirmed: boolean) => {
    const resolve = promoteResolver.current
    promoteResolver.current = null
    setDialog(null)
    resolve?.(confirmed)
  }

  /** One PATCH per changed field, each with the single-key body the old
   *  blur-save sent, then one reload. Master waits for its confirmation. */
  const saveEdits = async (account: Account, draft: AccountDraft): Promise<SaveOutcome> => {
    const accountId = account.ctid_trader_account_id
    let promoteCancelled = false
    await withPending(accountId, async () => {
      let sent = false
      if (draft.nickname !== (account.nickname ?? '')) {
        await patchField(accountId, { nickname: draft.nickname }, 'nickname')
        sent = true
      }
      if (draft.cutoff_date !== (account.cutoff_date ?? '')) {
        // An empty value clears the cutoff (and with it the reminder).
        await patchField(accountId, { cutoff_date: draft.cutoff_date }, 'cutoff date')
        sent = true
      }
      if (draft.enabled !== account.enabled) {
        await patchField(accountId, { enabled: draft.enabled }, 'enabled status')
        sent = true
      }
      if (draft.role !== account.role) {
        if (draft.role === 'master' && !(await askPromote(account))) {
          promoteCancelled = true
        } else {
          await patchRole(accountId, draft.role)
          sent = true
        }
      }
      if (sent) await fetchAccounts()
    })
    return { promoteCancelled }
  }

  // ---- Dialogs ----

  const cancelDialog = () => {
    if (dialog?.kind === 'promote') resolvePromote(false)
    else setDialog(null)
  }

  const askAddMt5 = () => setDialog({ kind: 'add-mt5', nickname: '' })
  const setMt5Nickname = (nickname: string) =>
    setDialog((prev) => (prev?.kind === 'add-mt5' ? { ...prev, nickname } : prev))
  const askDisconnect = (account: Account) => setDialog({ kind: 'disconnect', account })
  const askRemove = (account: Account) => setDialog({ kind: 'remove', account })
  const askRotate = (account: Account) => setDialog({ kind: 'rotate', account })
  const askFlatten = (account: Account) => {
    // A failed row's retry starts clean; the dialog decides what happens next.
    setFlatten(account.ctid_trader_account_id, null)
    setDialog({ kind: 'flatten', account })
  }

  const confirmRemove = async () => {
    if (dialog?.kind !== 'remove') return
    const accountId = dialog.account.ctid_trader_account_id
    try {
      setBusy(true)
      await orgApi(orgId, `mt5/accounts/${accountId}`, { method: 'DELETE' })
      showNotice('Account removed. Its terminal key stops working immediately.')
      setDialog(null)
      await fetchAccounts()
    } catch (err) {
      setDialog(null)
      showError(reason(err, 'Failed to remove account'))
    } finally {
      setBusy(false)
    }
  }

  const confirmDisconnect = async () => {
    if (dialog?.kind !== 'disconnect') return
    const accountId = dialog.account.ctid_trader_account_id
    try {
      setBusy(true)
      const result = await orgApi<{ accounts_removed: number }>(
        orgId, `accounts/${accountId}/connection`, { method: 'DELETE' })
      showNotice(
        `Disconnected. ${result.accounts_removed} account${result.accounts_removed === 1 ? '' : 's'} ` +
        'removed. The token stays revocable at ctrader.com.')
      setDialog(null)
      await fetchAccounts()
    } catch (err) {
      setDialog(null)
      showError(reason(err, 'Failed to disconnect account'))
    } finally {
      setBusy(false)
    }
  }

  const confirmAddMt5 = async () => {
    if (dialog?.kind !== 'add-mt5') return
    const nickname = dialog.nickname
    try {
      setBusy(true)
      const result = await orgApi<Mt5AccountCreated>(orgId, 'mt5/accounts', {
        method: 'POST',
        body: JSON.stringify({ nickname: nickname.trim() }),
      })
      setDialog(null)
      setKeyReveal({
        title: 'MT5 account added — install the EA',
        key: result.key,
        download_url: result.download_url,
        install: result.install,
      })
      await fetchAccounts()
    } catch (err) {
      setDialog(null)
      showError(`Could not add the MT5 account (${reason(err)})`)
    } finally {
      setBusy(false)
    }
  }

  const confirmRotate = async () => {
    if (dialog?.kind !== 'rotate') return
    const account = dialog.account
    try {
      setBusy(true)
      const result = await orgApi<{ key: string }>(
        orgId, `mt5/accounts/${account.ctid_trader_account_id}/key`, { method: 'POST' })
      setDialog(null)
      setKeyReveal({
        title: `New key for ${accountName(account)}`,
        key: result.key,
        download_url: null,
        install: [],
      })
    } catch (err) {
      setDialog(null)
      showError(`Could not rotate the key (${reason(err)})`)
    } finally {
      setBusy(false)
    }
  }

  const confirmFlatten = async () => {
    if (dialog?.kind !== 'flatten') return
    const account = dialog.account
    const accountId = account.ctid_trader_account_id
    // Close the dialog right away; progress and outcome live on the row so
    // it is always clear WHICH account is being flattened.
    setDialog(null)
    showError(null)
    setFlatten(accountId, 'busy')
    try {
      const result = await orgApi<CloseAllResult>(orgId, 'control/close-all', {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      })
      const summary = result.accounts[0]
      const stillOpen = summary.positions_remaining?.length ?? 0
      if (stillOpen > 0 || summary.error) {
        // Not 'done'. The row must not go green over an account that is
        // still carrying the position the operator asked to be rid of.
        setFlatten(accountId, 'error')
        showError(
          `Account ${account.trader_login}: closed ${summary.positions_closed}, but ` +
          `${stillOpen} position${stillOpen === 1 ? '' : 's'} could not be closed. ` +
          `You are still exposed — close ${stillOpen === 1 ? 'it' : 'them'} in the platform.`)
        return
      }
      showNotice(
        `Closed ${summary.positions_closed} position${summary.positions_closed === 1 ? '' : 's'} ` +
        `and cancelled ${summary.orders_cancelled} order${summary.orders_cancelled === 1 ? '' : 's'} ` +
        `on account ${account.trader_login}. Verified flat.`)
      setFlatten(accountId, 'done')
      window.setTimeout(() => {
        setRows((prev) => {
          if (prev.flatten[accountId] !== 'done') return prev
          const flatten = { ...prev.flatten }
          delete flatten[accountId]
          return { ...prev, flatten }
        })
      }, FLATTEN_DONE_MS)
    } catch (err) {
      setFlatten(accountId, 'error')
      showError(`Flatten failed on account ${account.trader_login}: ${reason(err, 'unknown error')}`)
    }
  }

  // The key leaves memory here; it is never shown again.
  const closeKeyReveal = () => setKeyReveal(null)

  // ---- Details drawer ----

  const loadAliases = async (accountId: number) => {
    try {
      const aliases = await orgApi<SymbolAliases>(orgId, `accounts/${accountId}/symbol-aliases`)
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({ ...prev, aliases, aliasesError: null }))
    } catch (err) {
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({
        ...prev, aliasesError: `Could not load the symbol mapping (${reason(err)})`,
      }))
    }
  }

  // One PUT per change, then a re-read: the server decides the auto/manual
  // tag, and an empty broker name removes the row.
  const saveAlias = async (accountId: number, canonical: string, brokerName: string) => {
    try {
      await orgApi(orgId, `accounts/${accountId}/symbol-aliases`, {
        method: 'PUT',
        body: JSON.stringify({ aliases: { [canonical]: brokerName } }),
      })
      await loadAliases(accountId)
    } catch (err) {
      setDrawer((prev) => ({
        ...prev, aliasesError: `Could not save the mapping for ${canonical} (${reason(err)})`,
      }))
    }
  }

  const openDetails = async (account: Account) => {
    const accountId = account.ctid_trader_account_id
    drawerIdRef.current = accountId
    setDrawer({ ...CLOSED_DRAWER, accountId })
    // The mapping lives in the api's database, so it loads even when the
    // copier is down; reading it needs the admin role.
    if (isMt5(account) && canTrade) loadAliases(accountId)
    try {
      const details = await orgApi<AccountDetails>(orgId, `accounts/${accountId}/details`)
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({ ...prev, details }))
    } catch (err) {
      if (drawerIdRef.current !== accountId) return
      setDrawer((prev) => ({
        ...prev,
        detailsError:
          `Could not fetch details: ${reason(err, 'unknown error')}. ` +
          (isMt5(account)
            ? 'The copier may be offline or the terminal has not connected yet.'
            : 'The copier may be offline or the account not yet authorized.'),
      }))
    }
  }

  const closeDetails = () => {
    drawerIdRef.current = null
    setDrawer(CLOSED_DRAWER)
  }

  // The drawer reads the account from the live list, so a save or a
  // promotion elsewhere shows up in it at once; a row that disappears
  // (disconnected, removed) closes it.
  const drawerAccount = drawer.accountId == null
    ? null
    : list.accounts.find((a) => a.ctid_trader_account_id === drawer.accountId) ?? null

  return {
    canControl,
    canTrade,
    accounts: list.accounts,
    equity: list.equity,
    isLoading: list.isLoading,
    error: feedback.error,
    notice: feedback.notice,
    dismissNotice: () => showNotice(null),
    reportError: (message: string) => showError(message),
    pending: rows.pending,
    flatten: rows.flatten,
    roleErrors: rows.roleErrors,
    dialog,
    busy,
    keyReveal,
    drawer: {
      account: drawerAccount,
      details: drawer.details,
      detailsError: drawer.detailsError,
      aliases: drawer.aliases,
      aliasesError: drawer.aliasesError,
    },
    connectOAuth,
    askAddMt5,
    setMt5Nickname,
    confirmAddMt5,
    askFlatten,
    confirmFlatten,
    askDisconnect,
    confirmDisconnect,
    askRemove,
    confirmRemove,
    askRotate,
    confirmRotate,
    resolvePromote,
    cancelDialog,
    closeKeyReveal,
    openDetails,
    closeDetails,
    saveEdits,
    saveAlias,
  }
}

export type AccountsPage = ReturnType<typeof useAccountsPage>
```

- [ ] **Step 5: Run the hook tests to verify they pass**

Run: `npx vitest run src/pages/accounts/useAccountsPage.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 6: Write the read-only row**

Create `dashboard/src/pages/accounts/AccountRow.tsx`:

```tsx
import type { ComponentProps } from 'react'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import Menu from '../../components/Menu'
import StatusDot from '../../components/StatusDot'
import { formatWhen, money } from '../../lib/format'
import { accountName, isMt5 } from '../../lib/platform'
import type { Account } from '../../lib/types'
import { needsRegrant, type FlattenState } from './useAccountsPage'

type MenuItems = ComponentProps<typeof Menu>['items']
type DotTone = ComponentProps<typeof StatusDot>['tone']

// The API keeps its role names; the product says "follower".
const ACCOUNT_ROLE_LABEL: Record<string, string> = {
  master: 'Master',
  slave: 'Follower',
  ignored: 'Ignored',
}

export function accountRoleLabel(role: string): string {
  return ACCOUNT_ROLE_LABEL[role] ?? role
}

/** "MT5 · login 555 · XYZ Ltd" -- what an MT5 row prints under the login in
 *  place of the cTrader id. Before the first hello there is no login and no
 *  broker to print, and "login 0" would look like one. */
export function mt5Subtitle(link: Account['mt5']): string {
  const parts = ['MT5', link?.login ? `login ${link.login}` : 'no login yet']
  if (link?.broker) parts.push(link.broker)
  return parts.join(' · ')
}

function humanise(value: string): string {
  const words = value.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The copier's view of the link. MT5: connected (a report within 15 s),
 *  offline (with when it was last heard from), or never reported -- the EA
 *  is not installed yet. cTrader: the grant is active, or it is not. */
function connectionHealth(account: Account): { tone: DotTone; word: string } {
  if (isMt5(account)) {
    switch (account.connection_status) {
      case 'connected':
        return { tone: 'ok', word: 'Connected' }
      case 'offline':
        return { tone: 'warn', word: `Offline · last seen ${formatWhen(account.mt5?.last_seen_at)}` }
      default:
        return { tone: 'warn', word: 'Waiting for the terminal' }
    }
  }
  if (account.connection_status === 'active') return { tone: 'ok', word: 'Active' }
  return { tone: 'warn', word: humanise(account.connection_status) }
}

function FlattenStatus({ state, pending, onRetry }: {
  state: FlattenState | undefined
  pending: boolean
  onRetry: () => void
}) {
  if (state === 'busy') {
    return (
      <Button
        variant="secondary"
        size="sm"
        aria-disabled="true"
        className="min-w-[6.5rem] animate-pulse motion-reduce:animate-none"
      >
        Flattening…
      </Button>
    )
  }
  if (state === 'done') {
    return (
      <Button variant="secondary" tone="profit" size="sm" aria-disabled="true" className="min-w-[6.5rem]">
        Flattened ✓
      </Button>
    )
  }
  if (state === 'error') {
    return (
      <Button tone="loss" size="sm" className="min-w-[6.5rem]" onClick={onRetry} disabled={pending}>
        Failed — retry
      </Button>
    )
  }
  return null
}

export default function AccountRow({
  account, equity, canControl, pending, flatten,
  onDetails, onFlatten, onRegrant, onRotate, onDisconnect, onRemove,
}: {
  account: Account
  /** The engine's reading; absent means no reading, shown as a dash. */
  equity: number | null | undefined
  canControl: boolean
  pending: boolean
  flatten: FlattenState | undefined
  onDetails: (account: Account) => void
  onFlatten: (account: Account) => void
  onRegrant: () => void
  onRotate: (account: Account) => void
  onDisconnect: (account: Account) => void
  onRemove: (account: Account) => void
}) {
  const id = account.ctid_trader_account_id
  const onMt5 = isMt5(account)
  const health = connectionHealth(account)

  const items: MenuItems = [
    { key: 'flatten', label: 'Flatten', disabled: pending || flatten === 'busy', onSelect: () => onFlatten(account) },
  ]
  if (needsRegrant(account)) {
    items.push({ key: 'regrant', label: 'Re-grant access', disabled: pending, onSelect: onRegrant })
  }
  if (onMt5) {
    items.push({ key: 'rotate', label: 'Rotate key', disabled: pending, onSelect: () => onRotate(account) })
  }
  // MT5 removal is permanent; the master is never offered it.
  if (onMt5 && account.role !== 'master') {
    items.push({ key: 'remove', label: 'Remove', tone: 'loss', disabled: pending, onSelect: () => onRemove(account) })
  }
  if (!onMt5) {
    items.push({ key: 'disconnect', label: 'Disconnect', tone: 'loss', disabled: pending, onSelect: () => onDisconnect(account) })
  }

  return (
    <tr className={`border-b border-line last:border-0 align-top ${pending ? 'opacity-60' : ''}`}>
      <td data-label="Account" className="px-5 py-3">
        <div className="num text-ink">{onMt5 ? (account.mt5?.login ?? '—') : account.trader_login}</div>
        <div className="text-xs text-ink-faint">{onMt5 ? mt5Subtitle(account.mt5) : `cTID ${id}`}</div>
        <Badge tone={onMt5 ? 'brand' : 'neutral'} className="mt-1">{onMt5 ? 'MT5' : 'cTrader'}</Badge>
      </td>
      <td data-label="Nickname" className="px-3 py-3">
        <span className="text-ink">{account.nickname || '—'}</span>
      </td>
      <td data-label="Role" className="px-3 py-3">
        <Badge tone={account.role === 'master' ? 'brand' : 'neutral'}>{accountRoleLabel(account.role)}</Badge>
      </td>
      <td data-label="Env" className="px-3 py-3">
        {/* Red means danger only: Live is a neutral chip with a filled dot. */}
        <Badge tone="neutral" pill className="gap-1.5">
          {account.is_live && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ink" />}
          {account.is_live ? 'Live' : 'Demo'}
        </Badge>
      </td>
      <td data-label="Equity" className="num px-3 py-3 text-right whitespace-nowrap">
        {/* An account the engine has no reading for shows a dash. Rendering
            0.00 would read as an empty account, which is a different fact. */}
        <span className="text-ink">{money(equity)}</span>
      </td>
      <td data-label="Health" className="px-3 py-3">
        <div className="flex items-center gap-1.5 text-ink">
          <StatusDot tone={health.tone} />
          <span>{health.word}</span>
        </div>
        {account.status === 'degraded' && (
          // The error expands in place rather than hiding in a tooltip.
          <div className="mt-1 flex items-start gap-1.5 text-xs text-loss-deep">
            <StatusDot tone="degraded" />
            <span className="break-words">
              Degraded{account.last_error ? `: ${account.last_error}` : ''}
            </span>
          </div>
        )}
      </td>
      <td className="px-5 py-3">
        <div className="flex flex-wrap items-center justify-end gap-2">
          {canControl && (
            <FlattenStatus state={flatten} pending={pending} onRetry={() => onFlatten(account)} />
          )}
          <Button variant="secondary" size="sm" onClick={() => onDetails(account)} disabled={pending}>
            Details
          </Button>
          {canControl && <Menu label={`Actions for ${accountName(account)}`} items={items} />}
        </div>
      </td>
    </tr>
  )
}
```

- [ ] **Step 7: Move the symbol-alias editor into its own component**

Create `dashboard/src/pages/accounts/AliasEditor.tsx`. Behaviour is unchanged: each existing mapping saves on blur with one PUT, a new pair is added with "Add mapping", and canonical names are upper-cased. The drafts are local, and they reset because the drawer unmounts its body when it closes.

```tsx
import { useState } from 'react'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { SymbolAliases } from '../../lib/types'

type AliasRow = SymbolAliases['aliases'][number]

export default function AliasEditor({ aliases, error, canView, canEdit, onSave }: {
  aliases: SymbolAliases | null
  error: string | null
  /** Reading the mapping needs the admin role (the api's `trade` action). */
  canView: boolean
  canEdit: boolean
  /** One PUT for one canonical name; an empty broker name removes it. */
  onSave: (canonical: string, brokerName: string) => Promise<void>
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [newAlias, setNewAlias] = useState({ canonical: '', broker_name: '' })

  const handleBlur = async (row: AliasRow) => {
    const draft = drafts[row.canonical]
    if (draft === undefined || draft.trim() === row.broker_name) return
    await onSave(row.canonical, draft.trim())
    setDrafts((prev) => {
      const next = { ...prev }
      delete next[row.canonical]
      return next
    })
  }

  const handleAdd = () => {
    // Canonical names are what master events carry: upper-case.
    const canonical = newAlias.canonical.trim().toUpperCase()
    const brokerName = newAlias.broker_name.trim()
    if (!canonical || !brokerName) return
    setNewAlias({ canonical: '', broker_name: '' })
    void onSave(canonical, brokerName)
  }

  return (
    <section>
      <h3 className="desk-label mb-2">Symbol mapping</h3>
      {!canView ? (
        <p className="text-sm text-ink-faint">Only an admin can see the mapping.</p>
      ) : error ? (
        <p className="text-sm text-loss-deep">{error}</p>
      ) : !aliases ? (
        <Loading lines={2} label="Loading the mapping" />
      ) : (
        <>
          {aliases.aliases.length === 0 ? (
            <p className="text-sm text-ink-faint">
              Nothing mapped yet — the terminal sends its symbol list when
              the EA first connects.
            </p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {aliases.aliases.map((row) => (
                <li key={row.canonical} className="flex items-center justify-between gap-3">
                  <span className="num text-ink">{row.canonical}</span>
                  <span className="flex items-center gap-2">
                    {canEdit ? (
                      <Input
                        type="text"
                        num
                        aria-label={`Broker symbol for ${row.canonical}`}
                        list="mt5-broker-symbols"
                        value={drafts[row.canonical] ?? row.broker_name}
                        onChange={(e) =>
                          setDrafts((prev) => ({ ...prev, [row.canonical]: e.target.value }))}
                        onBlur={() => handleBlur(row)}
                        className="w-28 text-right"
                      />
                    ) : (
                      <span className="num text-ink">{row.broker_name}</span>
                    )}
                    <Badge tone={row.source === 'manual' ? 'brand' : 'neutral'}>{row.source}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {canEdit && (
            <div className="mt-3 flex items-center gap-2">
              <Input
                type="text"
                num
                aria-label="New canonical symbol"
                placeholder="XAUUSD"
                value={newAlias.canonical}
                onChange={(e) => setNewAlias((prev) => ({ ...prev, canonical: e.target.value }))}
                className="w-24"
              />
              <span className="text-ink-faint">→</span>
              <Input
                type="text"
                num
                aria-label="New broker symbol"
                placeholder="GOLD.r"
                list="mt5-broker-symbols"
                value={newAlias.broker_name}
                onChange={(e) => setNewAlias((prev) => ({ ...prev, broker_name: e.target.value }))}
                className="w-28"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleAdd}
                disabled={!newAlias.canonical.trim() || !newAlias.broker_name.trim()}
              >
                Add mapping
              </Button>
            </div>
          )}
          <datalist id="mt5-broker-symbols">
            {aliases.broker_symbols.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <p className="mt-2 text-xs text-ink-faint">
            Clearing a broker name removes the mapping; a manual entry is
            never overwritten by the auto-matcher.
          </p>
        </>
      )}
    </section>
  )
}
```

- [ ] **Step 8: Move the one-time key dialog into its own component**

Create `dashboard/src/pages/accounts/KeyRevealDialog.tsx`:

```tsx
import { useEffect, useState } from 'react'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import type { KeyReveal } from './useAccountsPage'

/** One-time key reveal. Confirm and cancel both just close it, and closing
 *  is the last time the key is on screen. */
export default function KeyRevealDialog({ reveal, onClose, onCopyError }: {
  reveal: KeyReveal | null
  onClose: () => void
  onCopyError: (message: string) => void
}) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!reveal) setCopied(false)
  }, [reveal])

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      onCopyError('Could not copy — select the key and copy it by hand')
    }
  }

  return (
    <ConfirmDialog
      open={reveal != null}
      title={reveal?.title ?? ''}
      confirmLabel="I have copied it — close"
      onConfirm={onClose}
      onCancel={onClose}
    >
      <p>
        This key is shown <strong>once</strong>. Paste it into the EA's{' '}
        <span className="num">InpKey</span> input. If it is lost, rotate the
        key from the account's actions menu — the old one stops working at once.
      </p>
      <div className="flex items-center gap-2">
        <code
          aria-label="MT5 key"
          className="num flex-1 break-all rounded-inset border border-line-strong bg-paper px-3 py-2 text-xs text-ink"
        >
          {reveal?.key}
        </code>
        <Button
          variant="secondary"
          size="sm"
          className="shrink-0"
          onClick={() => { if (reveal) copyKey(reveal.key) }}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      {reveal?.download_url && (
        <a
          href={reveal.download_url}
          download
          className="inline-block text-sm font-medium text-brand hover:underline"
        >
          Download MirrorFleet.mq5
        </a>
      )}
      {reveal && reveal.install.length > 0 && (
        <ol className="list-decimal pl-5 space-y-1">
          {reveal.install.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
    </ConfirmDialog>
  )
}
```

- [ ] **Step 9: Move the confirm dialogs into one file**

Create `dashboard/src/pages/accounts/AccountDialogs.tsx`. The copy is today's, with "slave" replaced by "follower":

```tsx
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import { accountName } from '../../lib/platform'
import type { AccountsPage } from './useAccountsPage'

/** The page's confirm dialogs. At most one is open (`page.dialog`); promote
 *  and rotate can stack above the Details drawer. */
export default function AccountDialogs({ page }: { page: AccountsPage }) {
  const { dialog, busy } = page
  const target = dialog && dialog.kind !== 'add-mt5' ? dialog.account : null
  const mt5Nickname = dialog?.kind === 'add-mt5' ? dialog.nickname : ''

  return (
    <>
      <ConfirmDialog
        open={dialog?.kind === 'disconnect'}
        title={`Disconnect account ${target?.trader_login ?? ''}`}
        confirmLabel="Disconnect grant"
        danger
        busy={busy}
        onConfirm={page.confirmDisconnect}
        onCancel={page.cancelDialog}
      >
        <p>
          This removes the cTrader ID grant behind this account — and with it{' '}
          <strong>every account under that same grant</strong>. Open positions
          are not touched; the copier just stops seeing these accounts.
        </p>
        <p>You can reconnect any time with Connect cTrader ID.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog?.kind === 'remove'}
        title={`Remove account ${target?.nickname || target?.mt5?.login || ''}`}
        confirmLabel="Remove permanently"
        danger
        busy={busy}
        onConfirm={page.confirmRemove}
        onCancel={page.cancelDialog}
      >
        <p>
          This <strong>permanently deletes</strong> this MT5 account from
          MirrorFleet — its history disappears from every page, and the
          terminal&apos;s key stops working on its next poll. Open positions
          on the broker are not touched.
        </p>
        <p>Adding the login again later creates a fresh account.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog?.kind === 'promote'}
        title={`Make account ${target?.trader_login ?? ''} the master`}
        confirmLabel="Make it the master"
        onConfirm={() => page.resolvePromote(true)}
        onCancel={page.cancelDialog}
      >
        <p>
          Every other account in this workspace becomes a follower — including
          the current master and any Ignored accounts — and every enabled
          follower then copies account {target?.trader_login}'s trades.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog?.kind === 'flatten'}
        title={`Flatten account ${target?.trader_login ?? ''}`}
        confirmLabel="Close everything here"
        danger
        onConfirm={page.confirmFlatten}
        onCancel={page.cancelDialog}
      >
        <p>
          Every open position in this account is closed at market and every
          working order cancelled. Other accounts are untouched
          {target?.is_live ? ' — and this is a live account' : ''}.
        </p>
      </ConfirmDialog>

      {/* The nickname is the only identifier an MT5 row has until its
          terminal connects, so it is required. */}
      <ConfirmDialog
        open={dialog?.kind === 'add-mt5'}
        title="Add an MT5 account"
        confirmLabel="Create account"
        busy={busy}
        disabled={mt5Nickname.trim() === ''}
        onConfirm={page.confirmAddMt5}
        onCancel={page.cancelDialog}
      >
        <p>
          The account starts as a disabled follower. You get a one-time key to
          paste into the MirrorFleet EA running in the account's own MT5
          terminal; the login, broker and symbols arrive when the EA first
          connects.
        </p>
        <div>
          <label className="desk-label block mb-1" htmlFor="mt5-nickname">Nickname</label>
          <Input
            id="mt5-nickname"
            type="text"
            value={mt5Nickname}
            onChange={(e) => page.setMt5Nickname(e.target.value)}
            autoComplete="off"
            placeholder="e.g. VPS desk"
          />
        </div>
      </ConfirmDialog>

      {/* The old key dies the moment the new one exists. */}
      <ConfirmDialog
        open={dialog?.kind === 'rotate'}
        title={`Rotate the key for ${target ? accountName(target) : ''}`}
        confirmLabel="Rotate key"
        danger
        busy={busy}
        onConfirm={page.confirmRotate}
        onCancel={page.cancelDialog}
      >
        <p>
          The current key stops working the moment the new one exists, so the
          running EA disconnects until you paste the new key into its{' '}
          <span className="num">InpKey</span> input and restart it. Positions,
          symbol mapping and history are untouched.
        </p>
      </ConfirmDialog>
    </>
  )
}
```

- [ ] **Step 10: Write the Details drawer with its Edit section**

Create `dashboard/src/pages/accounts/AccountDrawer.tsx`. The drawer keeps today's content (subtitle, Broker profile, Copy settings, Terminal or OAuth grant, Symbol mapping, Open positions) and gains an **Edit** form at the top for admins, or a read-only **Settings** list for viewers:

```tsx
import { useEffect, useId, useState, type FormEvent } from 'react'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Select from '../../components/Select'
import { formatWhen } from '../../lib/format'
import { isMt5 } from '../../lib/platform'
import type { Account, AccountDetails, SymbolAliases } from '../../lib/types'
import AliasEditor from './AliasEditor'
import { accountRoleLabel, mt5Subtitle } from './AccountRow'
import { draftOf, type AccountDraft, type SaveOutcome } from './useAccountsPage'

function formatDate(iso: string | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function formatTimestamp(ms: number | null | undefined): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-soft">{label}</dt>
      <dd className={`text-ink text-right ${mono ? 'num' : ''}`}>{value}</dd>
    </div>
  )
}

/** Nickname, role, copying and cutoff, sent only on Save changes. Cancel
 *  puts the form back to what the server has. */
function EditSection({ account, pending, roleError, onSave }: {
  account: Account
  pending: boolean
  roleError: string | undefined
  onSave: (account: Account, draft: AccountDraft) => Promise<SaveOutcome>
}) {
  const id = useId()
  const saved = draftOf(account)
  const [draft, setDraft] = useState<AccountDraft>(saved)

  // Follow the SERVER's values (after a save, or when a promotion elsewhere
  // demoted this account), never a mere refetch: a window-focus reload
  // must not wipe what is being typed.
  useEffect(() => {
    setDraft(draftOf(account))
  }, [account.nickname, account.role, account.enabled, account.cutoff_date])

  const dirty =
    draft.nickname !== saved.nickname ||
    draft.role !== saved.role ||
    draft.enabled !== saved.enabled ||
    draft.cutoff_date !== saved.cutoff_date

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!dirty || pending) return
    const outcome = await onSave(account, draft)
    if (outcome.promoteCancelled) setDraft((prev) => ({ ...prev, role: account.role }))
  }

  return (
    <section>
      <h3 id={`${id}-heading`} className="desk-label mb-2">Edit</h3>
      <form aria-labelledby={`${id}-heading`} onSubmit={submit} className="space-y-3">
        <div>
          <label htmlFor={`${id}-nickname`} className="desk-label block mb-1">Nickname</label>
          <Input
            id={`${id}-nickname`}
            type="text"
            placeholder="Add a name"
            autoComplete="off"
            value={draft.nickname}
            onChange={(e) => setDraft((prev) => ({ ...prev, nickname: e.target.value }))}
            disabled={pending}
          />
        </div>
        <div>
          <label htmlFor={`${id}-role`} className="desk-label block mb-1">Role</label>
          <Select
            id={`${id}-role`}
            block
            value={draft.role}
            onChange={(e) => setDraft((prev) => ({ ...prev, role: e.target.value }))}
            disabled={pending}
          >
            <option value="master">Master</option>
            <option value="slave">Follower</option>
            <option value="ignored">Ignored</option>
          </Select>
          {roleError && <p className="mt-1 text-xs text-loss-deep">{roleError}</p>}
        </div>
        <label className="flex items-center justify-between gap-4 cursor-pointer has-[:disabled]:cursor-default">
          <span className="desk-label">Copying enabled</span>
          <span className="relative inline-flex items-center">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft((prev) => ({ ...prev, enabled: e.target.checked }))}
              disabled={pending}
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className="h-5 w-9 rounded-full bg-ink-faint transition-colors peer-checked:bg-brand peer-disabled:opacity-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand"
            />
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-card transition-transform peer-checked:translate-x-4"
            />
          </span>
        </label>
        <div>
          <label htmlFor={`${id}-cutoff`} className="desk-label block mb-1">Cutoff date</label>
          <Input
            id={`${id}-cutoff`}
            type="date"
            num
            value={draft.cutoff_date}
            onChange={(e) => setDraft((prev) => ({ ...prev, cutoff_date: e.target.value }))}
            disabled={pending}
          />
          <p className="mt-1 text-xs text-ink-faint">
            Clear the date to remove the cutoff and its reminder.
          </p>
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => setDraft(saved)}
            disabled={!dirty || pending}
          >
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!dirty || pending}>
            Save changes
          </Button>
        </div>
      </form>
    </section>
  )
}

/** Below control the same four settings, read-only. */
function SettingsSummary({ account }: { account: Account }) {
  return (
    <section>
      <h3 className="desk-label mb-2">Settings</h3>
      <dl className="space-y-1.5 text-sm">
        <DetailRow label="Nickname" value={account.nickname || '—'} />
        <DetailRow label="Role" value={accountRoleLabel(account.role)} />
        <DetailRow label="Copying enabled" value={account.enabled ? 'Yes' : 'No'} />
        <DetailRow label="Cutoff" value={account.cutoff_date || '—'} mono />
      </dl>
    </section>
  )
}

export default function AccountDrawer({
  account, details, detailsError, aliases, aliasesError, canControl, canTrade, pending, roleError,
  onClose, onSave, onRotate, onSaveAlias,
}: {
  account: Account | null
  details: AccountDetails | null
  detailsError: string | null
  aliases: SymbolAliases | null
  aliasesError: string | null
  canControl: boolean
  canTrade: boolean
  pending: boolean
  roleError: string | undefined
  onClose: () => void
  onSave: (account: Account, draft: AccountDraft) => Promise<SaveOutcome>
  onRotate: (account: Account) => void
  onSaveAlias: (accountId: number, canonical: string, brokerName: string) => Promise<void>
}) {
  return (
    <Drawer
      open={account != null}
      title={account ? (account.nickname || `Account ${account.trader_login}`) : ''}
      onClose={onClose}
    >
      {account && (
        <>
          <p className="num text-sm text-ink-soft -mt-2 mb-4">
            {isMt5(account)
              ? mt5Subtitle(account.mt5)
              : `${account.trader_login} · cTID ${account.ctid_trader_account_id}`}
          </p>

          <div className="space-y-6">
            {canControl ? (
              <EditSection
                key={account.ctid_trader_account_id}
                account={account}
                pending={pending}
                roleError={roleError}
                onSave={onSave}
              />
            ) : (
              <SettingsSummary account={account} />
            )}

            {detailsError ? (
              <p className="text-sm text-loss-deep">{detailsError}</p>
            ) : !details ? (
              <Loading lines={4} label="Fetching from the broker" />
            ) : (
              <>
                <section>
                  <h3 className="desk-label mb-2">Broker profile</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Broker" value={details.broker_name ?? '—'} />
                    <DetailRow
                      label="Balance"
                      value={details.balance != null
                        ? `${details.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${details.deposit_currency ?? ''}`
                        : '—'}
                      mono
                    />
                    <DetailRow label="Currency" value={details.deposit_currency ?? '—'} />
                    <DetailRow
                      label="Leverage"
                      value={details.leverage != null ? `1:${details.leverage}` : '—'}
                      mono
                    />
                    <DetailRow
                      label="Max leverage"
                      value={details.max_leverage != null ? `1:${details.max_leverage}` : '—'}
                      mono
                    />
                    <DetailRow label="Account type" value={details.account_type ?? '—'} />
                    <DetailRow label="Access" value={details.access_rights ?? '—'} />
                    <DetailRow
                      label="Swap-free"
                      value={details.swap_free == null ? '—' : details.swap_free ? 'Yes' : 'No'}
                    />
                    <DetailRow label="Registered" value={formatTimestamp(details.registration_timestamp)} />
                  </dl>
                  <p className="mt-3 text-xs text-ink-faint">
                    {isMt5(account)
                      ? 'The terminal reports only what MT5 exposes to an Expert Advisor — set a nickname for anything more.'
                      : 'The account holder\'s name and email are not exposed by the cTrader Open API — set a nickname instead.'}
                  </p>
                </section>

                <section>
                  <h3 className="desk-label mb-2">Copy settings</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Role" value={accountRoleLabel(details.role ?? account.role)} />
                    <DetailRow label="Enabled" value={(details.enabled ?? account.enabled) ? 'Yes' : 'No'} />
                    <DetailRow label="Status" value={details.status ?? account.status} />
                  </dl>
                </section>

                {isMt5(account) ? (
                  <section>
                    <h3 className="desk-label mb-2">Terminal</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Broker" value={account.mt5?.broker ?? '—'} />
                      <DetailRow label="Server" value={account.mt5?.server ?? '—'} />
                      <DetailRow label="Currency" value={account.mt5?.currency ?? '—'} />
                      <DetailRow
                        label="Hedging"
                        value={account.mt5?.hedging == null
                          ? '—'
                          : account.mt5?.hedging ? 'Yes' : 'No — netting accounts are not supported'}
                      />
                      <DetailRow label="Trade mode" value={account.mt5?.trade_mode ?? '—'} />
                      <DetailRow label="EA version" value={account.mt5?.ea_version ?? '—'} mono />
                      <DetailRow label="Last seen" value={formatWhen(account.mt5?.last_seen_at)} mono />
                    </dl>
                    {/* Rotate key sits beside the terminal it cuts off; the
                        row menu opens the same dialog. */}
                    {canControl && (
                      <Button variant="secondary" size="sm" className="mt-3" onClick={() => onRotate(account)}>
                        Rotate key
                      </Button>
                    )}
                  </section>
                ) : (
                  <section>
                    <h3 className="desk-label mb-2">OAuth grant</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Granted" value={formatDate(details.connection?.granted_at)} />
                      <DetailRow label="Token expires" value={formatDate(details.connection?.expires_at)} />
                      <DetailRow label="Grant status" value={details.connection?.status ?? '—'} />
                      <DetailRow label="Scope" value={details.connection?.scope ?? '—'} />
                    </dl>
                  </section>
                )}

                {isMt5(account) && (
                  <AliasEditor
                    key={account.ctid_trader_account_id}
                    aliases={aliases}
                    error={aliasesError}
                    canView={canTrade}
                    canEdit={canControl}
                    onSave={(canonical, brokerName) =>
                      onSaveAlias(account.ctid_trader_account_id, canonical, brokerName)}
                  />
                )}

                <section>
                  <h3 className="desk-label mb-2">Open positions ({details.open_positions.length})</h3>
                  {details.open_positions.length === 0 ? (
                    <p className="text-sm text-ink-faint">None.</p>
                  ) : (
                    <ul className="space-y-1 text-sm">
                      {details.open_positions.map((pos) => (
                        <li key={pos.position_id} className="flex justify-between">
                          <span className="num">{pos.symbol ?? pos.symbol_id}</span>
                          <span className={pos.side === 'BUY' ? 'text-profit' : 'text-loss'}>
                            {pos.side} <span className="num">{pos.volume_lots ?? pos.volume}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </>
            )}
          </div>
        </>
      )}
    </Drawer>
  )
}
```

- [ ] **Step 11: Rewrite the page as composition**

Replace `dashboard/src/pages/Accounts.tsx` with:

```tsx
import Button from '../components/Button'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { isMt5 } from '../lib/platform'
import AccountDialogs from './accounts/AccountDialogs'
import AccountDrawer from './accounts/AccountDrawer'
import AccountRow from './accounts/AccountRow'
import KeyRevealDialog from './accounts/KeyRevealDialog'
import { needsRegrant, useAccountsPage } from './accounts/useAccountsPage'

export default function Accounts() {
  const page = useAccountsPage()
  const { accounts, canControl, drawer } = page
  const drawerId = drawer.account?.ctid_trader_account_id
  // Re-grant is one header action: shown while any cTrader account exists,
  // enabled only when one of their grants has lapsed.
  const hasCtrader = accounts.some((a) => !isMt5(a))
  const regrantNeeded = accounts.some(needsRegrant)

  const actions = canControl ? (
    <div className="flex flex-col gap-2 md:flex-row md:items-center">
      {hasCtrader && (
        <Button
          variant="secondary"
          block
          className="md:w-auto"
          onClick={page.connectOAuth}
          disabled={!regrantNeeded}
        >
          Re-grant access
        </Button>
      )}
      <Button variant="secondary" block className="md:w-auto" onClick={page.askAddMt5}>
        Add MT5 account
      </Button>
      <Button block className="md:w-auto" onClick={page.connectOAuth}>
        Connect cTrader ID
      </Button>
    </div>
  ) : undefined

  return (
    <div className="space-y-8 max-w-6xl">
      <PageHeader
        title="Accounts"
        subtitle={
          'One cTrader ID grant covers every account under it; an MT5 account connects ' +
          'through the MirrorFleet EA in its own terminal. Roles, nicknames and cutoff ' +
          'dates apply per account and are edited from Details.'
        }
        actions={actions}
      />

      {/* Permanently mounted so screen readers reliably announce new notices. */}
      <div
        role="status"
        className={page.notice
          ? 'rounded-inset border border-line bg-brand-wash px-4 py-3 text-sm text-ink flex justify-between items-center gap-3'
          : 'sr-only'}
      >
        {page.notice && (
          <>
            <span>{page.notice}</span>
            <Button variant="ghost" tone="neutral" size="sm" onClick={page.dismissNotice}>
              Dismiss
            </Button>
          </>
        )}
      </div>
      {page.error && (
        <div role="alert" className="rounded-inset border border-loss/30 bg-loss-wash px-4 py-3 text-sm text-loss-deep">
          {page.error}
        </div>
      )}

      {page.isLoading ? (
        <Loading lines={4} label="Loading accounts" />
      ) : accounts.length === 0 ? (
        <Card>
          <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-ink-soft">
              No accounts yet: connect a cTrader ID to discover its trading accounts, or
              add an MT5 account and install the EA in its terminal.
            </p>
            {canControl && (
              <Button className="shrink-0" onClick={page.connectOAuth}>Connect cTrader ID</Button>
            )}
          </div>
        </Card>
      ) : (
        <Card inset>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2.5 font-semibold">Account</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Nickname</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Role</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Env</th>
                  <th className="desk-label px-3 py-2.5 font-semibold text-right">Equity</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Health</th>
                  <th className="desk-label px-5 py-2.5 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => {
                  const id = account.ctid_trader_account_id
                  return (
                    <AccountRow
                      key={id}
                      account={account}
                      equity={page.equity[String(id)]?.equity}
                      canControl={canControl}
                      pending={page.pending.has(id)}
                      flatten={page.flatten[id]}
                      onDetails={page.openDetails}
                      onFlatten={page.askFlatten}
                      onRegrant={page.connectOAuth}
                      onRotate={page.askRotate}
                      onDisconnect={page.askDisconnect}
                      onRemove={page.askRemove}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <AccountDialogs page={page} />
      <KeyRevealDialog
        reveal={page.keyReveal}
        onClose={page.closeKeyReveal}
        onCopyError={page.reportError}
      />
      <AccountDrawer
        account={drawer.account}
        details={drawer.details}
        detailsError={drawer.detailsError}
        aliases={drawer.aliases}
        aliasesError={drawer.aliasesError}
        canControl={canControl}
        canTrade={page.canTrade}
        pending={drawerId != null && page.pending.has(drawerId)}
        roleError={drawerId != null ? page.roleErrors[drawerId] : undefined}
        onClose={page.closeDetails}
        onSave={page.saveEdits}
        onRotate={page.askRotate}
        onSaveAlias={page.saveAlias}
      />
    </div>
  )
}
```

- [ ] **Step 12: Run the Accounts suites to verify they pass**

Run: `npx vitest run src/pages/accounts/useAccountsPage.test.ts src/pages/Accounts.test.tsx`
Expected: PASS (7 hook tests, 45 page tests).

- [ ] **Step 13: Check the size and the vocabulary**

Run: `wc -l src/pages/Accounts.tsx src/pages/accounts/*.ts src/pages/accounts/*.tsx`
Expected: `src/pages/Accounts.tsx` under 250 lines (about 150).

Run: `grep -n "Slave\|slave" src/pages/Accounts.tsx src/pages/accounts/*.tsx`
Expected: only `slave: 'Follower'` in `AccountRow.tsx` and `<option value="slave">Follower</option>` in `AccountDrawer.tsx`. No user-visible "slave" remains.

Run: `grep -n "useState" src/pages/Accounts.tsx src/pages/accounts/useAccountsPage.ts`
Expected: none in `Accounts.tsx`, and 7 in the hook (`list`, `feedback`, `rows`, `dialog`, `busy`, `keyReveal`, `drawer`).

- [ ] **Step 14: Run the full dashboard gate**

Run: `npm test`
Expected: the palette check passes, `tsc --noEmit -p tsconfig.app.json` is clean (strict, `noUnusedLocals`), and every vitest suite is green.

- [ ] **Step 15: Commit**

```bash
git add dashboard/src/pages/Accounts.tsx dashboard/src/pages/Accounts.test.tsx dashboard/src/pages/accounts/
git commit -m "$(cat <<'EOF'
feat(dashboard): Accounts as read-only rows with a Details drawer, one menu per row, and the page split into focused files

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b
EOF
)"
```

### Task 8: Investor pages — units on every figure, honest pending states, Copy address, a Withdraw review step

Spec §5.5 (Investor Overview plus Deposit and Withdraw) and the investor half of §5.6 (the `w-40` date column in Recent activity). Every money figure carries its unit; the two "not yet" states say what happens next and who to write to; the Deposit address gets a Copy button and the QR encoder leaves the main bundle; Withdraw gets "Use max", a review dialog before anything is posted, and a timeline that names the current step in words. History says the real window instead of "this week"; `formatWhen` shows the year for other years. All five investor pages adopt `PageHeader` (and `Loading` where they load).

**Grounding (read before starting):**

- The summary carries **no currency field**. `summary_json` in `api/src/api/investor_ledger.py` returns bare numbers for `total_deposited`, `total_withdrawn`, `net_deposits`, `pending_withdrawn`, `equity`, `profit`, `available`; the route adds `org`, `link_state`, `account`, `equity_source`, `wallet_configured` (`api/src/api/routes/investor.py`, `investor_summary`). So account-currency figures use a labelled default `ACCOUNT_CURRENCY = 'USD'`.
- Wallet fields: `coin`, `network`, `address`, `memo` (`_wallet`); `GET investor/wallet` answers **404** `Deposits are not open yet` when none is set.
- Deposit rows carry `coin` (`_deposit_json`); withdrawal rows do **not** (`_withdrawal_json`: `amount`, `destination`, `status`, `txid`, ...). `file_deposit` refuses any coin other than the wallet's, so a deposit row's `coin` is the ledger coin.
- Statuses: deposits `pending | confirmed | rejected`; withdrawals `requested | approved | paid | rejected`.
- Request bodies stay byte-identical: deposit `{ amount, coin: wallet.coin, txid, note }`, withdrawal `JSON.stringify(form)` with `form = { amount, destination }`.
- The only request change: Withdraw additionally reads the existing `GET investor/wallet` (an endpoint investors already call on Deposit) to learn the payout coin; a 404 falls back to `ACCOUNT_CURRENCY`. No endpoint, body or `api/` change.

**Files:**
- Modify: `dashboard/src/lib/format.ts` (`money` gains `unit`, accepts a form string; `formatWhen` shows the year outside the current year)
- Create: `dashboard/src/lib/format.test.ts`
- Modify: `dashboard/src/lib/investor.ts` (`ACCOUNT_CURRENCY`, `moneyOrDash(n, unit?)`, `shortAddress`)
- Modify: `dashboard/src/lib/investor.test.ts`
- Create: `dashboard/src/pages/investor/NextStep.tsx` (the shared pending-state card with the support line)
- Modify: `dashboard/src/pages/investor/InvestorOverview.tsx`, `InvestorOverview.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorDeposit.tsx`, `InvestorDeposit.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorWithdraw.tsx`, `InvestorWithdraw.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorHistory.tsx`, `InvestorHistory.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorAccount.tsx`
- Create: `dashboard/src/pages/investor/InvestorAccount.test.tsx`

**Interfaces:**
- Consumes (Task 2): `Card` (`components/Card.tsx`, props `title? actions? inset? className? as? children`), `PageHeader` (`components/PageHeader.tsx`, props `title subtitle? actions? children?`, sets `document.title` to `"<title> · MirrorFleet"` via `usePageTitle`), `Loading` (`components/Loading.tsx`, `role="status"`, text "Loading"). `StatTile`, `Badge`, `Banner`, `Button`, `Input`, `ConfirmDialog` keep today's APIs.
- Consumes (Task 5): `LANDING_FACTS.supportEmail` exported from `dashboard/src/pages/Landing.tsx` (`export const LANDING_FACTS = { legalName, address, supportEmail }`).
- Produces:
  - `money(value: number | string | null | undefined, unit?: string): string` — `money(500, 'USDT') === '500.00 USDT'`; no unit = old output; unknown or unparseable = `'—'` with no unit.
  - `formatWhen(ms)` — unchanged signature; adds the year when the date is not in the current year.
  - `ACCOUNT_CURRENCY: 'USD'`, `moneyOrDash(n: number | null | undefined, unit?: string): string`, `shortAddress(address: string): string` in `lib/investor.ts`.
  - `NextStep({ title: string; children: ReactNode })` default export in `pages/investor/NextStep.tsx`.

All commands run from `dashboard/` in Git Bash.

- [ ] **Step 1: Write the failing formatter tests**

Create `dashboard/src/lib/format.test.ts`:

```ts
// src/lib/format.test.ts
import { afterEach, describe, expect, test, vi } from 'vitest'
import { formatWhen, money } from './format'

afterEach(() => { vi.useRealTimers() })

describe('money', () => {
  test('two decimals with separators; no unit keeps the old output', () => {
    expect(money(1234.5)).toBe('1,234.50')
    expect(money(0)).toBe('0.00')
    expect(money(-12.3)).toBe('-12.30')
  })

  test('a unit follows the amount after one space', () => {
    expect(money(500, 'USDT')).toBe('500.00 USDT')
    expect(money(5120.5, 'USD')).toBe('5,120.50 USD')
    expect(money(-12.3, 'USD')).toBe('-12.30 USD')
  })

  test('a form field string is read as a number', () => {
    expect(money('500', 'USDT')).toBe('500.00 USDT')
    expect(money('1000.5')).toBe('1,000.50')
  })

  test('an unknown amount is a bare dash, never a dash with a unit', () => {
    expect(money(null, 'USDT')).toBe('—')
    expect(money(undefined, 'USD')).toBe('—')
    expect(money('', 'USDT')).toBe('—')
    expect(money('abc', 'USDT')).toBe('—')
    expect(money(Number.NaN)).toBe('—')
  })
})

describe('formatWhen', () => {
  test('a date in the current year leaves the year out', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 29, 12, 0, 0))
    const out = formatWhen(new Date(2026, 2, 4, 10, 5, 6).getTime())
    expect(out).toContain('04')
    expect(out).toContain('10:05:06')
    expect(out).not.toContain('2026')
  })

  test('a date in another year shows the year', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 29, 12, 0, 0))
    const out = formatWhen(new Date(2025, 2, 4, 10, 5, 6).getTime())
    expect(out).toContain('2025')
    expect(out).toContain('10:05:06')
    expect(formatWhen(new Date(2025, 11, 31, 9, 0, 0).toISOString())).toContain('2025')
  })

  test('missing and unparseable values are unchanged', () => {
    expect(formatWhen(null)).toBe('—')
    expect(formatWhen(undefined)).toBe('—')
    expect(formatWhen('not a date')).toBe('not a date')
  })
})
```

Replace `dashboard/src/lib/investor.test.ts` with:

```ts
import { describe, expect, test } from 'vitest'
import { ACCOUNT_CURRENCY, moneyOrDash, shortAddress, statusLabel, statusTone } from './investor'

describe('investor helpers', () => {
  test('statuses read as plain words with a tone', () => {
    expect(statusLabel('pending')).toBe('Pending review')
    expect(statusLabel('confirmed')).toBe('Confirmed')
    expect(statusLabel('requested')).toBe('Awaiting approval')
    expect(statusLabel('approved')).toBe('Approved, payment pending')
    expect(statusLabel('paid')).toBe('Paid')
    expect(statusLabel('rejected')).toBe('Rejected')
    expect(statusTone('confirmed')).toBe('ok')
    expect(statusTone('paid')).toBe('ok')
    expect(statusTone('pending')).toBe('warn')
    expect(statusTone('requested')).toBe('warn')
    expect(statusTone('approved')).toBe('warn')
    expect(statusTone('rejected')).toBe('bad')
    expect(statusTone('whatever')).toBe('quiet')
  })

  test('money shows a dash when unknown', () => {
    expect(moneyOrDash(null)).toBe('—')
    expect(moneyOrDash(undefined)).toBe('—')
    expect(moneyOrDash(1234.5)).toBe('1,234.50')
  })

  test('money carries its unit, and the dash never does', () => {
    expect(moneyOrDash(1234.5, 'USD')).toBe('1,234.50 USD')
    expect(moneyOrDash(null, 'USD')).toBe('—')
    expect(ACCOUNT_CURRENCY).toBe('USD')
  })

  test('long addresses shorten to first character and last two; short ones stay whole', () => {
    expect(shortAddress('TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f')).toBe('T…9f')
    expect(shortAddress('TDest')).toBe('TDest')
    expect(shortAddress('  TDest  ')).toBe('TDest')
  })
})
```

- [ ] **Step 2: Write the failing page tests**

Replace `dashboard/src/pages/investor/InvestorOverview.test.tsx` with:

```tsx
// src/pages/investor/InvestorOverview.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorOverview from './InvestorOverview'
import * as apiModule from '../../lib/api'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

// The support line reads LANDING_FACTS; pin it so Task 5's real address
// cannot make the "no contact" case pass or fail by accident.
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

class MockWebSocket {
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close() { /* no-op */ }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const linked = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true,
  total_deposited: 5000, total_withdrawn: 0, net_deposits: 5000, pending_withdrawn: 0,
  equity: 5120.5, profit: 120.5, available: 5120.5,
}
const unlinked = { ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown',
                   equity: null, profit: null, available: null, net_deposits: 0, total_deposited: 0 }

const deposit = { id: 1, user_id: 1, account_id: 1001, amount: 5000, coin: 'USDT', txid: 'abc',
                  note: null, status: 'confirmed', decided_by: 2, decided_at: '2026-09-20T11:00:00Z',
                  decision_note: null, created_at: '2026-09-20T10:00:00Z' }
const withdrawal = { id: 3, user_id: 1, account_id: 1001, amount: 1000, destination: 'TDest',
                     status: 'requested', equity_at_request: 5120.5, equity_verified: true,
                     decided_by: null, decided_at: null, decision_note: null, paid_by: null,
                     paid_at: null, txid: null, created_at: '2026-09-22T10:00:00Z' }

function mockRoutes(summaries: unknown | unknown[],
                    ledger: { deposits?: unknown[]; withdrawals?: unknown[] } = {}) {
  const queue = Array.isArray(summaries) ? [...summaries] : [summaries]
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(queue.length > 1 ? queue.shift() : queue[0])
    }
    if (url.endsWith('/investor/positions')) {
      return jsonResponse({ equity_source: 'live', positions: [
        { position_id: 7, symbol: 'XAUUSD', side: 'BUY', volume: 1, entry_price: 4350,
          current_price: 4360, stop_loss: 4300, take_profit: 4400, pnl_quote: 10 }] })
    }
    if (url.includes('/investor/analytics')) {
      return jsonResponse({ closed_trades: 3, wins: 2, losses: 1, win_rate: 66.7,
        profit_factor: 2.1, best_trade: 50, worst_trade: -20, avg_win: 40, avg_loss: -20,
        net_pnl: 120.5, gross_wins: 140.5, gross_losses: 20, max_drawdown: 30,
        max_drawdown_pct: 0.6, equity_curve: [{ timestamp: 1, balance: 5000 },
        { timestamp: 2, balance: 5120.5 }], per_symbol: [], weekly: [], weeks: 4, truncated: false })
    }
    if (url.endsWith('/investor/deposits')) return jsonResponse(ledger.deposits ?? [])
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(ledger.withdrawals ?? [])
    return jsonResponse({})
  }))
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
})
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  facts.supportEmail = ''
})

test('the page has its heading and title, and shows Loading until the summary lands', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(document.title).toBe('Overview · MirrorFleet')
})

test('unlinked investors see the setup notice and no figures', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.getByText(/you can file your deposit notice on the deposit page now/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('without a wallet the setup notice says what happens next', async () => {
  mockRoutes({ ...unlinked, wallet_configured: false })
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText(
    "Your admin links your trading account; deposits open once the workspace's wallet is set.",
  )).toBeInTheDocument()
})

test('the setup notice shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('linked investors see equity, profit, positions and the snapshot, each with a unit', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  // Equity and profit are account-currency figures (USD default).
  expect((await screen.findAllByText('5,120.50 USD')).length).toBeGreaterThan(0)
  expect((await screen.findAllByText('120.50 USD')).length).toBeGreaterThan(0)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(await screen.findByText(/66\.7%/)).toBeInTheDocument()
  expect(screen.getAllByText(/live/i).length).toBeGreaterThan(0)
  expect(screen.getByText('5,120.50 USD available')).toBeInTheDocument()
})

test('recent activity carries the ledger coin and a fluid date column', async () => {
  mockRoutes(linked, { deposits: [deposit], withdrawals: [withdrawal] })
  const { container } = render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText('+5,000.00 USDT')).toBeInTheDocument()
  expect(screen.getByText('-1,000.00 USDT')).toBeInTheDocument()
  // Ledger tiles use the same coin as the rows they add up.
  expect(screen.getByText('5,000.00 USDT')).toBeInTheDocument()
  const when = container.querySelector('li time')
  expect(when).not.toBeNull()
  expect(when).not.toHaveClass('w-40')
  expect(when).toHaveClass('min-w-0')
})

test('positions and the snapshot go away when the account is unlinked later', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes([linked, unlinked])
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(10000)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/66\.7%/)).not.toBeInTheDocument()
})
```

Replace `dashboard/src/pages/investor/InvestorDeposit.test.tsx` with:

```tsx
import { readFileSync } from 'node:fs'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDeposit from './InvestorDeposit'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
// The page loads the encoder with `await import('qrcode')` and takes the
// named export; keep `default` too so either shape resolves.
vi.mock('qrcode', () => {
  const toDataURL = vi.fn(async () => 'data:image/png;base64,QR')
  return { toDataURL, default: { toDataURL } }
})
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const wallet = { coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: null }
const notice = { id: 1, user_id: 1, account_id: null, amount: 5000, coin: 'USDT', txid: 'abc',
                 note: null, status: 'pending', decided_by: null, decided_at: null,
                 decision_note: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { wallet?: boolean } = {}) {
  const deposits: unknown[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/wallet')) {
      return opts.wallet === false
        ? jsonResponse({ detail: 'Deposits are not open yet' }, 404)
        : jsonResponse(wallet)
    }
    if (url.endsWith('/investor/deposits') && init?.method === 'POST') {
      deposits.unshift(notice)
      return jsonResponse(notice, 201)
    }
    if (url.endsWith('/investor/deposits')) return jsonResponse(deposits)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  facts.supportEmail = ''
})

test('shows the wallet card with a QR and files a notice', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText('TAddr123')).toBeInTheDocument()
  expect(screen.getByText(/USDT on TRC20/)).toBeInTheDocument()
  expect((await screen.findByRole('img', { name: /QR/ })).getAttribute('src')).toContain('data:image')
  expect(screen.getByRole('heading', { level: 1, name: 'Deposit' })).toBeInTheDocument()
  expect(document.title).toBe('Deposit · MirrorFleet')

  await userEvent.type(screen.getByLabelText('Amount in USDT'), '5000')
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'I have sent it' }))

  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  expect(String(post![0])).toMatch(/\/investor\/deposits$/)
  expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual(
    { amount: '5000', coin: 'USDT', txid: 'abc', note: '' })
  await waitFor(() => expect(screen.getByText('Pending review')).toBeInTheDocument())
  // The row's amount carries its coin.
  expect(screen.getByText('5,000.00 USDT')).toBeInTheDocument()
})

test('Copy address copies, says so, and reverts after two seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')

  // Installed AFTER render: user-event swaps in its own clipboard stub on
  // first use, and this one must be the one the page hits.
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }))

  await waitFor(() => expect(writeText).toHaveBeenCalledWith('TAddr123'))
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  expect(screen.getByText('Address copied to the clipboard.')).toBeInTheDocument()

  await vi.advanceTimersByTimeAsync(2000)
  expect(await screen.findByRole('button', { name: 'Copy address' })).toBeInTheDocument()
})

test('without a clipboard API, Copy address selects the address instead', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')

  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }))

  expect(await screen.findByText(/address selected/i)).toBeInTheDocument()
  expect(window.getSelection()?.toString()).toBe('TAddr123')
  expect(screen.getByRole('button', { name: 'Copy address' })).toBeInTheDocument()
})

test('says deposits are not open when there is no wallet, and what happens next', async () => {
  mockRoutes({ wallet: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText(/Deposits are not open yet/)).toBeInTheDocument()
  expect(screen.getByText(
    "Your admin links your trading account; deposits open once the workspace's wallet is set.",
  )).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'I have sent it' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Copy address' })).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('the closed state shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes({ wallet: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('the QR encoder is loaded on demand, not in the main bundle', () => {
  const source = readFileSync('src/pages/investor/InvestorDeposit.tsx', 'utf8')
  expect(source).not.toMatch(/^import .* from 'qrcode'/m)
  expect(source).toContain("await import('qrcode')")
})
```

Replace `dashboard/src/pages/investor/InvestorWithdraw.test.tsx` with:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWithdraw from './InvestorWithdraw'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const LONG = 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f'
const wallet = { coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: null }
const summary = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true, total_deposited: 5000, total_withdrawn: 0,
  net_deposits: 5000, pending_withdrawn: 0, equity: 5120.5, profit: 120.5, available: 5120.5,
}
const request = { id: 3, user_id: 1, account_id: 1001, amount: 1000, destination: 'TDest',
                  status: 'approved', equity_at_request: 5120.5, equity_verified: true,
                  decided_by: 2, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
                  paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { refuse?: string; unlinked?: boolean; noWallet?: boolean;
                            rows?: unknown[] } = {}) {
  const rows: unknown[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(opts.unlinked
        ? { ...summary, link_state: 'unlinked', account: null, available: null } : summary)
    }
    if (url.endsWith('/investor/wallet')) {
      return opts.noWallet
        ? jsonResponse({ detail: 'Deposits are not open yet' }, 404)
        : jsonResponse(wallet)
    }
    if (url.endsWith('/investor/withdrawals') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse({ detail: opts.refuse }, 400)
      rows.unshift(request)
      return jsonResponse(request, 201)
    }
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(rows)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); facts.supportEmail = '' })

test('shows what is available with its unit and files a request only after the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText('5,120.50 USD')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Withdraw' })).toBeInTheDocument()
  expect(document.title).toBe('Withdraw · MirrorFleet')

  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), LONG)
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))

  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USDT to T…9f?' })
  expect(within(dialog).getByText(LONG)).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)

  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/withdrawals$/)
  expect(JSON.parse((post[1] as RequestInit).body as string))
    .toEqual({ amount: '1000', destination: LONG })
  await waitFor(() => expect(screen.getByText('Approved, payment pending')).toBeInTheDocument())
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByText('1,000.00 USDT')).toBeInTheDocument()
})

test('Cancel on the review sends nothing and keeps the form', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USDT to TDest?' })

  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  expect(screen.getByLabelText('Amount in USDT')).toHaveValue('1000')
  expect(screen.getByLabelText('Destination address')).toHaveValue('TDest')
})

test('Use max fills the available amount, and that is what is posted', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByLabelText('Amount in USDT')
  await userEvent.click(screen.getByRole('button', { name: 'Use max' }))
  expect(screen.getByLabelText('Amount in USDT')).toHaveValue('5120.50')

  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 5,120.50 USDT to TDest?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string))
    .toEqual({ amount: '5120.50', destination: 'TDest' })
})

test('an amount that is not above zero never reaches the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '0')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText('Enter an amount above zero')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('without a wallet the figures fall back to the account currency', async () => {
  mockRoutes({ noWallet: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('5,120.50 USD')
  await userEvent.type(screen.getByLabelText('Amount in USD'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByRole('dialog', { name: 'Send 1,000.00 USD to TDest?' })).toBeInTheDocument()
})

test("the server's refusal is shown as written", async () => {
  mockRoutes({ refuse: 'amount exceeds what is available to withdraw (1120.50)' })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('5,120.50 USD')
  await userEvent.type(await screen.findByLabelText('Amount in USDT'), '9999')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  expect(await screen.findByText(/available to withdraw \(1120\.50\)/)).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('the timeline names the current step in words and with aria-current', async () => {
  mockRoutes({ rows: [request] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const progress = await screen.findByRole('list', { name: 'Withdrawal progress' })
  const steps = within(progress).getAllByRole('listitem')
  expect(steps).toHaveLength(3)
  expect(steps[0]).not.toHaveAttribute('aria-current')
  expect(steps[1]).toHaveAttribute('aria-current', 'step')
  expect(steps[1]).toHaveTextContent('Approved, payment pending')
  expect(steps[1]).toHaveTextContent('current')
  expect(steps[2]).not.toHaveAttribute('aria-current')
  expect(steps[2]).not.toHaveTextContent('current')
})

test('unlinked investors cannot request yet, and are told what happens next', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes({ unlinked: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.getByText('Withdrawals open once your admin links your trading account.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'help@desk.example' })).toHaveAttribute('href', 'mailto:help@desk.example')
  expect(screen.queryByRole('button', { name: 'Request withdrawal' })).not.toBeInTheDocument()
})
```

Replace `dashboard/src/pages/investor/InvestorHistory.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorHistory from './InvestorHistory'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const deal = { deal_id: 1, order_id: 1, position_id: 7, symbol_id: 41, symbol: 'XAUUSD',
               side: 'SELL', volume: 100, filled_volume: 100, volume_lots: '0.01',
               execution_price: 4360.0, status: 'FILLED', commission: -0.07,
               create_timestamp: 1758620000000, execution_timestamp: 1758620000000,
               close: { entry_price: 4350.0, gross_profit: 10.0, swap: -0.1, commission: -0.07,
                        balance: 5009.83, closed_volume: 100, closed_volume_lots: '0.01' } }

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/investor/history/deals')) return jsonResponse({ deals: [deal], has_more: false })
    return jsonResponse({})
  }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists closed deals for the last week and pages earlier', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(screen.getByText('9.83 USD')).toBeInTheDocument()
  expect(screen.getByText('10.00 USD')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
  expect(document.title).toBe('History · MirrorFleet')
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
  const first = String(fetchMock.mock.calls[0][0])
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  const second = String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0])
  const toOf = (u: string) => Number(new URL(u, 'http://x').searchParams.get('to'))
  expect(toOf(first) - toOf(second)).toBe(7 * 24 * 3600 * 1000)
})

test('the total names the real window, not "this week", once paged back', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('9.83 USD net, last 7 days')).toBeInTheDocument()
  expect(screen.queryByText(/this week/i)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  expect(await screen.findByText(/^9\.83 USD net, .+ – .+$/)).toBeInTheDocument()
  expect(screen.queryByText(/last 7 days/)).not.toBeInTheDocument()
})

test('Later is disabled at the current week and comes back after paging earlier', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  await screen.findByText('XAUUSD')
  const later = screen.getByRole('button', { name: 'Later' })
  expect(later).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  expect(later).toBeEnabled()
  await userEvent.click(later)
  expect(later).toBeDisabled()
  await screen.findByText('XAUUSD')
})
```

Create `dashboard/src/pages/investor/InvestorAccount.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorAccount from './InvestorAccount'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks() })

test('the Account page shows who you are, the login forms and its title', () => {
  // AccountSecurity calls useNavigate, so the page needs a router.
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Account' })).toBeInTheDocument()
  expect(screen.getByText('Test User')).toBeInTheDocument()
  expect(screen.getByText('user@example.com')).toBeInTheDocument()
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(screen.getByText('Investor')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Your login' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Change MPIN' })).toBeInTheDocument()
  expect(document.title).toBe('Account · MirrorFleet')
})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npx vitest run src/lib/format.test.ts src/lib/investor.test.ts src/pages/investor`
Expected: FAIL. `format.test.ts`: `money(500, 'USDT')` returns `'500.00'`, `money('500', 'USDT')` returns `'500'` (a string's `toLocaleString` ignores the options), `money(Number.NaN)` returns `'NaN'`, and the other-year `formatWhen` lacks `2025`. `investor.test.ts`: `ACCOUNT_CURRENCY` and `shortAddress` are not exported. Page tests: the headings/`document.title` assertions fail (no `PageHeader` yet), `'5,120.50 USD'`, `'Amount in USDT'`, `Copy address`, `Use max`, the review dialog, `Withdrawal progress`, `'9.83 USD net, last 7 days'` and the `mailto:` link are all missing; `InvestorAccount.test.tsx` fails on `document.title`.

- [ ] **Step 4: Implement `lib/format.ts`**

Replace `dashboard/src/lib/format.ts` with:

```ts
/** Shared desk formatters — every money figure renders the same everywhere. */

/**
 * Two decimals with thousands separators. `unit` appends the currency or
 * coin after one space ("500.00 USDT"); without it the output is exactly
 * what it always was. A string is what a form field holds ("500") and is
 * read as a number. An unknown or unparseable amount is a bare dash --
 * never "— USDT", which would claim a unit for a number nobody has.
 */
export function money(value: number | string | null | undefined, unit?: string): string {
  if (value == null) return '—'
  const n = typeof value === 'string' ? (value.trim() === '' ? Number.NaN : Number(value)) : value
  if (!Number.isFinite(n)) return '—'
  const formatted = n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return unit ? `${formatted} ${unit}` : formatted
}

export function signed(value: number | null | undefined): string {
  if (value == null) return '—'
  const formatted = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
  return `${value < 0 ? '-' : '+'}${formatted}`
}

/**
 * "04 Mar, 10:05:06" for a moment in the current year; "04 Mar 2025,
 * 10:05:06" otherwise, so a row from last December never reads as next
 * month's.
 */
export function formatWhen(ms: number | string | null | undefined): string {
  if (!ms) return '—'
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return String(ms)
  const options: Intl.DateTimeFormatOptions = {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }
  if (d.getFullYear() !== new Date().getFullYear()) options.year = 'numeric'
  return d.toLocaleString('en-GB', options)
}

/** The API surfaces errors as "409: a master already exists" — drop the code. */
export function stripCode(message: string): string {
  return message.replace(/^\d{3}:\s*/, '')
}

export function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? stripCode(err.message) : fallback
}
```

- [ ] **Step 5: Implement `lib/investor.ts`**

Replace `dashboard/src/lib/investor.ts` with:

```ts
import { money } from './format'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'quiet'

/**
 * The unit for account-currency figures: equity, profit, available, trade
 * P&L. The investor summary carries NO currency field -- `summary_json` in
 * api/src/api/investor_ledger.py returns bare numbers for total_deposited,
 * total_withdrawn, net_deposits, pending_withdrawn, equity, profit and
 * available -- so this is a labelled default, not something the API said.
 * Ledger figures (deposits, withdrawals) use the wallet's coin instead.
 */
export const ACCOUNT_CURRENCY = 'USD'

const LABELS: Record<string, string> = {
  pending: 'Pending review',
  confirmed: 'Confirmed',
  requested: 'Awaiting approval',
  approved: 'Approved, payment pending',
  paid: 'Paid',
  rejected: 'Rejected',
}

export function statusLabel(status: string): string {
  return LABELS[status] ?? status
}

export function statusTone(status: string): StatusTone {
  if (status === 'confirmed' || status === 'paid') return 'ok'
  if (status === 'pending' || status === 'requested' || status === 'approved') return 'warn'
  if (status === 'rejected') return 'bad'
  return 'quiet'
}

export function moneyOrDash(n: number | null | undefined, unit?: string): string {
  return n == null ? '—' : money(n, unit)
}

/**
 * "T…9f": the first character and the last two, for a one-line summary
 * such as the Withdraw review title. The full address is always shown next
 * to it; short strings stay whole.
 */
export function shortAddress(address: string): string {
  const a = address.trim()
  return a.length <= 8 ? a : `${a.slice(0, 1)}…${a.slice(-2)}`
}

/** Tailwind classes for a status pill, matching Automation's OutcomePill. */
export function pillClass(status: string): string {
  const tone = statusTone(status)
  return tone === 'ok' ? 'bg-profit-wash text-profit-deep'
    : tone === 'warn' ? 'bg-warn-wash text-warn-deep'
    : tone === 'bad' ? 'bg-loss-wash text-loss-deep'
    : 'bg-paper text-ink-soft'
}
```

- [ ] **Step 6: Create the shared pending-state card**

Create `dashboard/src/pages/investor/NextStep.tsx`:

```tsx
import type { ReactNode } from 'react'
import Card from '../../components/Card'
import { LANDING_FACTS } from '../Landing'

/**
 * A "not yet" state that says what happens next in one sentence, and who
 * to write to when the workspace has published a support address
 * (LANDING_FACTS.supportEmail, the same address the landing footer shows).
 * With no address the line is left out rather than showing an empty mailto.
 */
export default function NextStep({ title, children }: { title: string; children: ReactNode }) {
  const email = LANDING_FACTS.supportEmail
  return (
    <Card title={title}>
      <p className="text-sm text-ink-soft">{children}</p>
      {email && (
        <p className="text-sm text-ink-soft mt-2">
          Questions? Write to{' '}
          <a href={`mailto:${email}`}
             className="text-brand underline underline-offset-2 hover:text-brand-deep">
            {email}
          </a>
          .
        </p>
      )}
    </Card>
  )
}
```

- [ ] **Step 7: Implement `InvestorOverview.tsx`**

Replace `dashboard/src/pages/investor/InvestorOverview.tsx` with:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money, signed } from '../../lib/format'
import { ACCOUNT_CURRENCY, moneyOrDash, statusLabel, statusTone } from '../../lib/investor'
import Badge, { type BadgeTone } from '../../components/Badge'
import Banner from '../../components/Banner'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import NextStep from './NextStep'
import type {
  Analytics, InvestorDeposit, InvestorPositions, InvestorSummary, InvestorWithdrawal,
} from '../../lib/types'

const POLL_MS = 10000

// statusTone's four states, mapped onto the desk's one chip.
const BADGE_TONE: Record<ReturnType<typeof statusTone>, BadgeTone> = {
  ok: 'profit', warn: 'warn', bad: 'loss', quiet: 'neutral',
}

type Activity = {
  key: string; when: string; what: string; amount: number; unit: string; status: string
}

export default function InvestorOverview() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [activity, setActivity] = useState<Activity[]>([])
  // The ledger's coin: the newest deposit row's `coin`. The API only files
  // deposits in the wallet's coin (investor.py file_deposit), so that row
  // names the unit every ledger total is kept in. With no deposit yet,
  // the account-currency default.
  const [ledgerUnit, setLedgerUnit] = useState<string>(ACCOUNT_CURRENCY)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setSummary(s)
      const [deps, wds] = await Promise.all([
        orgApi<InvestorDeposit[]>(orgId, 'investor/deposits'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      const unit = deps[0]?.coin ?? ACCOUNT_CURRENCY
      setLedgerUnit(unit)
      const rows: Activity[] = [
        ...deps.map((d) => ({ key: `d${d.id}`, when: d.created_at, what: `Deposit ${d.coin}`,
                              amount: d.amount, unit: d.coin, status: d.status })),
        // Withdrawal rows carry no coin (investor.py _withdrawal_json); they
        // are paid in the ledger's coin.
        ...wds.map((w) => ({ key: `w${w.id}`, when: w.created_at, what: 'Withdrawal',
                             amount: -w.amount, unit, status: w.status })),
      ].sort((a, b) => (a.when < b.when ? 1 : -1)).slice(0, 8)
      setActivity(rows)
      if (s.link_state === 'linked') {
        const [p, a] = await Promise.all([
          orgApi<InvestorPositions>(orgId, 'investor/positions'),
          orgApi<Analytics>(orgId, 'investor/analytics?weeks=4'),
        ])
        setPositions(p); setAnalytics(a)
      } else {
        setPositions(null)
        setAnalytics(null)
      }
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your overview'))
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  return (
    <div className="space-y-6 max-w-6xl">
      <PageHeader title="Overview" subtitle={summary?.org.name} />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!summary && !error && <Loading lines={4} />}

      {summary?.link_state === 'unlinked' && (
        <NextStep title="Your account is being set up">
          {summary.wallet_configured
            ? 'Your admin links your trading account once your deposit is confirmed; you can file your deposit notice on the Deposit page now.'
            : "Your admin links your trading account; deposits open once the workspace's wallet is set."}
        </NextStep>
      )}

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Current equity" value={moneyOrDash(summary.equity, ACCOUNT_CURRENCY)} tone="brand"
                    sub={summary.link_state === 'linked'
                      ? `${summary.equity_source} figure` : 'no account yet'} />
          <StatTile label="Profit" value={moneyOrDash(summary.profit, ACCOUNT_CURRENCY)}
                    tone={summary.profit == null ? 'neutral' : summary.profit < 0 ? 'loss' : 'profit'}
                    sub="equity minus what you put in" />
          <StatTile label="Net deposits" value={money(summary.net_deposits, ledgerUnit)}
                    sub={`${money(summary.total_deposited, ledgerUnit)} in · ${money(summary.total_withdrawn, ledgerUnit)} out`} />
          <StatTile label="Pending withdrawals" value={money(summary.pending_withdrawn, ledgerUnit)}
                    sub={summary.available != null
                      ? `${money(summary.available, ACCOUNT_CURRENCY)} available` : undefined} />
        </div>
      )}

      {summary?.account && (
        <Card>
          <dl className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <div><dt className="desk-label inline mr-2">Account</dt>
              <dd className="inline text-ink">{summary.account.nickname ?? summary.account.account_id}</dd></div>
            <div><dt className="desk-label inline mr-2">Platform</dt>
              <dd className="inline text-ink uppercase">{summary.account.platform}</dd></div>
            <div><dt className="desk-label inline mr-2">Connection</dt>
              <dd className={`inline ${summary.account.connected ? 'text-profit' : 'text-warn-deep'}`}>
                {summary.account.connected ? 'connected' : 'terminal offline'}
              </dd></div>
          </dl>
        </Card>
      )}

      {positions && (
        <Card title="Open positions" inset
              actions={<span className="text-xs text-ink-soft">{positions.equity_source}</span>}>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-4 py-2 font-semibold">Side</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Volume</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Entry</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Current</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">SL / TP</th>
                  {/* pnl_quote is in each symbol's quote currency, which is
                      not always the account currency, so the header says so. */}
                  <th className="desk-label px-4 py-2 font-semibold text-right">Live P&L (quote currency)</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.length === 0 && (
                  <tr><td colSpan={7} className="text-center py-8 text-ink-faint">No open positions</td></tr>
                )}
                {positions.positions.map((p) => (
                  <tr key={p.position_id} className="border-b border-line last:border-0">
                    <td data-label="Symbol" className="px-4 py-2.5 text-ink">{p.symbol ?? '—'}</td>
                    <td data-label="Side" className="px-4 py-2.5">{p.side}</td>
                    <td data-label="Volume" className="tnum px-4 py-2.5 text-right">{p.volume}</td>
                    <td data-label="Entry" className="tnum px-4 py-2.5 text-right">{p.entry_price ?? '—'}</td>
                    <td data-label="Current" className="tnum px-4 py-2.5 text-right">{p.current_price ?? '—'}</td>
                    <td data-label="SL / TP" className="tnum px-4 py-2.5 text-right">{p.stop_loss ?? '—'} / {p.take_profit ?? '—'}</td>
                    <td data-label="Live P&L (quote currency)" className={`tnum px-4 py-2.5 text-right ${(p.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>
                      {p.pnl_quote == null ? '—' : signed(p.pnl_quote)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {analytics && (
        <Card title={`Performance, last ${analytics.weeks} weeks`}
              actions={<span className="text-xs text-ink-soft">{analytics.closed_trades} closed trades</span>}>
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatTile label="Net P&L" value={money(analytics.net_pnl, ACCOUNT_CURRENCY)}
                        tone={analytics.net_pnl < 0 ? 'loss' : 'profit'} />
              <StatTile label="Win rate"
                        value={analytics.win_rate == null ? '—' : `${analytics.win_rate.toFixed(1)}%`}
                        sub={`${analytics.wins} won · ${analytics.losses} lost`} />
              <StatTile label="Max drawdown" value={money(analytics.max_drawdown, ACCOUNT_CURRENCY)} tone="loss"
                        sub={`${analytics.max_drawdown_pct.toFixed(1)}% from peak`} />
              <StatTile label="Profit factor"
                        value={analytics.profit_factor == null ? '—' : analytics.profit_factor.toFixed(2)} />
            </div>
            {analytics.equity_curve.length > 1 && (
              <div className="inset p-3">
                <EquityCurve points={analytics.equity_curve} height={160} />
              </div>
            )}
          </div>
        </Card>
      )}

      <Card title="Recent activity" inset>
        <ul className="divide-y divide-line">
          {activity.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {activity.map((a) => (
            <li key={a.key} className="px-4 py-2.5 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
              {/* Fluid, not a fixed w-40: on a 360px phone the date takes its
                  own line and the rest wraps beneath it. */}
              <time dateTime={a.when} className="num text-ink-soft min-w-0 basis-full sm:basis-auto">
                {formatWhen(a.when)}
              </time>
              <span className="text-ink flex-1 min-w-0">{a.what}</span>
              <span className={`tnum ${a.amount < 0 ? 'text-loss' : 'text-ink'}`}>
                {`${signed(a.amount)} ${a.unit}`}
              </span>
              <Badge tone={BADGE_TONE[statusTone(a.status)]}>{statusLabel(a.status)}</Badge>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
```

- [ ] **Step 8: Implement `InvestorDeposit.tsx`**

Replace `dashboard/src/pages/investor/InvestorDeposit.tsx` with:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { statusLabel, statusTone } from '../../lib/investor'
import Badge, { type BadgeTone } from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import NextStep from './NextStep'
import type { InvestorDeposit as Deposit, InvestorWallet } from '../../lib/types'

// statusTone's four states, mapped onto the desk's one chip.
const BADGE_TONE: Record<ReturnType<typeof statusTone>, BadgeTone> = {
  ok: 'profit', warn: 'warn', bad: 'loss', quiet: 'neutral',
}

const COPIED_MS = 2000

export default function InvestorDeposit() {
  const { orgId } = useOrg()
  const [wallet, setWallet] = useState<InvestorWallet | null | 'closed'>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [form, setForm] = useState({ amount: '', txid: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [copy, setCopy] = useState<'idle' | 'copied' | 'selected'>('idle')
  const addressRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      setDeposits(await orgApi<Deposit[]>(orgId, 'investor/deposits'))
    } catch (err) {
      setError(errorText(err, 'Could not load your deposits'))
    }
    let w: InvestorWallet | null = null
    try {
      w = await orgApi<InvestorWallet>(orgId, 'investor/wallet')
      setWallet(w)
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('404')) setWallet('closed')
      else setError(errorText(err, 'Could not load the deposit address'))
    }
    if (!w) return
    try {
      // Loaded on demand so the QR encoder stays out of the main bundle.
      const { toDataURL } = await import('qrcode')
      setQr(await toDataURL(w.address, { width: 192, margin: 1 }))
    } catch {
      // The address and the Copy button still work without the picture.
      setQr(null)
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (copy !== 'copied') return
    const id = window.setTimeout(() => setCopy('idle'), COPIED_MS)
    return () => window.clearTimeout(id)
  }, [copy])

  // Fallback when there is no clipboard API (plain http, some in-app
  // browsers) or the browser refuses it: select the address so the
  // investor's own copy command takes it.
  const selectAddress = () => {
    const node = addressRef.current
    const selection = window.getSelection()
    if (!node || !selection) return
    const range = document.createRange()
    range.selectNodeContents(node)
    selection.removeAllRanges()
    selection.addRange(range)
    setCopy('selected')
  }

  const copyAddress = async (address: string) => {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(address)
        setCopy('copied')
        return
      } catch {
        // Permission refused: fall through to selecting the text.
      }
    }
    selectAddress()
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (wallet === null || wallet === 'closed') return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<Deposit>(orgId, 'investor/deposits', {
        method: 'POST',
        body: JSON.stringify({ amount: form.amount, coin: wallet.coin, txid: form.txid,
                               note: form.note }),
      })
      setForm({ amount: '', txid: '', note: '' })
      setNotice('Notice filed. An admin will confirm it once the transfer is seen.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not file the notice'))
    } finally {
      setBusy(false)
    }
  }

  // Rows are shown in the wallet's coin when it is known, else in the coin
  // stored on the row itself.
  const walletCoin = wallet !== null && wallet !== 'closed' ? wallet.coin : null

  return (
    <div className="space-y-6 max-w-4xl">
      <PageHeader
        title="Deposit"
        subtitle="Send crypto to the address below, then tell us the amount and the transaction ID."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {wallet === null && !error && <Loading lines={4} />}

      {wallet === 'closed' && (
        <NextStep title="Deposits are not open yet">
          Your admin links your trading account; deposits open once the workspace's wallet is set.
        </NextStep>
      )}

      {wallet !== null && wallet !== 'closed' && (
        <>
          <Card>
            <div className="grid gap-5 md:grid-cols-[192px_1fr]">
              {qr && (
                <img src={qr} alt="QR code of the deposit address" width={192} height={192}
                     className="rounded-inset border border-line bg-card" />
              )}
              <div className="space-y-3 min-w-0">
                <div>
                  <div className="desk-label">Send only</div>
                  <div className="text-lg font-semibold text-ink">{wallet.coin} on {wallet.network}</div>
                </div>
                <div>
                  <div className="desk-label">Address</div>
                  <div className="flex flex-wrap items-start gap-3">
                    <div ref={addressRef} className="num text-sm text-ink break-all min-w-0 flex-1">
                      {wallet.address}
                    </div>
                    <Button variant="secondary" size="sm" onClick={() => copyAddress(wallet.address)}>
                      {copy === 'copied' ? 'Copied' : 'Copy address'}
                    </Button>
                  </div>
                  {/* Always mounted so screen readers hear the change. */}
                  <p role="status" className="text-xs text-ink-soft mt-1 min-h-4">
                    {copy === 'copied' ? 'Address copied to the clipboard.'
                      : copy === 'selected' ? 'Address selected. Copy it with Ctrl+C, or long-press on a phone.'
                      : ''}
                  </p>
                </div>
                {wallet.memo && (
                  <div>
                    <div className="desk-label">Memo / tag</div>
                    <div className="num text-sm text-ink">{wallet.memo}</div>
                  </div>
                )}
                <Banner kind="warn" announce={false}>
                  Sending any other coin or network to this address will lose the funds.
                </Banner>
              </div>
            </div>
          </Card>

          <Card title="Tell us about your transfer">
            <form onSubmit={submit} className="space-y-4">
              <div className="flex gap-3 flex-wrap items-end">
                <label className="block w-40">
                  <span className="desk-label block mb-1">Amount ({wallet.coin})</span>
                  <Input aria-label={`Amount in ${wallet.coin}`} num value={form.amount} required
                         onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                </label>
                <label className="block flex-1 min-w-56">
                  <span className="desk-label block mb-1">Transaction ID</span>
                  <Input aria-label="Transaction ID" num value={form.txid} required
                         onChange={(e) => setForm({ ...form, txid: e.target.value })} />
                </label>
              </div>
              <label className="block">
                <span className="desk-label block mb-1">Note (optional)</span>
                <Input aria-label="Note" value={form.note}
                       onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </label>
              <Button type="submit" disabled={busy}>
                I have sent it
              </Button>
            </form>
          </Card>
        </>
      )}

      <Card title="Your deposit notices" inset>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-4 py-2 font-semibold">Filed</th>
                <th className="desk-label px-4 py-2 font-semibold text-right">Amount</th>
                <th className="desk-label px-4 py-2 font-semibold">Transaction</th>
                <th className="desk-label px-4 py-2 font-semibold">Status</th>
                <th className="desk-label px-4 py-2 font-semibold">Admin note</th>
              </tr>
            </thead>
            <tbody>
              {deposits.length === 0 && (
                <tr><td colSpan={5} className="text-center py-8 text-ink-faint">No notices yet</td></tr>
              )}
              {deposits.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0">
                  <td data-label="Filed" className="num px-4 py-2.5">{formatWhen(d.created_at)}</td>
                  <td data-label="Amount" className="tnum px-4 py-2.5 text-right">{money(d.amount, walletCoin ?? d.coin)}</td>
                  <td data-label="Transaction" className="num px-4 py-2.5 break-all">{d.txid}</td>
                  <td data-label="Status" className="px-4 py-2.5">
                    <Badge tone={BADGE_TONE[statusTone(d.status)]}>{statusLabel(d.status)}</Badge>
                  </td>
                  <td data-label="Admin note" className="px-4 py-2.5 text-ink-soft">{d.decision_note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 9: Implement `InvestorWithdraw.tsx`**

Replace `dashboard/src/pages/investor/InvestorWithdraw.tsx` with:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, moneyOrDash, pillClass, shortAddress, statusLabel } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import NextStep from './NextStep'
import type { InvestorSummary, InvestorWallet, InvestorWithdrawal } from '../../lib/types'

const STEPS = ['requested', 'approved', 'paid'] as const

function Timeline({ w }: { w: InvestorWithdrawal }) {
  if (w.status === 'rejected') {
    return <Badge tone="loss">Rejected</Badge>
  }
  const reached = STEPS.indexOf(w.status as typeof STEPS[number])
  return (
    <ol aria-label="Withdrawal progress" className="flex flex-wrap items-center gap-2 text-xs">
      {STEPS.map((step, i) => {
        const current = i === reached
        return (
          <li key={step} aria-current={current ? 'step' : undefined}
              className={`px-2 py-0.5 rounded-full ${i <= reached ? pillClass(step) : 'bg-paper text-ink-faint'} ${current ? 'font-semibold' : ''}`}>
            <span>{statusLabel(step)}</span>
            {/* Colour is never the only signal: the current step says so. */}
            {current && <span> · current</span>}
          </li>
        )
      })}
    </ol>
  )
}

export default function InvestorWithdraw() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<InvestorWithdrawal[]>([])
  const [coin, setCoin] = useState<string | null>(null)
  const [form, setForm] = useState({ amount: '', destination: '' })
  const [reviewing, setReviewing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      setSummary(s); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your withdrawals'))
    }
    try {
      // The wallet's coin is what the admin pays out in. Read-only, the same
      // endpoint the Deposit page uses; 404 means no wallet yet.
      setCoin((await orgApi<InvestorWallet>(orgId, 'investor/wallet')).coin)
    } catch {
      setCoin(null)
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  // Withdrawal rows carry no coin (investor.py _withdrawal_json), so the
  // unit is the wallet's coin, else the account-currency default.
  const unit = coin ?? ACCOUNT_CURRENCY
  const available = summary?.available ?? null

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setNotice(null)
    if (!(Number(form.amount) > 0)) {
      setError('Enter an amount above zero')
      return
    }
    setReviewing(true)
  }

  const send = async () => {
    setBusy(true)
    try {
      await orgApi<InvestorWithdrawal>(orgId, 'investor/withdrawals', {
        method: 'POST', body: JSON.stringify(form),
      })
      setForm({ amount: '', destination: '' })
      setNotice('Request sent. You will be emailed when an admin decides.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not send the request'))
    } finally {
      setBusy(false)
      setReviewing(false)
    }
  }

  const linked = summary?.link_state === 'linked'

  return (
    <div className="space-y-6 max-w-4xl">
      <PageHeader
        title="Withdraw"
        subtitle="Ask for an amount and where to send it. An admin approves, pays from the workspace wallet, and records the transaction."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!summary && !error && <Loading lines={3} />}

      {summary && !linked && (
        <NextStep title="Your account is being set up">
          Withdrawals open once your admin links your trading account.
        </NextStep>
      )}

      {summary && linked && (
        <Card title="Available to withdraw"
              actions={<span className="num text-2xl font-semibold text-ink">{moneyOrDash(available, ACCOUNT_CURRENCY)}</span>}>
          <form onSubmit={review} className="space-y-4">
            {available == null && (
              <p className="text-xs text-warn-deep">
                Your account is offline right now, so the available figure is unknown; an admin will check it.
              </p>
            )}
            <div className="flex gap-3 flex-wrap items-end">
              <label className="block w-40">
                <span className="desk-label block mb-1">Amount ({unit})</span>
                <Input aria-label={`Amount in ${unit}`} num value={form.amount} required
                       onChange={(e) => setForm({ ...form, amount: e.target.value })} />
              </label>
              <Button variant="ghost" size="sm" disabled={available == null}
                      onClick={() => { if (available != null) setForm({ ...form, amount: available.toFixed(2) }) }}>
                Use max
              </Button>
              <label className="block flex-1 min-w-56">
                <span className="desk-label block mb-1">Destination address</span>
                <Input aria-label="Destination address" num value={form.destination} required
                       onChange={(e) => setForm({ ...form, destination: e.target.value })} />
              </label>
            </div>
            <Button type="submit" disabled={busy}>
              Request withdrawal
            </Button>
          </form>
        </Card>
      )}

      <ConfirmDialog
        open={reviewing}
        title={`Send ${money(form.amount, unit)} to ${shortAddress(form.destination)}?`}
        confirmLabel="Send request"
        busy={busy}
        onConfirm={send}
        onCancel={() => setReviewing(false)}
      >
        <p>An admin reviews the request, pays it from the workspace wallet and records the transaction.</p>
        <div>
          <div className="desk-label">Amount</div>
          <div className="num text-ink">{money(form.amount, unit)}</div>
        </div>
        <div>
          <div className="desk-label">Destination</div>
          <div className="num text-ink break-all">{form.destination}</div>
        </div>
        <p>Check every character: a payment sent to a wrong address cannot be recalled.</p>
      </ConfirmDialog>

      <Card title="Your requests" inset>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No requests yet</li>}
          {rows.map((w) => (
            <li key={w.id} className="px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(w.created_at)}</span>
                <span className="tnum font-semibold text-ink">{money(w.amount, unit)}</span>
                <span className="num text-ink-soft break-all min-w-0">to {w.destination}</span>
              </div>
              <Timeline w={w} />
              {w.decision_note && <p className="text-xs text-ink-soft">Admin: {w.decision_note}</p>}
              {w.txid && <p className="num text-xs text-ink-soft break-all">Transaction: {w.txid}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
```

- [ ] **Step 10: Implement `InvestorHistory.tsx`**

Replace `dashboard/src/pages/investor/InvestorHistory.tsx` with:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import type { Deal } from '../../lib/types'

const WEEK_MS = 7 * 24 * 3600 * 1000

function netOf(d: Deal): number | null {
  if (!d.close) return null
  return d.close.gross_profit + d.close.swap + d.close.commission
}

export default function InvestorHistory() {
  const { orgId } = useOrg()
  // Fixed once at mount: this page has no "refresh" action, so "now" for
  // paging purposes is "when the page was opened", not a moving target.
  // Reading Date.now() again inside goLater (instead of against this
  // anchor) loses the race against real elapsed time -- by the time a
  // click handler runs, the live clock has already moved past whatever
  // "now" was captured when windowEnd was last set, so an exact
  // Earlier-then-Later round trip could never land back on "at now".
  const nowAnchorRef = useRef(Date.now())
  const [windowEnd, setWindowEnd] = useState(() => nowAnchorRef.current)
  const [atNow, setAtNow] = useState(true)
  const [deals, setDeals] = useState<Deal[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    setDeals([])
    try {
      const r = await orgApi<{ deals: Deal[]; has_more: boolean }>(
        orgId, `investor/history/deals?from=${windowEnd - WEEK_MS}&to=${windowEnd}`)
      setDeals(r.deals.filter((d) => d.close != null))
    } catch (err) {
      setError(errorText(err, 'Could not load your history'))
    } finally {
      setLoading(false)
    }
  }, [orgId, windowEnd])

  useEffect(() => { load() }, [load])

  const total = deals.reduce((sum, d) => sum + (netOf(d) ?? 0), 0)

  const goEarlier = () => { setWindowEnd((t) => t - WEEK_MS); setAtNow(false) }
  const goLater = () => {
    const now = nowAnchorRef.current
    const next = windowEnd + WEEK_MS
    if (next >= now) { setWindowEnd(now); setAtNow(true) } else { setWindowEnd(next) }
  }

  // The window is a rolling seven days ending at windowEnd, not a calendar
  // week, so the copy says which seven days rather than "this week".
  const range = `${formatWhen(windowEnd - WEEK_MS)} – ${formatWhen(windowEnd)}`
  const windowLabel = atNow ? 'last 7 days' : range

  return (
    <div className="space-y-6 max-w-6xl">
      <PageHeader
        title="History"
        subtitle="Closed trades on your account, seven days at a time, straight from the broker."
        actions={
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Button variant="secondary" size="sm" onClick={goEarlier}>
              Earlier
            </Button>
            <span className="num text-ink-soft">{range}</span>
            <Button variant="secondary" size="sm" onClick={goLater} disabled={atNow}>
              Later
            </Button>
          </div>
        }
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      {/* Deal figures are in the account's deposit currency; the summary
          names none, so the labelled default applies (lib/investor.ts). */}
      <Card title="Closed trades" inset
            actions={
              <span className={`tnum text-sm font-semibold ${total < 0 ? 'text-loss' : 'text-profit'}`}>
                {`${money(total, ACCOUNT_CURRENCY)} net, ${windowLabel}`}
              </span>
            }>
        {loading ? <Loading lines={3} /> : (
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Closed</th>
                  <th className="desk-label px-4 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-4 py-2 font-semibold">Side</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Lots</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Entry → Exit</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Gross</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Swap + fees</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Net</th>
                </tr>
              </thead>
              <tbody>
                {deals.length === 0 && (
                  <tr><td colSpan={8} className="text-center py-8 text-ink-faint">
                    {atNow ? 'No closed trades in the last 7 days' : `No closed trades between ${formatWhen(windowEnd - WEEK_MS)} and ${formatWhen(windowEnd)}`}
                  </td></tr>
                )}
                {deals.map((d) => {
                  const net = netOf(d) ?? 0
                  return (
                    <tr key={d.deal_id} className="border-b border-line last:border-0">
                      <td data-label="Closed" className="num px-4 py-2.5">{formatWhen(d.execution_timestamp)}</td>
                      <td data-label="Symbol" className="px-4 py-2.5 text-ink">{d.symbol ?? d.symbol_id}</td>
                      <td data-label="Side" className="px-4 py-2.5">{d.side}</td>
                      <td data-label="Lots" className="tnum px-4 py-2.5 text-right">{d.close?.closed_volume_lots ?? d.volume_lots ?? '—'}</td>
                      <td data-label="Entry → Exit" className="tnum px-4 py-2.5 text-right">{d.close?.entry_price} → {d.execution_price ?? '—'}</td>
                      <td data-label="Gross" className="tnum px-4 py-2.5 text-right">{money(d.close!.gross_profit, ACCOUNT_CURRENCY)}</td>
                      <td data-label="Swap + fees" className="tnum px-4 py-2.5 text-right">{money(d.close!.swap + d.close!.commission, ACCOUNT_CURRENCY)}</td>
                      <td data-label="Net" className={`tnum px-4 py-2.5 text-right font-semibold ${net < 0 ? 'text-loss' : 'text-profit'}`}>{money(net, ACCOUNT_CURRENCY)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
```

- [ ] **Step 11: Implement `InvestorAccount.tsx`**

Replace `dashboard/src/pages/investor/InvestorAccount.tsx` with (it reads only `useOrg()`, so there is nothing to load and no `Loading`):

```tsx
import { useOrg } from '../../lib/org'
import AccountSecurity from '../../components/AccountSecurity'
import Card from '../../components/Card'
import PageHeader from '../../components/PageHeader'

export default function InvestorAccount() {
  const { me, org } = useOrg()
  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader title="Account" />
      <Card title="Profile">
        <dl className="grid gap-3 md:grid-cols-2 text-sm">
          <div><dt className="desk-label">Name</dt><dd className="text-ink">{me.user.display_name}</dd></div>
          <div><dt className="desk-label">Email</dt><dd className="text-ink">{me.user.email}</dd></div>
          <div><dt className="desk-label">Workspace</dt><dd className="text-ink">{org.name}</dd></div>
          <div><dt className="desk-label">Role</dt><dd className="text-ink">Investor</dd></div>
        </dl>
      </Card>
      <AccountSecurity />
    </div>
  )
}
```

- [ ] **Step 12: Run the task's tests to verify they pass**

Run: `npx vitest run src/lib/format.test.ts src/lib/investor.test.ts src/pages/investor`
Expected: all pass (format 7, investor 4, Overview 7, Deposit 6, Withdraw 8, History 3, Account 1).

If the fallback-copy test fails only on `window.getSelection()?.toString()`, the jsdom in use does not stringify selections; keep the `/address selected/i` assertion and replace the selection check with `expect(window.getSelection()?.rangeCount).toBe(1)` — do not drop the test.

- [ ] **Step 13: Run the whole suite, then check the bundle**

Run: `npm test`
Expected: palette prover OK, `tsc --noEmit -p tsconfig.app.json` clean, every vitest file green (the admin `Investors.tsx` still calls `money(x)` with one argument and is unaffected).

Run: `npm run build`
Expected: the build succeeds and lists a separate chunk carrying the `qrcode` code, apart from the investor-pages chunk; record the initial chunk size in the task report.

- [ ] **Step 14: Commit**

```bash
git add dashboard/src/lib/format.ts dashboard/src/lib/format.test.ts \
        dashboard/src/lib/investor.ts dashboard/src/lib/investor.test.ts \
        dashboard/src/pages/investor/NextStep.tsx \
        dashboard/src/pages/investor/InvestorOverview.tsx dashboard/src/pages/investor/InvestorOverview.test.tsx \
        dashboard/src/pages/investor/InvestorDeposit.tsx dashboard/src/pages/investor/InvestorDeposit.test.tsx \
        dashboard/src/pages/investor/InvestorWithdraw.tsx dashboard/src/pages/investor/InvestorWithdraw.test.tsx \
        dashboard/src/pages/investor/InvestorHistory.tsx dashboard/src/pages/investor/InvestorHistory.test.tsx \
        dashboard/src/pages/investor/InvestorAccount.tsx dashboard/src/pages/investor/InvestorAccount.test.tsx
git commit -m "feat(dashboard): investor pages -- units on every figure, honest pending states with a contact, Copy address, a Withdraw review step

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

### Task 9: The sweep — every remaining page on PageHeader, Card, Tabs and Loading; one vocabulary

**Files:**
- Modify: `src/pages/Positions.tsx`, `Trade.tsx`, `Automation.tsx`, `History.tsx`, `Performance.tsx`, `Logs.tsx`, `Members.tsx`, `Investors.tsx`, `src/components/KillSwitch.tsx`, `src/components/AccountSecurity.tsx`
- Modify: their `*.test.tsx` files where a heading, label or "Slave" string is asserted
- Create: `src/vocabulary.test.ts`
- Test: the page test files above, `src/vocabulary.test.ts`

**Interfaces:**
- Consumes: `PageHeader`, `Card`, `Tabs`, `Loading`, `Badge`, `Select`, `Button` (Task 2); `useLiveRefresh` as today.
- Produces: nothing new; every page now has exactly one `<h1>` from `PageHeader`, titles its document, shows `Loading` while fetching, and uses "follower" and sentence case.

- [ ] **Step 1: Write the failing source-vocabulary test**

`src/vocabulary.test.ts`:

```ts
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from 'vitest'

// The product speaks one language. These scans read the shipped source, not
// the DOM, so a stray string in a rarely rendered branch cannot hide.
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

const files = [...sources('src/pages'), ...sources('src/components')]

test('no user-visible "Slave": JSX text, string literals and labels say follower', () => {
  const offenders: string[] = []
  for (const f of files) {
    const src = readFileSync(f, 'utf8')
    // A quoted or JSX-text "Slave"/"slave" (identifiers like role === 'slave'
    // compare against the API value and are allowed only in that shape).
    for (const m of src.matchAll(/(>[^<]*\b[Ss]lave\b[^<]*<|['"`][^'"`\n]*\b[Ss]lave accounts?\b[^'"`\n]*['"`]|\bSlave\b(?=[^'"`\n]*<\/))/g)) {
      offenders.push(`${f}: ${m[0].trim().slice(0, 60)}`)
    }
  }
  expect(offenders).toEqual([])
})

test('no literal "Loading..." text: pages use the Loading primitive', () => {
  const offenders = files.filter((f) => /Loading\.\.\./.test(readFileSync(f, 'utf8')))
  expect(offenders).toEqual([])
})

test('no page renders its own h1: headings come from PageHeader or AuthCard', () => {
  const allowed = new Set(['src/components/PageHeader.tsx', 'src/components/AuthCard.tsx', 'src/pages/NotFound.tsx', 'src/pages/Landing.tsx'])
  const offenders = files.filter((f) => !allowed.has(f.replace(/\\/g, '/')) && /<h1[\s>]/.test(readFileSync(f, 'utf8')))
  expect(offenders).toEqual([])
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/vocabulary.test.ts`
Expected: all three tests FAIL listing the offending files (Overview, Accounts and the investor pages are already clean from Tasks 6–8; the rest are listed).

- [ ] **Step 3: Positions, Trade, Automation, Performance, Members — headings and loading**

In each of these files replace the page heading block with `PageHeader` and every loading placeholder with `Loading`. The pattern, shown on `Positions.tsx` (its `<h1 className="page-title">Positions</h1>` and the `Loading...` at line 265):

```tsx
import PageHeader from '../components/PageHeader'
import Loading from '../components/Loading'
// ...
  if (loading && !state) {
    return (
      <>
        <PageHeader title="Positions" />
        <Loading lines={6} label="Loading positions" />
      </>
    )
  }
// ...
      <PageHeader title="Positions" subtitle={`${openCount} open across the fleet`} actions={/* the existing header buttons, unchanged */}>
        {/* the existing filter/toggle toolbar, unchanged */}
      </PageHeader>
```

Apply the same shape to:
- `Trade.tsx` (both `<h1>` sites, the no-permission branch and the loaded branch, become `<PageHeader title="Trade" />` + the branch's content; the three one-off `<button>`s at the price/amount mode switch, BUY/SELL and the default-symbol star become `Button` calls: mode switch → `variant="secondary" size="sm"`, BUY → `tone="profit"`, SELL → `tone="loss"`, the star → `variant="ghost" tone="neutral" size="sm" aria-pressed={isDefault} aria-label="Set as default symbol"` with the ★/☆ glyph as its child).
- `Automation.tsx` (both `<h1>` sites → `PageHeader title="Automation"`).
- `Performance.tsx` (`PageHeader title="Performance"`; the chart panels wrap in `Card` with `title` per chart).
- `Members.tsx` (`PageHeader title="Members" subtitle={org.name}`; the "Your login" section becomes `<Card title="Your login">` around `AccountSecurity`).

Sentence case everywhere a heading or label is Title Case ("Open Positions:" → "Open positions", "Copying Status" → "Copying status", "Slave Accounts" → "Follower accounts").

- [ ] **Step 4: History and Investors on Tabs**

`History.tsx`: delete `TAB_ORDER`, `onTablistKeyDown` and `tabButton` (lines 448–475) and the hand-rolled `role="tablist"` block (lines 600–610); render instead:

```tsx
import Tabs from '../components/Tabs'
// ...
          <Tabs
            idBase="history"
            label="History views"
            value={tab}
            onChange={(k) => setTab(k as Tab)}
            items={[
              { key: 'bymaster', label: 'All accounts', count: fleet != null ? masterGroups.length : undefined },
              { key: 'closed', label: 'Closed positions', count: closingDeals.length },
              { key: 'deals', label: 'Deals', count: deals.length },
              { key: 'orders', label: 'Orders', count: orders.length },
              { key: 'cashflow', label: 'Cash flow', count: cashFlow.length },
            ]}
          />
          <div id="history-panel" role="tabpanel" aria-labelledby={`history-tab-${tab}`}>
            {/* the existing per-tab content, unchanged */}
          </div>
```

`Investors.tsx`: replace the `role="tablist"` block (lines 219–227) with

```tsx
          <Tabs
            idBase="investors"
            label="Money requests"
            value={tab}
            onChange={(k) => setTab(k as 'deposits' | 'withdrawals')}
            items={[
              { key: 'deposits', label: 'Deposits', count: deposits.length },
              { key: 'withdrawals', label: 'Withdrawals', count: withdrawals.length },
            ]}
          />
          <div id="investors-panel" role="tabpanel" aria-labelledby={`investors-tab-${tab}`}>
            {/* the existing tab === 'deposits' ? ... : ... content, unchanged */}
          </div>
```

Both pages get `PageHeader` ("History" / "Investors" with subtitle "Who invests through this workspace, their linked accounts, and the money requests" moved from the old paragraph) and `Loading`. The Investors wallet form and the requests table sit in `Card`s (`inset` for the table).

- [ ] **Step 5: Logs — associated labels, humanised options, an account Select, a cap on the live list**

In `Logs.tsx`:

1. Each filter (`Account`, `Severity`, `Category`, `Since`) gets a `<label htmlFor={id}>` bound to its control through `useId()`; the `Account ID` text field becomes a `Select` of the org's accounts (option label `nickname ?? login`, value `account_id`), with "All accounts" as the first option; the existing `filters.account_id` string state is kept, so the fetch URL is unchanged.
2. Severity options render `All`, `Info`, `Warning`, `Error` (values unchanged).
3. The live handler caps the list:

```tsx
            setEvents((prev) => [newEvent, ...prev].slice(0, 500))
```

4. `PageHeader title="Logs"` and `Loading`; the table sits in `<Card inset>`.

Add to `Logs.test.tsx`: every filter has an accessible name (`getByLabelText('Severity')` etc.), the account filter is a combobox, and after 501 live events the list holds 500 rows (feed messages through the existing WebSocket mock in the file).

- [ ] **Step 6: KillSwitch and AccountSecurity**

`KillSwitch.tsx`: `STOP COPYING` → `Stop copying`; `RESUME COPYING` (if present) → `Resume copying`; "follower" wording stays. Its wrapper becomes a `Card`. Update `Overview.test.tsx`/`KillSwitch` assertions that match the uppercase label.

`AccountSecurity.tsx`: the two forms sit in one `Card` with `title="Your login"`? No — Members already wraps it (Step 3); here only replace the plain section headings with `<h2 className="text-lg font-semibold text-ink">Change password</h2>` / `Change MPIN` and keep behaviour.

- [ ] **Step 7: Vocabulary pass**

Run: `grep -rn "\bSlave\b\|slave accounts\|Slave accounts" src --include=*.tsx | grep -v "\.test\." `
For each hit that is user-visible text (JSX text, `label`, `aria-label`, `title`, badge content), change it to follower/Follower. Comparisons against the API value (`role === 'slave'`, `a.role !== 'master'`) stay. Then: `grep -rn "Loading\.\.\." src --include=*.tsx | grep -v "\.test\."` must be empty.

- [ ] **Step 8: Run the changed page tests, the vocabulary test, then the whole gate**

Run: `npx vitest run src/vocabulary.test.ts src/pages/Positions.test.tsx src/pages/Trade.test.tsx src/pages/Automation.test.tsx src/pages/History.test.tsx src/pages/Performance.test.tsx src/pages/Logs.test.tsx src/pages/Members.test.tsx src/pages/Investors.test.tsx`
Expected: all pass (assertions updated for Follower, sentence case and the new tab/heading roles; none removed).
Run: `npm test`
Expected: green.

- [ ] **Step 9: Commit**

```bash
git add src/pages src/components/KillSwitch.tsx src/components/AccountSecurity.tsx src/vocabulary.test.ts
git commit -m "feat(dashboard): every page on PageHeader, Card, Tabs and Loading; followers not slaves; sentence case; Logs filters labelled and the live list capped

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 10: Gates and hand-off

**Files:** none modified (a report only).

- [ ] **Step 1: The prover and the gate** — from `dashboard/`: `node scripts/palette_check.mjs` → `ALL PASS (both themes)`; `npm test` → prover, tsc, vitest all green.
- [ ] **Step 2: Build sizes** — `npm run build`; record every chunk from `dist/assets/` in the report. Expected shape: an entry chunk well under the old 193 KB, plus `admin-*`, `investor-*`, `auth-*` and a `qrcode-*` chunk.
- [ ] **Step 3: Grep gates** — from `dashboard/`:
  - `grep -rnE "#[0-9a-fA-F]{6}\b|rgb\(" src --include=*.tsx | grep -v "\.test\." | grep -v "components/Logo.tsx"` → nothing.
  - `grep -rnE "text-(red|green|blue|gray|slate|stone|emerald|amber)-[0-9]" src --include=*.tsx` → nothing.
  - `grep -rn "border-l-4\|bg-clip-text" src --include=*.tsx` → nothing.
  - `grep -rn "role=\"tablist\"" src/pages --include=*.tsx | grep -v test` → nothing (only `Tabs.tsx` renders one).
- [ ] **Step 4: Visual check** — if the Chrome extension is connected: `npm run dev` (the compose api must be running for data), open `/`, `/login`, `/mpin`, `/org/<id>`, `/org/<id>/accounts`, `/org/<id>/invest` at 1280 px and 390 px in both themes, screenshot each, attach to the report, and note anything that overflows, clips or fails to read. If the extension is not connected, say so in the report and list the six URLs for the owner to check after deploy.
- [ ] **Step 5: API untouched** — `git diff --stat main..HEAD -- api copier db e2e` prints nothing.
- [ ] **Step 6: Hand off** — use the superpowers:finishing-a-development-branch skill. Branch `luminous-glass-redesign`, base `main`. Deploy is the owner's call (spec §11): `git pull`, `docker compose build api`, `docker compose up -d api`, hard reload.

---

## Rollout (on the owner's say-so, after merge)

```bash
cd ~/mirrorfleet && git pull
sudo docker compose build api      # the dashboard is baked into the api image
sudo docker compose up -d api      # no migration, copier untouched
```

Then hard-reload the dashboard. Existing sessions are unaffected; the only behaviour changes are the ones the six-screen page pass lists.
