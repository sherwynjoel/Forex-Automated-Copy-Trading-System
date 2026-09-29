# Luminous Glass — MirrorFleet frontend redesign

**Status:** implemented and deployed to mirrorfleet.com on 2026-09-29 (main 731334c; Approach C: token re-skin plus a
six-screen page pass).
**Owner decisions:** direction "luminous glass"; accent aqua/teal; light first
with a dim mode kept; Rubik only, committed scale.
**Scope:** `dashboard/` only. The API, copier, database and every request or
response shape stay exactly as they are.

## 1. Goal

The whole product looks new: very light, airy, premium, with frosted glass used
on purpose. A first-time visitor should think "who designed this?", not "which
template is this?". A trader should still read every number instantly. Both
themes keep passing the contrast prover; every page keeps its tests.

Non-goals: no new features, no API changes, no data-layer refactor (a shared
store is a separate follow-up), no ESLint adoption (separate follow-up), no
copy rewrite beyond the vocabulary sweep in §8.

## 2. Visual language

### 2.1 Colour tokens

All values below were checked against every pair the prover enforces (§10)
and pass in both themes. Names stay the same as today so every existing
`text-ink`, `bg-card`, `border-field-line` class keeps working.

| Token | Light | Dim | Role |
|---|---|---|---|
| `paper` | `#f4fafb` | `#0e1a1f` | page ground under the wash |
| `card` | `#ffffff` | `#152329` | opaque data surface (insets, drawer and dialog bodies) |
| `ink` | `#14303a` | `#e6f2f4` | text |
| `ink-soft` | `#496670` | `#a8c0c6` | secondary text, labels |
| `ink-faint` | `#587681` | `#8fa9b0` | timestamps, placeholders, empty states |
| `line` | `#dbe9ed` | `#243740` | hairlines |
| `line-strong` | `#bcd3da` | `#31474f` | strong hairlines |
| `field-line` | `#6a8893` | `#7d979f` | control edges (3:1) |
| `brand` | `#0b7c80` | `#5fd0d3` | the one accent: primary actions, links, focus, selection |
| `brand-deep` | `#075d61` | `#8fe3e5` | hover/pressed, brand text on wash |
| `brand-wash` | `#e0f5f4` | `#173538` | selected rows, chips, notice banners |
| `profit` / `-deep` / `-wash` | `#15803d` / `#116230` / `#e6f6ec` | `#43c07a` / `#7edfa6` / `#152b1e` | gains, Buy, Resume |
| `loss` / `-deep` / `-wash` | `#c92e45` / `#a52333` / `#fdecec` | `#f26a80` / `#fda4b0` / `#3a1c24` | losses, Sell, danger |
| `warn` / `-deep` / `-wash` | `#9a6700` / `#7a5200` / `#fbf1d9` | `#d09a3e` / `#e9c37c` / `#332708` | dry run, cautions |
| `on-accent` | `#ffffff` | `#0e1a1f` | text on brand/profit/loss/warn fills |
| `wash-deep` | `#c6e7ec` | `#0a1418` | the deepest stop of the wash; the prover's glass underlay |
| `glass` | `rgb(255 255 255 / 0.72)` | `rgb(21 35 41 / 0.72)` | frosted fill |
| `glass-line` | `rgb(20 48 58 / 0.10)` | `rgb(230 242 244 / 0.10)` | frosted edge |

Red keeps meaning danger only: the "Live" environment badge moves to a
neutral ink chip with a filled dot, so red is never "live".

### 2.2 The luminous wash

A fixed, full-viewport layer behind the page (`body::before`, `z-index: -1`,
`pointer-events: none`) paints three large soft radial gradients over
`paper`:

- aqua `rgb(11 124 128 / 0.16)` centred top-left,
- sky `rgb(96 165 250 / 0.12)` centred right,
- lilac `rgb(139 127 215 / 0.10)` centred bottom.

They drift with one 60 s alternating keyframe that moves each gradient's
centre by a few percent. Under `prefers-reduced-motion` the drift stops (the
existing global block already does this); under `prefers-reduced-transparency`
the wash still paints (it is not glass) but glass falls back to `card`. The
dim theme uses the same three hues at 0.14 / 0.10 / 0.08 over the dark
ground. The wash is the only decoration on the page; nothing else glows.

### 2.3 Glass, and where it may appear

Glass is a material for layers that float over the wash. It may appear on:

1. the navigation rail and the phone tab bar,
2. the desk strip and page headers,
3. `Card` panels (KPI tiles, tickets, settings panels, the auth card),
4. drawer and dialog headers, popovers.

`.glass` = `background: var(--color-glass); border: 1px solid
var(--color-glass-line); backdrop-filter: blur(16px) saturate(1.3)`, with the
existing `@supports not` and reduced-transparency fallbacks to `card`.

Data never sits directly on glass. Inside a glass card, tables, forms, charts
and any run of figures sit on an **inset**: `.inset` = `bg-card` with a `line`
hairline and 12px radius. Drawer bodies and dialog bodies stay opaque `card`.
The prover proves ink, ink-soft, ink-faint, brand and brand-deep on glass
mixed over `wash-deep`, the darkest thing glass can float over.

### 2.4 Shape, depth, spacing

- Radii: `--radius-card: 1rem`, `--radius-inset: 0.75rem`,
  `--radius-control: 0.625rem`, chips fully rounded. Tailwind utilities map to
  these through `@theme` (`rounded-card`, `rounded-inset`, `rounded-control`).
- Shadows (light): `--shadow-card: 0 8px 30px rgb(11 124 128 / 0.08)`,
  `--shadow-float: 0 16px 48px rgb(20 48 58 / 0.14)` for drawers, dialogs and
  popovers. Dim: same geometry at `rgb(0 0 0 / 0.45)` and `0.6`.
- Hairlines stay 1px. No side-stripe accents, no gradient text, no glow
  borders.
- Spacing rhythm: page padding 24px (16px on phones), card padding 20px,
  section gap 32px, dense tables keep their 8/10px cells.
- Layer scale unchanged: 10 sticky chrome and popovers, 40 drawers and phone
  nav, 50 dialogs.

### 2.5 Type

Rubik only. `--font-display` becomes an alias of `--font-sans` and stays for
existing class names. Scale (rem): page title 1.75 / 700 / -0.015em; section
title 1.125 / 600; body 0.9375; small 0.8125; label (`.desk-label`) 0.6875 /
600 / 0.08em uppercase, used only for table headers and field labels, never as
a section kicker. Numbers stay `.num` (tabular, 500). Landing hero
`clamp(2.25rem, 5vw, 4rem)` / 700 / -0.02em, `text-wrap: balance`.

### 2.6 Motion

Easing `cubic-bezier(0.23, 1, 0.32, 1)`. Durations: state changes 150 ms,
panel mount 220 ms (fade + rise 8px), route crossfade 180 ms, wash drift 60 s.
Motion conveys state or arrival, never decoration beyond the wash. Everything
obeys the global reduced-motion block and, where the `motion` library is used,
`useReducedMotion`.

## 3. Components

Existing primitives keep their APIs and tests; their recipes change.

| Primitive | Change |
|---|---|
| `Button` | `primary`: solid `brand` fill, `on-accent` text, inner top highlight `inset 0 1px 0 rgb(255 255 255 / 0.25)`, hover `brand-deep`. `secondary`: glass fill, `ink` text, `glass-line` edge. `ghost` unchanged. Radius `control`. Tones and sizes unchanged. |
| `Input`, `Select` | `card` fill, `field-line` edge, radius `control`, aqua focus ring, chevron recoloured to `brand`. |
| `Badge` | pill, wash fills as today, new `neutral` uses `line`/`ink-soft`. |
| `Banner` | opaque wash tints as today (alerts must never be translucent), radius `inset`. |
| `Drawer`, `ConfirmDialog` | glass header, opaque body, frosted scrim, `shadow-float`, radius `card` on the desktop drawer edge. |
| `PinInput` | unchanged except the box radius follows `control`. |
| **New `Card`** | `<Card title? actions? glass={true} inset?>`: glass panel with optional header row; `inset` renders children on an inset surface. All tiles, tickets and settings panels use it. |
| **New `Tabs`** | `<Tabs items value onChange>`: `role="tablist"`, roving `tabIndex`, arrow/Home/End keys, `aria-controls`/`aria-labelledby`, underline indicator in `brand`. Replaces the hand-rolled bars in History and Investors. |
| **New `Loading`** | skeleton lines/blocks on an inset (`aria-busy`, `role="status"`, text "Loading"); replaces every ad hoc "Loading..." text. |
| **New `PageHeader`** | `<PageHeader title subtitle? actions?>` renders the `<h1 class="page-title">`, calls `usePageTitle(title)` (sets `document.title` to "Title · MirrorFleet"), glass on scroll. |
| **New `AuthCard`** | the shared glass card over the wash for Login, Register, MPIN, Join, Welcome: logo, a real `<h1>` with the page purpose, a lead line, children. |
| **New `SkipLink`** | "Skip to content", visible on focus, targets `<main id="main">`. |
| **New `NotFound`** | route `*`: "That page is not here", a link to the desk (or `/login` when signed out). |
| `Logo` | mark recoloured to the aqua ramp `#0b7c80`, `#3aa7aa`, `#8fd4d6`, waterline `line-strong`; wordmark Mirror in `ink`, Fleet in `brand`. Favicon data URI and `theme-color` follow; `useTheme.ts` `PAPER` map updated and covered by a test that reads `index.css`. |

## 4. Shell

- **Rail (≥ lg):** a floating glass rail with 16px inset from the viewport
  edge, grouped nav with small group names (Desk: Overview, Positions, Trade,
  History · Fleet: Accounts, Automation, Performance · Org: Members,
  Investors, Logs), `aria-current="page"` on the active link, the org
  switcher and theme toggle at the bottom. Investors see their five items
  ungrouped.
- **Desk strip:** glass bar under the page top with the pulse dot, copying
  state, master equity, P&L and close-all; its behaviour is unchanged.
- **Phone (< lg):** a bottom glass tab bar in thumb reach with five items
  (admin: Overview, Positions, Trade, Accounts, More; investor: Overview,
  Deposit, Withdraw, History, Account). "More" opens the existing Drawer
  with the remaining links. The top bar keeps the logo and org name.
- **Skip link** first in the tab order; `<main id="main">`; focus moves to the
  `<h1>` on route change.
- **Code splitting:** three lazy groups with `React.lazy` + one `Suspense`
  fallback (`Loading`): admin pages, investor pages, auth pages. `Landing`
  stays eager (it renders at `/`). `qrcode` is dynamically imported inside
  the Deposit page.
- **Route crossfade** 180 ms via `motion`'s `AnimatePresence` keyed on the
  pathname; disabled under reduced motion.

## 5. The six-screen page pass

Acceptance criteria per screen. Everything not listed inherits the system.

1. **Landing** (`pages/Landing.tsx`): hero = one strong headline and one
   sentence over the wash, with a real product screenshot (the Overview at
   1280 px, captured from the running app, stored under `dashboard/public/`)
   inside a glass frame; primary "Create account" and secondary "Sign in".
   Then three asymmetric panels (one wide, two narrow) that each show a piece
   of the product rather than an icon; a short "how it works" as prose with
   the three steps inline (no numbered circles, no kicker labels); the
   investor section as one panel; FAQ as a disclosure list; footer with the
   risk notice, support email and address filled from `LANDING_FACTS`. A
   `<meta name="description">` is added to `index.html`. No identical card
   grids, no eyebrow kickers, no gradient text.
2. **Auth stack** (Login, Register, MPIN, Join, Welcome): all use `AuthCard`;
   each has a real `<h1>` ("Sign in", "Create your account", "Choose your
   MPIN" / "Enter your MPIN" / "Reset your MPIN", "Join <org>", "Welcome");
   Welcome explains what an organisation is in one line and shows "Create"
   and "Join with an invite" as two labelled choices; Register says
   registration is by invite only as soon as the server's registration-closed
   error arrives, keeping the typed values in the form; Login gains a "Forgot
   password?" line that explains the current path (ask an admin) until a
   reset flow exists; "Reset MPIN" is a brand-tone action; "Prove your
   password" becomes "Confirm your password".
3. **Overview** (`pages/Overview.tsx`): triage first. Top: a compact KPI row
   of four glass tiles (master equity, today's P&L, open positions,
   followers copying). Then an "Attention" panel listing only live problems
   (refresh failed, offline follower, degraded copier, margin call, expiring
   token), each with its action link; empty when all is well. Then the fleet
   grid. The mid-page kill switch (stop/resume copying, dry run) moves into
   the desk strip beside close-all, so every page carries it and Overview
   stops duplicating it; the master card stops repeating equity. Empty org: a four-step setup checklist
   (connect the master → add followers → dry run → go live) with links to
   Accounts and Automation, replacing "No slave accounts configured".
4. **Accounts** (`pages/Accounts.tsx`): rows become read-only (login, nickname,
   role, equity, health) plus a Details button and one "⋯" menu (Flatten,
   Re-grant when applicable, Rotate key, Disconnect/Remove). Nickname, role,
   enabled and cutoff edit inside the existing Drawer with explicit Save;
   Re-grant moves to the page header. The MT5 key-reveal dialog and the
   symbol-alias editor become their own components. Empty state gets a
   "Connect cTrader ID" button.
5. **Investor Overview** (`pages/investor/InvestorOverview.tsx`) plus Deposit
   and Withdraw: every money figure shows its unit (`USDT` or the wallet
   coin; equity in the account currency); unlinked and "deposits not open"
   states say what happens next and show the org's support contact when
   present; the Deposit address gets a Copy button with confirmation; Withdraw
   gets a review step ("Send 500.00 USDT to T…9f?"), a "Use max" button, and
   a text marker plus `aria-current` on the status timeline.
6. **Mobile shell**: the bottom tab bar, the top bar, the margin-call banner
   wrapping onto two lines with Dismiss below the text, and the `w-40` date
   column in Recent activity becoming fluid.

## 6. Everything else (the sweep)

All remaining pages adopt `PageHeader`, `Card` and `Loading`, replace
hand-rolled buttons and tab bars with the primitives (the three deliberate
one-offs in Accounts disappear with §5.4; the Trade one-offs become `Button`
`size="sm"` variants), and lose any `border-2`, hand-typed radius or shadow.
Tables keep `.stack-table`.

## 7. Logo and assets

`components/Logo.tsx`, the favicon data URI in `index.html`, the
`theme-color` values (`#f4fafb` / `#0e1a1f`) and `hooks/useTheme.ts` change
together. `app-logo.png` at the repo root is regenerated from the SVG by a
one-off script in the task, not by hand.

## 8. Vocabulary and copy sweep

- "Slave" → "follower" everywhere the user reads it (column headers, badges,
  tiles, copy, tests). API field and role names are untouched.
- Sentence case for every heading and label.
- One `Loading` primitive; no "Loading..." text.
- Status badges show humanised labels, not raw enum strings.
- Logs severity options capitalised; the Logs account filter becomes a
  `Select` of the org's accounts.

## 9. Accessibility

Kept: global focus ring (now `brand` aqua, 3:1 on both grounds), reduced
motion, `role="alert"`/`role="status"` conventions, `.stack-table`.
Added: skip link, per-route titles, real `<h1>`s, `aria-current` on nav,
associated labels on the Logs filters, the `Tabs` primitive, error text that
expands in place instead of living in `title`, 44px targets on the phone tab
bar. The dim theme is a first-class theme: every new pair is proven there too.

## 10. Testing and gates

- `scripts/palette_check.mjs`: glass underlay becomes `wash-deep`; new pairs
  `ink-faint on glass-solid`, `on-accent on brand` (kept), `brand on
  wash-deep` (3:1, focus ring over the wash), `glass-line` presence; the
  script keeps failing `npm test` on any miss.
- Every changed primitive keeps its test file green; new primitives (`Card`,
  `Tabs`, `Loading`, `PageHeader`, `AuthCard`, `SkipLink`, `NotFound`) get
  tests that assert roles, keyboard behaviour and text.
- Shell tests: nav groups render, `aria-current` follows the route, the
  bottom bar shows the right five items per role, the skip link targets
  `#main`, an unknown URL renders NotFound, `document.title` changes per
  route, lazy groups resolve (a test awaits the Suspense fallback).
- Page tests are updated for the new structure, never weakened; the six
  screens gain tests for their acceptance criteria (setup checklist, review
  step, Copy button, units, Attention panel).
- A visual check at 1280 px and 390 px in both themes for the six screens,
  with screenshots attached to the task report (needs the browser extension).
- `npm test` green at the end of every task; production build size reported
  per task (target: initial admin chunk under 150 KB minified).

## 11. Rollout

Branch `luminous-glass-redesign` off `main`, one task per commit, review after
each task, whole-branch review at the end (most capable model). Deploy only on
the owner's say-so: `git pull`, `docker compose build api`, `docker compose up
-d api` (the dashboard is baked into the api image; no migration), then a hard
reload. The copier and database are untouched.

## 12. Out of scope, noted for later

Shared org-data store and polling coordination; ESLint with the hooks plugin;
Logs live-list cap and reconnect reuse (a small separate fix, can ship before
this branch); a stronger MPIN reset; the data-layer duplication list from the
2026-09-29 critique.
