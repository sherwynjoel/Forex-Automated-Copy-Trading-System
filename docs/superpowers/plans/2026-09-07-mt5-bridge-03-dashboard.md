# MT5 Bridge 03 — Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard treats an MT5 account as a first-class account: an admin adds one and receives its one-time key with the install steps, every row says which platform it is on and whether its terminal is connected, the Details drawer shows the terminal and lets an admin correct the canonical → broker symbol mapping, and every place that names an account by its cTrader login names an MT5 account by its MT5 login instead.

**Architecture:** All MT5 knowledge on the client lives in two optional fields the accounts list already carries (`Account.platform`, `Account.mt5`) plus one small helper module (`lib/platform.ts`) that turns them into labels; pages consume the helpers, so the cTrader rendering is byte-identical where no MT5 account exists. The Accounts page gains three dialogs (nickname → create, one-time key reveal, rotate-key confirm) built on the existing `ConfirmDialog`, and the Details drawer swaps its OAuth section for a Terminal section and a Symbol-mapping editor when the account is MT5. Layout, History, Trade, Performance and Overview each change a single label or marker.

**Tech Stack:** React 18 + TypeScript 5.6 + Vite 5, Tailwind v4 (token classes only), react-router-dom 7, vitest 2 + @testing-library/react 16 + @testing-library/user-event 14 (jsdom).

**Spec:** `docs/superpowers/specs/2026-09-07-mt5-bridge-design.md`
**Contract:** `docs/superpowers/plans/2026-09-07-mt5-bridge-interfaces.md` — section 4 is binding for every type name and shape below; section 3 for every endpoint the page calls.

## Global Constraints

Verbatim from the spec (`2026-09-07-mt5-bridge-design.md`):

> Non-goals for this build: MT4; netting accounts; MT5→MT5 copying; copying
> pending-order *expiry* changes; MetaApi.

> **dashboard**
>
> - Accounts: *Add MT5 account* (admin) → nickname → one-time key dialog with the
>   five install steps and the download link. Platform badge on every row
>   (`cTrader` / `MT5`). MT5 rows: subtitle `MT5 · login · broker`, connection
>   badge `connected` (green) / `offline` (warn, with "since") / `waiting for the
>   terminal` (muted). Details drawer: *Terminal* section (broker, server,
>   currency, hedging, EA version, last seen, *Rotate key*) in place of the OAuth
>   section; *Symbol mapping* section listing canonical → broker name with an
>   inline edit and an "auto" / "manual" tag. Copy that hard-codes cTrader
>   ("One cTrader ID grant covers…", "revocable at ctrader.com") becomes
>   platform-aware.
> - Trade, Positions, History, Performance, Overview: no structural change;
>   labels that read `cTID {id}` show `login {login}` for MT5. The Layout caption
>   `FP Markets · cTrader` becomes the org's platform list.
> - `lib/types.ts`: `Account.platform?: 'ctrader' | 'mt5'`, `Account.mt5?: {...}`
>   (optional so existing fixtures stay valid).

> - **Security**: key checked before any other read; only sha256 stored; wrong key → 401 after the per-IP bucket; no key ever in a URL or log (`_redact` scrubs the `mt5_` prefix by value exactly like `tvw_`); the EA file contains no secrets; the api never accepts commands from the EA — the EA only reports and acknowledges.

> - **dashboard tests**: Accounts (add dialog, key once, badges, MT5 details, symbol mapping edit), Layout caption, History labels.

Verbatim from the contract, section 4:

> ```ts
> export interface Account {
>   // existing fields unchanged ...
>   platform?: 'ctrader' | 'mt5'          // absent = ctrader (older api)
>   mt5?: {
>     login: number | null; broker: string | null; server: string | null; currency: string | null
>     hedging: boolean | null; trade_mode: string | null; ea_version: string | null
>     last_seen_at: string | null; connected: boolean
>   } | null
> }
> export interface Mt5AccountCreated { account_id: number; key: string; download_url: string; install: string[] }
> export interface SymbolAliases { aliases: { canonical: string; broker_name: string; source: 'auto'|'manual' }[]; broker_symbols: string[] }
> ```

Project rules for this plan:

- **Repo root (Git Bash path):** `/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System`. Every command below is run from the Bash tool, quoted because of the space.
- **Dashboard test command (the only form used here):**
  `cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run <file>`
  A "write the failing test" step that cannot typecheck yet runs `npx vitest run <file>` alone and says so; every "run tests" step after an implementation runs the full form. `tsconfig.app.json` includes `src/test/` (only `*.test.ts(x)` is excluded), so shared fixtures under `src/test/` must typecheck.
- **Keep every existing test green.** `Account.platform` and `Account.mt5` are optional, so no existing fixture changes. cTrader rendering must stay byte-identical except where a task says otherwise (Task 7 removes a duplicated login from two labels that no test pins).
- **Tailwind vocabulary:** only classes already used in `Accounts.tsx` / `Layout.tsx` / `Overview.tsx`: `text-ink`, `text-ink-soft`, `text-ink-faint`, `bg-card`, `bg-paper`, `border-line`, `border-line-strong`, `desk-label`, `text-warn-deep`, `bg-warn-wash`, `border-warn/40`, `text-profit`, `text-profit-deep`, `bg-profit-wash`, `text-loss`, `text-loss-deep`, `bg-loss-wash`, `bg-brand`, `bg-brand-wash`, `text-brand`, `text-on-accent`, `bg-line`, `num`. Do not invent classes.
- **Endpoints (contract §3), called through `orgApi(orgId, tail, init)` from `lib/api.ts:88`:** `POST mt5/accounts` `{nickname}` → 201 `Mt5AccountCreated`; `POST mt5/accounts/{id}/key` → `{key}`; `GET accounts/{id}/symbol-aliases` → `SymbolAliases` (trader+); `PUT accounts/{id}/symbol-aliases` `{aliases: {canonical: broker_name}}` (admin; `""` removes); `GET accounts` rows carry `platform`, `mt5`, and `connection_status` ∈ `connected|offline|never` for MT5.
- **The key is shown once.** It is held only in the reveal dialog's state and cleared when the dialog closes; it is never written to storage, the URL, or a log line.
- **Role gating mirrors the server:** `can(role, 'control')` (admin/owner) for Add MT5 account, Rotate key and alias edits; `can(role, 'trade')` for reading the mapping.
- **Out of scope (do not add):** a "Remove account" action; Positions page labels; the Overview "Token refresh failed" org banner (only the slave tile marker changes); any change to `ConfirmDialog`.
- **Commits:** one per task, imperative subject, body when useful, ending with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Commit only the files the task names.

## File map

New:
- `dashboard/src/lib/platform.ts` — `isMt5`, `accountIdent`, `accountName`, `accountWho`, `platformCaption`
- `dashboard/src/lib/platform.test.ts`
- `dashboard/src/test/mt5Fixtures.ts` — the shared `mt5Account` fixture every test file below reuses

Modified:
- `dashboard/src/lib/types.ts` (Account, + `Mt5AccountCreated`, `SymbolAliases`)
- `dashboard/src/pages/Accounts.tsx`, `Accounts.test.tsx`
- `dashboard/src/components/Layout.tsx`, `Layout.test.tsx`
- `dashboard/src/pages/History.tsx`, `History.test.tsx`
- `dashboard/src/pages/Trade.tsx`, `Trade.test.tsx`
- `dashboard/src/pages/Performance.tsx`, `Performance.test.tsx`
- `dashboard/src/pages/Overview.tsx`, `Overview.test.tsx`

---

### Task 1: Types, platform helpers and the shared MT5 fixture

**Files:**
- Modify: `dashboard/src/lib/types.ts` (lines 30-44, the `Account` interface; append two interfaces after it)
- Create: `dashboard/src/lib/platform.ts`
- Create: `dashboard/src/test/mt5Fixtures.ts`
- Test: `dashboard/src/lib/platform.test.ts`

**Interfaces**

Consumes:
- `Account` from `dashboard/src/lib/types.ts` (fields `trader_login: number`, `nickname?: string | null`).

Produces (all in `dashboard/src/lib/platform.ts`):
- `isMt5(account: Pick<Account, 'platform'>): boolean`
- `accountIdent(account: Account): string` — `"12345"` | `"MT5 · login 555"` | `"MT5 · waiting for the terminal"`
- `accountName(account: Account): string` — nickname, else `"Account 12345"` / the MT5 ident
- `accountWho(account: Account): string` — `"<nickname> · <ident>"`, else `accountName`
- `platformCaption(accounts: Pick<Account, 'platform'>[]): string` — `"cTrader"` | `"MT5"` | `"cTrader · MT5"` (empty list → `"cTrader · MT5"`)
- `mt5Account: Account` in `dashboard/src/test/mt5Fixtures.ts` (id `1000000000001`, login `555`, nickname `'VPS desk'`, broker `'XYZ Ltd'`, `connection_status: 'connected'`)
- `Account.platform?`, `Account.mt5?`, `Mt5AccountCreated`, `SymbolAliases` exactly as the contract §4.

**Steps**

- [ ] **Step 1: Write the fixture and the failing test**

Create `dashboard/src/test/mt5Fixtures.ts`:

```ts
import type { Account } from '../lib/types'

/** An MT5 account whose terminal has already said hello -- the shape
 *  GET /api/orgs/{org}/accounts returns once the EA is connected. Spread
 *  over it for the offline / never-reported variants. */
export const mt5Account: Account = {
  ctid_trader_account_id: 1000000000001,
  trader_login: 555,
  is_live: true,
  role: 'slave',
  enabled: false,
  multiplier: 1.0,
  status: 'ok',
  last_error: null,
  connection_status: 'connected',
  nickname: 'VPS desk',
  cutoff_date: null,
  platform: 'mt5',
  mt5: {
    login: 555,
    broker: 'XYZ Ltd',
    server: 'XYZ-Live3',
    currency: 'USD',
    hedging: true,
    trade_mode: 'real',
    ea_version: '1.0.0',
    last_seen_at: '2026-09-07T10:00:00+00:00',
    connected: true,
  },
}
```

Create `dashboard/src/lib/platform.test.ts`:

```ts
import { expect, test } from 'vitest'
import { accountIdent, accountName, accountWho, isMt5, platformCaption } from './platform'
import { mt5Account } from '../test/mt5Fixtures'
import type { Account } from './types'

const ctrader: Account = {
  ctid_trader_account_id: 1, trader_login: 12345, is_live: false, role: 'master',
  enabled: true, multiplier: 1, status: 'ok', connection_status: 'active', nickname: null,
}

// Added a minute ago: no hello yet, so the api has no login to copy into
// trader_login and reports 0 there.
const fresh: Account = {
  ...mt5Account, trader_login: 0, connection_status: 'never',
  mt5: { ...mt5Account.mt5!, login: null, broker: null, server: null, connected: false, last_seen_at: null },
}

test('an account without a platform field is cTrader (older api)', () => {
  expect(isMt5(ctrader)).toBe(false)
  expect(isMt5({ platform: 'ctrader' })).toBe(false)
  expect(isMt5(mt5Account)).toBe(true)
})

test('the identifying figure is the trader login for cTrader, the MT5 login for MT5', () => {
  expect(accountIdent(ctrader)).toBe('12345')
  expect(accountIdent(mt5Account)).toBe('MT5 · login 555')
})

test('an MT5 account that has never reported says so rather than printing login 0', () => {
  expect(accountIdent(fresh)).toBe('MT5 · waiting for the terminal')
  expect(accountName({ ...fresh, nickname: null })).toBe('MT5 · waiting for the terminal')
})

test('accountName prefers the nickname, then the platform-aware ident', () => {
  expect(accountName({ ...ctrader, nickname: 'Main' })).toBe('Main')
  expect(accountName(ctrader)).toBe('Account 12345')
  expect(accountName({ ...mt5Account, nickname: null })).toBe('MT5 · login 555')
})

test('accountWho never repeats the login', () => {
  expect(accountWho({ ...ctrader, nickname: 'Second' })).toBe('Second · 12345')
  expect(accountWho(ctrader)).toBe('Account 12345')
  expect(accountWho(mt5Account)).toBe('VPS desk · MT5 · login 555')
  expect(accountWho({ ...mt5Account, nickname: null })).toBe('MT5 · login 555')
})

test('platformCaption lists the platforms present, cTrader first', () => {
  expect(platformCaption([ctrader])).toBe('cTrader')
  expect(platformCaption([mt5Account])).toBe('MT5')
  expect(platformCaption([mt5Account, ctrader])).toBe('cTrader · MT5')
  expect(platformCaption([])).toBe('cTrader · MT5')
})
```

- [ ] **Step 2: Run the test and watch it fail**

The fixture references `Account.platform`, which does not exist yet, so `tsc` would fail on the fixture before vitest ran; run vitest alone for this step:

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx vitest run src/lib/platform.test.ts
```

Expected: the file fails to load — `Error: Failed to resolve import "./platform" from "src/lib/platform.test.ts". Does the file exist?` — 0 passed.

- [ ] **Step 3: Add the types, the helpers**

In `dashboard/src/lib/types.ts`, replace lines 30-44:

```ts
export interface Account {
  ctid_trader_account_id: number
  trader_login: number
  is_live: boolean
  role: string
  enabled: boolean
  multiplier: number
  status: string
  last_error?: string | null
  connection_status: string
  nickname?: string | null
  // Admin-set one-time cutoff date (ISO YYYY-MM-DD); a reminder event fires
  // two days before it.
  cutoff_date?: string | null
}
```

with:

```ts
export interface Account {
  ctid_trader_account_id: number
  trader_login: number
  is_live: boolean
  role: string
  enabled: boolean
  multiplier: number
  status: string
  last_error?: string | null
  connection_status: string
  nickname?: string | null
  // Admin-set one-time cutoff date (ISO YYYY-MM-DD); a reminder event fires
  // two days before it.
  cutoff_date?: string | null
  /** Absent on an api that predates MT5: the account is cTrader. */
  platform?: 'ctrader' | 'mt5'
  /** The MT5 terminal link; null/absent for cTrader accounts. `connected`
   *  means a report arrived within the last 15 s. */
  mt5?: {
    login: number | null; broker: string | null; server: string | null; currency: string | null
    hedging: boolean | null; trade_mode: string | null; ea_version: string | null
    last_seen_at: string | null; connected: boolean
  } | null
}

/** Returned exactly once by POST .../mt5/accounts. Hold it only while the
 *  key dialog is open. */
export interface Mt5AccountCreated { account_id: number; key: string; download_url: string; install: string[] }

export interface SymbolAliases { aliases: { canonical: string; broker_name: string; source: 'auto' | 'manual' }[]; broker_symbols: string[] }
```

Create `dashboard/src/lib/platform.ts`:

```ts
import type { Account } from './types'

/** Absent `platform` means an api that predates MT5: the account is cTrader. */
export function isMt5(account: Pick<Account, 'platform'>): boolean {
  return account.platform === 'mt5'
}

/** The figure that identifies an account in a label. cTrader: the trader
 *  login. MT5: the terminal's login once it has reported one -- before the
 *  first hello there is none, and the row's trader_login of 0 must never be
 *  printed as if it were one. */
export function accountIdent(account: Account): string {
  if (!isMt5(account)) return String(account.trader_login)
  const login = account.mt5?.login
  return login ? `MT5 · login ${login}` : 'MT5 · waiting for the terminal'
}

/** The nickname, else the identifying figure: "Account 12345" for cTrader,
 *  "MT5 · login 555" for MT5. */
export function accountName(account: Account): string {
  if (account.nickname) return account.nickname
  return isMt5(account) ? accountIdent(account) : `Account ${account.trader_login}`
}

/** "<nickname> · <ident>", or just the name when there is no nickname --
 *  never the login twice ("Account 12345 · 12345"). */
export function accountWho(account: Account): string {
  return account.nickname ? `${account.nickname} · ${accountIdent(account)}` : accountName(account)
}

/** The platforms present in a fleet, cTrader first: "cTrader", "MT5" or
 *  "cTrader · MT5". An empty fleet names both, since either can be added. */
export function platformCaption(accounts: Pick<Account, 'platform'>[]): string {
  if (accounts.length === 0) return 'cTrader · MT5'
  const parts: string[] = []
  if (accounts.some((a) => !isMt5(a))) parts.push('cTrader')
  if (accounts.some((a) => isMt5(a))) parts.push('MT5')
  return parts.join(' · ')
}
```

- [ ] **Step 4: Typecheck and run the test**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/lib/platform.test.ts
```

Expected: tsc prints nothing; vitest `Test Files 1 passed (1)`, `Tests 6 passed (6)`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/lib/types.ts dashboard/src/lib/platform.ts dashboard/src/lib/platform.test.ts dashboard/src/test/mt5Fixtures.ts && git commit -m "feat(dashboard): an account knows which platform it is on" -m "Account gains the optional platform and mt5 fields the api now returns, plus the one-time Mt5AccountCreated and SymbolAliases shapes from the interface contract. lib/platform.ts turns them into labels: an MT5 account is named by its MT5 login, never by the cTID it does not have, and never as 'login 0' before its terminal has reported." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Accounts rows — platform badge, MT5 subtitle, connection badge, platform-aware copy

**Files:**
- Modify: `dashboard/src/pages/Accounts.tsx` (imports lines 5-7; helper after line 24; header copy lines 272-275; empty state lines 312-314; column header line 328; Account cell lines 338-340; Grant cell lines 444-452; Re-grant lines 462-470; Disconnect lines 503-511; new component after line 705)
- Test: `dashboard/src/pages/Accounts.test.tsx` (import after line 8; helper after line 146; three tests appended)

**Interfaces**

Consumes:
- `isMt5(account)` from Task 1; `mt5Account` fixture from Task 1.
- `formatWhen(ms: number | string | null | undefined): string` from `dashboard/src/lib/format.ts` (line 16).
- `Account.mt5`, `Account.connection_status` (`connected|offline|never` for MT5).
- `mockRoutes(overrides)`, `jsonResponse(payload, status)`, `renderAccounts()`, `mockAccounts` from `Accounts.test.tsx` (lines 55-146). `mockRoutes` matches override keys by substring in insertion order, so a more specific path must be listed before `/api/orgs/1/accounts`.

Produces (module-private in `Accounts.tsx`):
- `mt5Subtitle(link: Account['mt5']): string` — `"MT5 · login 555 · XYZ Ltd"`, `"MT5 · no login yet"` before hello
- `Mt5ConnectionBadge({ account }: { account: Account }): JSX.Element` — `Connected` (profit wash) / `Offline · last seen …` (warn wash) / `Waiting for the terminal` (muted)
- In `Accounts.test.tsx`: `mockMt5Routes(extra?)` — `mockRoutes` with the fleet `[...mockAccounts, mt5Account]`, `extra` listed first.

**Steps**

- [ ] **Step 1: Write the failing tests**

In `dashboard/src/pages/Accounts.test.tsx`, after line 8 (`import { mockUseOrg } from '../test/orgMock'`) add:

```ts
import { mt5Account } from '../test/mt5Fixtures'
```

After the `renderAccounts` function (line 146) add:

```ts
/** The fleet plus one connected MT5 account. `extra` is spread FIRST: the
 *  override loop matches by substring in insertion order, and
 *  '/api/orgs/1/accounts' would otherwise swallow '/accounts/…/details'. */
function mockMt5Routes(extra: Record<string, (init?: RequestInit) => Response> = {}) {
  return mockRoutes({
    ...extra,
    '/api/orgs/1/accounts': () => jsonResponse([...mockAccounts, mt5Account]),
  })
}
```

Append at the end of the file:

```ts
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

  const rows = await screen.findAllByRole('row')
  const mt5Row = rows.find((r) => r.textContent?.includes('XYZ Ltd'))!
  expect(within(mt5Row).queryByRole('button', { name: /re-grant access/i })).not.toBeInTheDocument()
  expect(within(mt5Row).queryByRole('button', { name: /disconnect/i })).not.toBeInTheDocument()
  // The per-account kill switch still applies: Close all covers MT5 too.
  expect(within(mt5Row).getByRole('button', { name: /^flatten$/i })).toBeInTheDocument()
  // cTrader rows keep both.
  expect(screen.getAllByRole('button', { name: /re-grant access/i })).toHaveLength(2)
})
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean (no source change yet); vitest: the three new tests fail — the first with `TestingLibraryElementError: Unable to find an element with the text: MT5`, the second with `Unable to find an element with the text: Connected`, the third at its first assertion with `expected <button …>Re-grant access</button> not to be in the document` (the MT5 row still renders Re-grant). `Tests 3 failed | 24 passed (27)`.

- [ ] **Step 3: Implement the row changes**

In `dashboard/src/pages/Accounts.tsx`, replace line 7:

```ts
import { money } from '../lib/format'
```

with:

```ts
import { money, formatWhen } from '../lib/format'
import { isMt5 } from '../lib/platform'
```

After line 24 (the closing `}` of `formatTimestamp`) add:

```ts
/** "MT5 · login 555 · XYZ Ltd" -- what an MT5 row prints under the login in
 *  place of the cTrader id. Before the first hello there is no login and no
 *  broker to print, and "login 0" would look like one. */
function mt5Subtitle(link: Account['mt5']): string {
  const parts = ['MT5', link?.login ? `login ${link.login}` : 'no login yet']
  if (link?.broker) parts.push(link.broker)
  return parts.join(' · ')
}
```

Replace the header copy (lines 272-275):

```tsx
          <p className="text-sm text-ink-soft mt-1">
            One cTrader ID grant covers every account under it. Roles,
            nicknames, and cutoff dates apply per account.
          </p>
```

with:

```tsx
          <p className="text-sm text-ink-soft mt-1">
            One cTrader ID grant covers every account under it; an MT5 account
            connects through the MirrorFleet EA in its own terminal. Roles,
            nicknames, and cutoff dates apply per account.
          </p>
```

Replace the empty-state hint (lines 312-314):

```tsx
          <p className="text-sm text-ink-faint mt-1">
            Connect a cTrader ID to discover its trading accounts.
          </p>
```

with:

```tsx
          <p className="text-sm text-ink-faint mt-1">
            Connect a cTrader ID to discover its trading accounts, or add an
            MT5 account and install the EA in its terminal.
          </p>
```

Replace the column header (line 328):

```tsx
                <th className="desk-label px-3 py-2.5 font-semibold">Grant</th>
```

with:

```tsx
                <th className="desk-label px-3 py-2.5 font-semibold">Connection</th>
```

Replace the start of the row body (lines 334-340):

```tsx
                const id = account.ctid_trader_account_id
                const isPending = pendingRows.has(id)
                return (
                  <tr key={id} className={`border-b border-line last:border-0 align-top ${isPending ? 'opacity-60' : ''}`}>
                    <td data-label="Account" className="px-5 py-3">
                      <div className="num text-ink">{account.trader_login}</div>
                      <div className="text-xs text-ink-faint">cTID {id}</div>
```

with:

```tsx
                const id = account.ctid_trader_account_id
                const isPending = pendingRows.has(id)
                const onMt5 = isMt5(account)
                return (
                  <tr key={id} className={`border-b border-line last:border-0 align-top ${isPending ? 'opacity-60' : ''}`}>
                    <td data-label="Account" className="px-5 py-3">
                      <div className="num text-ink">
                        {onMt5 ? (account.mt5?.login ?? '—') : account.trader_login}
                      </div>
                      <div className="text-xs text-ink-faint">
                        {onMt5 ? mt5Subtitle(account.mt5) : `cTID ${id}`}
                      </div>
                      <span className={`mt-1 inline-block text-xs font-semibold px-2 py-0.5 rounded ${
                        onMt5 ? 'bg-brand-wash text-ink' : 'bg-line text-ink-soft'
                      }`}>
                        {onMt5 ? 'MT5' : 'cTrader'}
                      </span>
```

Replace the Grant cell (lines 444-452):

```tsx
                    <td data-label="Grant" className="px-3 py-3">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                        account.connection_status === 'active'
                          ? 'bg-profit-wash text-profit-deep'
                          : 'bg-warn-wash text-warn-deep'
                      }`}>
                        {account.connection_status === 'active' ? 'Active' : account.connection_status}
                      </span>
                    </td>
```

with:

```tsx
                    <td data-label="Connection" className="px-3 py-3">
                      {onMt5 ? (
                        <Mt5ConnectionBadge account={account} />
                      ) : (
                        <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                          account.connection_status === 'active'
                            ? 'bg-profit-wash text-profit-deep'
                            : 'bg-warn-wash text-warn-deep'
                        }`}>
                          {account.connection_status === 'active' ? 'Active' : account.connection_status}
                        </span>
                      )}
                    </td>
```

Replace the Re-grant button guard (line 462):

```tsx
                        {can(role, 'control') && (
                          <button
                            onClick={handleConnectOAuth}
```

with:

```tsx
                        {can(role, 'control') && !onMt5 && (
                          <button
                            onClick={handleConnectOAuth}
```

Replace the Disconnect button guard (line 503):

```tsx
                        {can(role, 'control') && (
                          <button
                            onClick={() => setDisconnecting(account)}
```

with:

```tsx
                        {can(role, 'control') && !onMt5 && (
                          <button
                            onClick={() => setDisconnecting(account)}
```

After the `DetailRow` function (after line 705, end of file) add:

```tsx
/** The copier's view of the terminal, as the accounts row reports it:
 *  connected (a report within 15 s), offline (with when it was last heard
 *  from), or never reported -- the EA has not been installed yet. */
function Mt5ConnectionBadge({ account }: { account: Account }) {
  switch (account.connection_status) {
    case 'connected':
      return (
        <span className="text-xs font-medium px-2 py-0.5 rounded bg-profit-wash text-profit-deep">
          Connected
        </span>
      )
    case 'offline':
      return (
        <span className="text-xs font-medium px-2 py-0.5 rounded bg-warn-wash text-warn-deep">
          Offline · last seen {formatWhen(account.mt5?.last_seen_at)}
        </span>
      )
    default:
      return (
        <span className="text-xs font-medium px-2 py-0.5 rounded bg-line text-ink-soft">
          Waiting for the terminal
        </span>
      )
  }
}
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; `Tests 27 passed (27)`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/pages/Accounts.tsx dashboard/src/pages/Accounts.test.tsx && git commit -m "feat(accounts): rows say which platform they are on" -m "Every row carries a cTrader or MT5 badge. An MT5 row prints its MT5 login and broker where a cTrader row prints its cTID, and its Connection cell reads the terminal, not an OAuth grant: connected, offline since the last report, or waiting for the EA to be installed. Re-grant access and Disconnect are hidden on MT5 rows -- there is no grant behind them to renew or revoke." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Add MT5 account — nickname dialog, POST, one-time key dialog

**Files:**
- Modify: `dashboard/src/pages/Accounts.tsx` (import line 5; new interface after line 10; state after line 44; handlers after `handleDisconnect` (line 201); header buttons lines 277-284; two dialogs after the flatten `ConfirmDialog` (line 574))
- Test: `dashboard/src/pages/Accounts.test.tsx` (four tests appended)

**Interfaces**

Consumes:
- `POST /api/orgs/{org}/mt5/accounts` `{nickname: string}` → 201 `Mt5AccountCreated` (contract §3).
- `ConfirmDialog` props `open, title, children, confirmLabel, danger?, busy?, disabled?, onConfirm, onCancel` (`dashboard/src/components/ConfirmDialog.tsx` lines 3-18); it sets `aria-label={title}` on the `role="dialog"` element.
- `navigator.clipboard.writeText(text: string): Promise<void>`.
- `mockMt5Routes(extra)` from Task 2.

Produces (module-private in `Accounts.tsx`):
- `interface KeyReveal { title: string; key: string; download_url: string | null; install: string[] }`
- state `addingMt5: boolean`, `mt5Nickname: string`, `keyReveal: KeyReveal | null`, `copied: boolean`
- `handleAddMt5(): Promise<void>`, `copyKey(key: string): Promise<void>`, `closeKeyReveal(): void`
- UI: button `Add MT5 account`; dialog titled `Add an MT5 account` with input labelled `Nickname` and confirm `Create account` (disabled until the nickname is non-blank); dialog titled `MT5 account added — install the EA` showing the key in `<code aria-label="MT5 key">`, a `Copy` button (reads `Copied` for 2 s), a link `Download MirrorFleet.mq5` to `download_url`, an ordered list of `install` steps, and the close button `I have copied it — close`.

**Steps**

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/src/pages/Accounts.test.tsx`:

```ts
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

test('trader sees no Add MT5 account button', async () => {
  setRole('trader')
  mockRoutes()
  renderAccounts()

  await screen.findByText('12345')
  expect(screen.queryByRole('button', { name: /add mt5 account/i })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; the two flow tests fail with `Unable to find role="button" and name \`/add mt5 account/i\``; the two gating tests pass already (nothing renders the button yet) — that is fine, they guard the gate once the button exists. `Tests 2 failed | 26 passed (28)`.

- [ ] **Step 3: Implement the add flow**

In `dashboard/src/pages/Accounts.tsx`, replace line 5:

```ts
import type { Account, AccountDetails, ApiState, CloseAllResult, StateSnapshot } from '../lib/types'
```

with:

```ts
import type {
  Account, AccountDetails, ApiState, CloseAllResult, Mt5AccountCreated, StateSnapshot,
} from '../lib/types'
```

After line 10 (`const FLATTEN_DONE_MS = 5000`) add:

```ts
/** A key is shown exactly once. This is held only while its dialog is open
 *  and dropped the moment the dialog closes -- never stored anywhere else. */
interface KeyReveal {
  title: string
  key: string
  /** Present on creation; a rotation only replaces the key. */
  download_url: string | null
  install: string[]
}
```

After line 44 (`const [busy, setBusy] = useState(false)`) add:

```ts
  // Add MT5 account: nickname dialog → POST → one-time key dialog.
  const [addingMt5, setAddingMt5] = useState(false)
  const [mt5Nickname, setMt5Nickname] = useState('')
  const [keyReveal, setKeyReveal] = useState<KeyReveal | null>(null)
  const [copied, setCopied] = useState(false)
```

After `handleDisconnect` (after line 201, its closing `}`) add:

```ts
  const handleAddMt5 = async () => {
    try {
      setBusy(true)
      const result = await orgApi<Mt5AccountCreated>(orgId, 'mt5/accounts', {
        method: 'POST',
        body: JSON.stringify({ nickname: mt5Nickname.trim() }),
      })
      setAddingMt5(false)
      setMt5Nickname('')
      setKeyReveal({
        title: 'MT5 account added — install the EA',
        key: result.key,
        download_url: result.download_url,
        install: result.install,
      })
      await fetchAccounts()
    } catch (err) {
      setAddingMt5(false)
      setError(`Could not add the MT5 account (${err instanceof Error ? err.message : 'unknown'})`)
    } finally {
      setBusy(false)
    }
  }

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Could not copy — select the key and copy it by hand')
    }
  }

  const closeKeyReveal = () => {
    // The key leaves memory here; it is never shown again.
    setKeyReveal(null)
    setCopied(false)
  }
```

Replace the header button (lines 277-284):

```tsx
        {can(role, 'control') && (
          <button
            onClick={handleConnectOAuth}
            className="w-full md:w-auto shrink-0 px-4 py-2.5 bg-brand text-on-accent text-sm font-semibold rounded hover:bg-brand-deep transition-colors"
          >
            Connect cTrader ID
          </button>
        )}
```

with:

```tsx
        {can(role, 'control') && (
          <div className="flex flex-col gap-2 md:flex-row md:items-center shrink-0">
            <button
              onClick={() => setAddingMt5(true)}
              className="w-full md:w-auto px-4 py-2.5 text-sm font-semibold rounded border border-line-strong text-ink hover:border-ink transition-colors"
            >
              Add MT5 account
            </button>
            <button
              onClick={handleConnectOAuth}
              className="w-full md:w-auto px-4 py-2.5 bg-brand text-on-accent text-sm font-semibold rounded hover:bg-brand-deep transition-colors"
            >
              Connect cTrader ID
            </button>
          </div>
        )}
```

After the per-account kill switch `ConfirmDialog` (after line 574, its closing `</ConfirmDialog>`) add:

```tsx
      {/* Add MT5 account: the nickname is the only identifier the row has
          until its terminal connects, so it is required. */}
      <ConfirmDialog
        open={addingMt5}
        title="Add an MT5 account"
        confirmLabel="Create account"
        busy={busy}
        disabled={mt5Nickname.trim() === ''}
        onConfirm={handleAddMt5}
        onCancel={() => { setAddingMt5(false); setMt5Nickname('') }}
      >
        <p>
          The account starts as a disabled slave. You get a one-time key to
          paste into the MirrorFleet EA running in the account's own MT5
          terminal; the login, broker and symbols arrive when the EA first
          connects.
        </p>
        <div>
          <label className="desk-label block mb-1" htmlFor="mt5-nickname">Nickname</label>
          <input
            id="mt5-nickname"
            type="text"
            value={mt5Nickname}
            onChange={(e) => setMt5Nickname(e.target.value)}
            autoComplete="off"
            placeholder="e.g. VPS desk"
            className="w-full rounded border border-line-strong px-3 py-2 text-sm text-ink bg-card"
          />
        </div>
      </ConfirmDialog>

      {/* One-time key reveal. Confirm and cancel both just close it. */}
      <ConfirmDialog
        open={keyReveal != null}
        title={keyReveal?.title ?? ''}
        confirmLabel="I have copied it — close"
        onConfirm={closeKeyReveal}
        onCancel={closeKeyReveal}
      >
        <p>
          This key is shown <strong>once</strong>. Paste it into the EA's{' '}
          <span className="num">InpKey</span> input. If it is lost, rotate the
          key from the account's row — the old one stops working at once.
        </p>
        <div className="flex items-center gap-2">
          <code
            aria-label="MT5 key"
            className="num flex-1 break-all rounded border border-line-strong bg-paper px-3 py-2 text-xs text-ink"
          >
            {keyReveal?.key}
          </code>
          <button
            type="button"
            onClick={() => { if (keyReveal) copyKey(keyReveal.key) }}
            className="shrink-0 px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        {keyReveal?.download_url && (
          <a
            href={keyReveal.download_url}
            download
            className="inline-block text-sm font-medium text-brand hover:underline"
          >
            Download MirrorFleet.mq5
          </a>
        )}
        {keyReveal && keyReveal.install.length > 0 && (
          <ol className="list-decimal pl-5 space-y-1">
            {keyReveal.install.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        )}
      </ConfirmDialog>
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; `Tests 28 passed (28)`. The pre-existing `admin (control) sees editors…` test still finds `Connect cTrader ID` (the button keeps its text inside the new wrapper).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/pages/Accounts.tsx dashboard/src/pages/Accounts.test.tsx && git commit -m "feat(accounts): add an MT5 account and hand over its key once" -m "An admin names the account, the api creates it as a disabled slave and answers with the key, and the key is shown in one dialog with a Copy button, the EA download link and the five install steps. The dialog is the only place the key ever lives on the client; closing it drops it, and a lost key is replaced by rotation, never re-shown." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Rotate key on an MT5 row

**Files:**
- Modify: `dashboard/src/pages/Accounts.tsx` (import from `../lib/platform`; state after the Task 3 state block; handler after `closeKeyReveal`; row button after the Flatten button group; dialog after the key reveal dialog)
- Test: `dashboard/src/pages/Accounts.test.tsx` (two tests appended)

**Interfaces**

Consumes:
- `POST /api/orgs/{org}/mt5/accounts/{id}/key` → 200 `{key: string}` (contract §3; no body).
- `accountName(account: Account): string` from Task 1.
- `KeyReveal`, `setKeyReveal`, `busy` from Task 3.

Produces (module-private in `Accounts.tsx`):
- state `rotating: Account | null`
- `handleRotateKey(): Promise<void>`
- UI: row button `Rotate key` (MT5 rows, `can(role, 'control')` only; Task 5 adds a second one inside the drawer's Terminal section, where the spec places it, opening the same dialog); dialog titled `Rotate the key for <accountName>` with confirm `Rotate key`; on success the key reveal opens titled `New key for <accountName>` with no download link and no steps.

**Steps**

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/src/pages/Accounts.test.tsx`:

```ts
// ---------- Rotate key ----------

test('Rotate key confirms, POSTs the rotation, and shows the new key once', async () => {
  setRole('admin')
  const fetchMock = mockMt5Routes({
    'POST /api/orgs/1/mt5/accounts/1000000000001/key': () => jsonResponse({ key: 'mt5_rotated_key' }),
  })
  renderAccounts()

  await userEvent.click(await screen.findByRole('button', { name: /rotate key/i }))
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
  setRole('trader')
  mockMt5Routes()
  renderAccounts()

  await screen.findByText('MT5 · login 555 · XYZ Ltd')
  expect(screen.queryByRole('button', { name: /rotate key/i })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; the first new test fails with `Unable to find role="button" and name \`/rotate key/i\``; the second passes already. `Tests 1 failed | 29 passed (30)`.

- [ ] **Step 3: Implement rotation**

In `dashboard/src/pages/Accounts.tsx`, replace the platform import (added in Task 2):

```ts
import { isMt5 } from '../lib/platform'
```

with:

```ts
import { isMt5, accountName } from '../lib/platform'
```

After the Task 3 state block (after `const [copied, setCopied] = useState(false)`) add:

```ts
  const [rotating, setRotating] = useState<Account | null>(null)
```

After `closeKeyReveal` (its closing `}`) add:

```ts
  const handleRotateKey = async () => {
    if (!rotating) return
    const account = rotating
    try {
      setBusy(true)
      const result = await orgApi<{ key: string }>(
        orgId, `mt5/accounts/${account.ctid_trader_account_id}/key`, { method: 'POST' })
      setRotating(null)
      setKeyReveal({
        title: `New key for ${accountName(account)}`,
        key: result.key,
        download_url: null,
        install: [],
      })
    } catch (err) {
      setRotating(null)
      setError(`Could not rotate the key (${err instanceof Error ? err.message : 'unknown'})`)
    } finally {
      setBusy(false)
    }
  }
```

In the row actions, directly after the Flatten button group's closing `))}` (the line reading `                        ))}` that ends the `flattenStatus[id] === 'busy' ? … : …` expression) and before the Disconnect guard `{can(role, 'control') && !onMt5 && (`, add:

```tsx
                        {can(role, 'control') && onMt5 && (
                          <button
                            onClick={() => setRotating(account)}
                            disabled={isPending}
                            className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors disabled:opacity-50"
                          >
                            Rotate key
                          </button>
                        )}
```

After the one-time key reveal `ConfirmDialog` (Task 3) add:

```tsx
      {/* Rotate key: the old key dies the moment the new one exists. */}
      <ConfirmDialog
        open={rotating != null}
        title={`Rotate the key for ${rotating ? accountName(rotating) : ''}`}
        confirmLabel="Rotate key"
        danger
        busy={busy}
        onConfirm={handleRotateKey}
        onCancel={() => setRotating(null)}
      >
        <p>
          The current key stops working the moment the new one exists, so the
          running EA disconnects until you paste the new key into its{' '}
          <span className="num">InpKey</span> input and restart it. Positions,
          symbol mapping and history are untouched.
        </p>
      </ConfirmDialog>
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; `Tests 30 passed (30)`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/pages/Accounts.tsx dashboard/src/pages/Accounts.test.tsx && git commit -m "feat(accounts): rotate an MT5 account's key" -m "Rotate key replaces Re-grant access and Disconnect on an MT5 row: it confirms, because the running EA goes dark until the new key is pasted in, then shows the new key through the same one-time dialog as creation." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Details drawer — Terminal section and Symbol mapping editor

**Files:**
- Modify: `dashboard/src/pages/Accounts.tsx` (import `SymbolAliases`; state after `rotating`; `loadAliases`/`saveAlias`/`handleAliasBlur`/`handleAddAlias` before `openDetails`; `openDetails` lines 249-261 as they stood before Task 3's insertions; drawer subtitle lines 590-592; broker footnote lines 642-645; OAuth section lines 660-668)
- Test: `dashboard/src/pages/Accounts.test.tsx` (fixtures and three tests appended)

**Interfaces**

Consumes:
- `GET /api/orgs/{org}/accounts/{id}/symbol-aliases` → `SymbolAliases` (trader+); `PUT …/symbol-aliases` `{aliases: {canonical: broker_name}}` (admin) (contract §3).
- `GET /api/orgs/{org}/accounts/{id}/details` → `AccountDetails` (for MT5, the copier lane's details: `broker_name`, `balance`, `deposit_currency`, `leverage`, `open_positions`, `pending_orders`).
- `Account.mt5` (Terminal section reads the row's link block: broker, server, currency, hedging, trade_mode, ea_version, last_seen_at).
- `isMt5`, `mt5Subtitle`, `formatWhen`, `can`, `DetailRow({label, value, mono?})`.
- `setRotating` and the rotate-key confirm dialog from Task 4: the Terminal section's `Rotate key` reuses them for `detailsFor`.

Produces (module-private in `Accounts.tsx`):
- state `aliases: SymbolAliases | null`, `aliasesError: string | null`, `aliasDrafts: Record<string, string>`, `newAlias: { canonical: string; broker_name: string }`
- `loadAliases(accountId: number): Promise<void>`
- `saveAlias(accountId: number, canonical: string, brokerName: string): Promise<void>` — one PUT, then reload
- `handleAliasBlur(row: SymbolAliases['aliases'][number]): Promise<void>`
- `handleAddAlias(): void`
- UI: section heading `Terminal` (rows Broker, Server, Currency, Hedging, Trade mode, EA version, Last seen, then a `Rotate key` button for `can(role, 'control')` that calls `setRotating(detailsFor)`) in place of `OAuth grant` for MT5; section heading `Symbol mapping` listing `canonical` → input `aria-label="Broker symbol for <canonical>"` (admin) or text (trader), tag `auto`/`manual`; add row with inputs `New canonical symbol`, `New broker symbol`, button `Add mapping`; `<datalist id="mt5-broker-symbols">` of `broker_symbols`.

The add row exists because the spec's behaviour rule ("an intent for an unmatched symbol becomes an Alert naming the symbol and the Details panel where the mapping is set") needs a way to map a canonical name the auto-matcher produced no row for; it uses the same PUT body as the inline edit.

**Steps**

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/src/pages/Accounts.test.tsx`:

```ts
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
  // confirm dialog as the row button, stacked above the drawer.
  await userEvent.click(within(terminal).getByRole('button', { name: /rotate key/i }))
  expect(await screen.findByRole('dialog', { name: /rotate the key for vps desk/i })).toBeInTheDocument()
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
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; the three new tests fail — the first with `Unable to find role="heading" and name "Terminal"`, the other two with `Unable to find a label with the text of: Broker symbol for XAUUSD`. `Tests 3 failed | 30 passed (33)`.

- [ ] **Step 3: Implement the drawer sections**

In `dashboard/src/pages/Accounts.tsx`, replace the types import (as it stands after Task 3):

```ts
import type {
  Account, AccountDetails, ApiState, CloseAllResult, Mt5AccountCreated, StateSnapshot,
} from '../lib/types'
```

with:

```ts
import type {
  Account, AccountDetails, ApiState, CloseAllResult, Mt5AccountCreated, StateSnapshot,
  SymbolAliases,
} from '../lib/types'
```

After `const [rotating, setRotating] = useState<Account | null>(null)` add:

```ts
  // Symbol mapping for the MT5 account whose drawer is open.
  const [aliases, setAliases] = useState<SymbolAliases | null>(null)
  const [aliasesError, setAliasesError] = useState<string | null>(null)
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>({})
  const [newAlias, setNewAlias] = useState({ canonical: '', broker_name: '' })
```

Replace `openDetails` (the block that begins `const openDetails = async (account: Account) => {` and ends with its closing `}`):

```ts
  const openDetails = async (account: Account) => {
    setDetailsFor(account)
    setDetails(null)
    setDetailsError(null)
    try {
      setDetails(await orgApi<AccountDetails>(
        orgId, `accounts/${account.ctid_trader_account_id}/details`))
    } catch (err) {
      setDetailsError(
        `Could not fetch details: ${err instanceof Error ? err.message : 'unknown error'}. ` +
        'The copier may be offline or the account not yet authorized.')
    }
  }
```

with:

```ts
  const loadAliases = async (accountId: number) => {
    try {
      setAliases(await orgApi<SymbolAliases>(orgId, `accounts/${accountId}/symbol-aliases`))
      setAliasesError(null)
    } catch (err) {
      setAliasesError(
        `Could not load the symbol mapping (${err instanceof Error ? err.message : 'unknown'})`)
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
      setAliasesError(
        `Could not save the mapping for ${canonical} (${err instanceof Error ? err.message : 'unknown'})`)
    }
  }

  const handleAliasBlur = async (row: SymbolAliases['aliases'][number]) => {
    if (!detailsFor) return
    const draft = aliasDrafts[row.canonical]
    if (draft === undefined || draft.trim() === row.broker_name) return
    await saveAlias(detailsFor.ctid_trader_account_id, row.canonical, draft.trim())
    setAliasDrafts((prev) => {
      const next = { ...prev }
      delete next[row.canonical]
      return next
    })
  }

  const handleAddAlias = () => {
    if (!detailsFor) return
    // Canonical names are what master events carry: upper-case.
    const canonical = newAlias.canonical.trim().toUpperCase()
    const brokerName = newAlias.broker_name.trim()
    if (!canonical || !brokerName) return
    setNewAlias({ canonical: '', broker_name: '' })
    saveAlias(detailsFor.ctid_trader_account_id, canonical, brokerName)
  }

  const openDetails = async (account: Account) => {
    setDetailsFor(account)
    setDetails(null)
    setDetailsError(null)
    setAliases(null)
    setAliasesError(null)
    setAliasDrafts({})
    setNewAlias({ canonical: '', broker_name: '' })
    // The mapping lives in the api's database, so it loads even when the
    // copier is down; reading it needs the trader role.
    if (isMt5(account) && can(role, 'trade')) loadAliases(account.ctid_trader_account_id)
    try {
      setDetails(await orgApi<AccountDetails>(
        orgId, `accounts/${account.ctid_trader_account_id}/details`))
    } catch (err) {
      setDetailsError(
        `Could not fetch details: ${err instanceof Error ? err.message : 'unknown error'}. ` +
        (isMt5(account)
          ? 'The copier may be offline or the terminal has not connected yet.'
          : 'The copier may be offline or the account not yet authorized.'))
    }
  }
```

Replace the drawer subtitle:

```tsx
                <p className="num text-sm text-ink-soft mt-0.5">
                  {detailsFor.trader_login} · cTID {detailsFor.ctid_trader_account_id}
                </p>
```

with:

```tsx
                <p className="num text-sm text-ink-soft mt-0.5">
                  {isMt5(detailsFor)
                    ? mt5Subtitle(detailsFor.mt5)
                    : `${detailsFor.trader_login} · cTID ${detailsFor.ctid_trader_account_id}`}
                </p>
```

Replace the broker-profile footnote:

```tsx
                  <p className="mt-3 text-xs text-ink-faint">
                    The account holder's name and email are not exposed by the
                    cTrader Open API — set a nickname instead.
                  </p>
```

with:

```tsx
                  <p className="mt-3 text-xs text-ink-faint">
                    {isMt5(detailsFor)
                      ? 'The terminal reports only what MT5 exposes to an Expert Advisor — set a nickname for anything more.'
                      : 'The account holder\'s name and email are not exposed by the cTrader Open API — set a nickname instead.'}
                  </p>
```

Replace the OAuth grant section:

```tsx
                <section>
                  <h3 className="desk-label mb-2">OAuth grant</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Granted" value={formatDate(details.connection?.granted_at)} />
                    <DetailRow label="Token expires" value={formatDate(details.connection?.expires_at)} />
                    <DetailRow label="Grant status" value={details.connection?.status ?? '—'} />
                    <DetailRow label="Scope" value={details.connection?.scope ?? '—'} />
                  </dl>
                </section>
```

with:

```tsx
                {isMt5(detailsFor) ? (
                  <section>
                    <h3 className="desk-label mb-2">Terminal</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Broker" value={detailsFor.mt5?.broker ?? '—'} />
                      <DetailRow label="Server" value={detailsFor.mt5?.server ?? '—'} />
                      <DetailRow label="Currency" value={detailsFor.mt5?.currency ?? '—'} />
                      <DetailRow
                        label="Hedging"
                        value={detailsFor.mt5?.hedging == null
                          ? '—'
                          : detailsFor.mt5?.hedging ? 'Yes' : 'No — netting accounts are not supported'}
                      />
                      <DetailRow label="Trade mode" value={detailsFor.mt5?.trade_mode ?? '—'} />
                      <DetailRow label="EA version" value={detailsFor.mt5?.ea_version ?? '—'} mono />
                      <DetailRow label="Last seen" value={formatWhen(detailsFor.mt5?.last_seen_at)} mono />
                    </dl>
                    {/* The spec puts Rotate key here, beside the terminal it
                        cuts off; the row button opens the same dialog. */}
                    {can(role, 'control') && (
                      <button
                        type="button"
                        onClick={() => setRotating(detailsFor)}
                        className="mt-3 px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors"
                      >
                        Rotate key
                      </button>
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

                {isMt5(detailsFor) && (
                  <section>
                    <h3 className="desk-label mb-2">Symbol mapping</h3>
                    {!can(role, 'trade') ? (
                      <p className="text-sm text-ink-faint">Traders and admins can see the mapping.</p>
                    ) : aliasesError ? (
                      <p className="text-sm text-loss-deep">{aliasesError}</p>
                    ) : !aliases ? (
                      <p className="text-sm text-ink-faint">Loading the mapping…</p>
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
                                  {can(role, 'control') ? (
                                    <input
                                      type="text"
                                      aria-label={`Broker symbol for ${row.canonical}`}
                                      list="mt5-broker-symbols"
                                      value={aliasDrafts[row.canonical] ?? row.broker_name}
                                      onChange={(e) =>
                                        setAliasDrafts((prev) => ({ ...prev, [row.canonical]: e.target.value }))}
                                      onBlur={() => handleAliasBlur(row)}
                                      className="num w-28 rounded border border-transparent hover:border-line-strong focus:border-line-strong px-2 py-1 text-sm bg-transparent text-right"
                                    />
                                  ) : (
                                    <span className="num text-ink">{row.broker_name}</span>
                                  )}
                                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                                    row.source === 'manual' ? 'bg-brand-wash text-ink' : 'bg-line text-ink-soft'
                                  }`}>
                                    {row.source}
                                  </span>
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                        {can(role, 'control') && (
                          <div className="mt-3 flex items-center gap-2">
                            <input
                              type="text"
                              aria-label="New canonical symbol"
                              placeholder="XAUUSD"
                              value={newAlias.canonical}
                              onChange={(e) => setNewAlias((prev) => ({ ...prev, canonical: e.target.value }))}
                              className="num w-24 rounded border border-line-strong px-2 py-1 text-sm bg-card"
                            />
                            <span className="text-ink-faint">→</span>
                            <input
                              type="text"
                              aria-label="New broker symbol"
                              placeholder="GOLD.r"
                              list="mt5-broker-symbols"
                              value={newAlias.broker_name}
                              onChange={(e) => setNewAlias((prev) => ({ ...prev, broker_name: e.target.value }))}
                              className="num w-28 rounded border border-line-strong px-2 py-1 text-sm bg-card"
                            />
                            <button
                              type="button"
                              onClick={handleAddAlias}
                              disabled={!newAlias.canonical.trim() || !newAlias.broker_name.trim()}
                              className="px-2.5 py-1 text-xs font-medium rounded border border-line-strong text-ink-soft hover:text-ink hover:border-ink transition-colors disabled:opacity-50"
                            >
                              Add mapping
                            </button>
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
                )}
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Accounts.test.tsx
```

Expected: tsc clean; `Tests 33 passed (33)`. The pre-existing `details button opens the drawer with broker profile fields` test still finds `OAuth grant` for the cTrader account.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/pages/Accounts.tsx dashboard/src/pages/Accounts.test.tsx && git commit -m "feat(accounts): MT5 details show the terminal and its symbol mapping" -m "For an MT5 account the drawer's OAuth grant section becomes a Terminal section -- broker, server, currency, hedging, trade mode, EA version, last seen -- read from the link the accounts row already carries, with Rotate key beside them where the spec places it. A Symbol mapping section lists canonical to broker names with their auto/manual tag; an admin edits a broker name inline (one PUT on blur, then a re-read so the tag is the server's) or adds a mapping for a symbol the auto-matcher missed, which is where the copier's unmatched-symbol alert sends them." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Layout caption derived from the fleet's platforms

**Files:**
- Modify: `dashboard/src/components/Layout.tsx` (import after line 12; `DeskStrip` signature line 61; `refresh` lines 82-93 and its deps line 135; `Layout` state after line 381; caption line 423; `<DeskStrip />` line 512)
- Test: `dashboard/src/components/Layout.test.tsx` (import after line 6; two tests appended)

**Interfaces**

Consumes:
- `platformCaption(accounts: Pick<Account, 'platform'>[]): string` from Task 1; `mt5Account` fixture.
- `DeskStrip`'s existing `refresh` (already fetches `orgApi<Account[]>(orgId, 'accounts')` every 10 s).
- `mockRoutes({ accounts })`, `renderLayout()`, `makeOrgValue(role)`, `accounts` from `Layout.test.tsx` (lines 40-104).

Produces:
- `DeskStrip({ onAccounts }: { onAccounts?: (accounts: Account[]) => void })` — calls `onAccounts` with every accounts fetch.
- In `Layout`: state `caption: string | null` (null until the first fetch; the `<p className="desk-label mt-1">` is rendered only when set) and `handleAccounts = useCallback((list: Account[]) => setCaption(platformCaption(list)), [])`.

**Steps**

- [ ] **Step 1: Write the failing tests**

In `dashboard/src/components/Layout.test.tsx`, after line 6 (`import type { Role } from '../lib/roles'`) add:

```ts
import { mt5Account } from '../test/mt5Fixtures'
```

Append at the end of the file:

```ts
test('the sidebar caption names the platforms the org has connected', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes({ accounts: [...accounts, mt5Account] })
  renderLayout()

  expect(await screen.findByText('cTrader · MT5')).toBeInTheDocument()
  expect(screen.queryByText('FP Markets · cTrader')).not.toBeInTheDocument()
})

test('a single-platform org gets a single-platform caption', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('owner'))
  mockRoutes()
  const view = renderLayout()
  expect(await screen.findByText('cTrader')).toBeInTheDocument()
  view.unmount()

  mockRoutes({ accounts: [mt5Account] })
  renderLayout()
  expect(await screen.findByText('MT5')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/components/Layout.test.tsx
```

Expected: tsc clean; both new tests fail with `Unable to find an element with the text: cTrader · MT5` / `…text: cTrader` (the caption is still the hard-coded `FP Markets · cTrader`). `Tests 2 failed | 25 passed (27)`.

- [ ] **Step 3: Implement the derived caption**

In `dashboard/src/components/Layout.tsx`, after line 12 (`import { money, signed, errorText } from '../lib/format'`) add:

```ts
import { platformCaption } from '../lib/platform'
```

Replace line 61:

```tsx
function DeskStrip() {
```

with:

```tsx
/** `onAccounts` hands the fleet the strip already polls to whoever needs
 *  it -- the sidebar caption -- so the platform list costs no extra fetch. */
function DeskStrip({ onAccounts }: { onAccounts?: (accounts: Account[]) => void }) {
```

Replace lines 87-88:

```ts
      setSettings(sett)
      const master = accounts.find((a) => a.role === 'master')
```

with:

```ts
      setSettings(sett)
      onAccounts?.(accounts)
      const master = accounts.find((a) => a.role === 'master')
```

Replace line 135:

```ts
  }, [orgId, role])
```

with:

```ts
  }, [orgId, role, onAccounts])
```

After line 381 (`const [menuOpen, setMenuOpen] = useState(false)`) add:

```ts
  // "cTrader", "MT5" or "cTrader · MT5" -- null until the first fetch, so
  // the sidebar never claims a platform list it has not seen.
  const [caption, setCaption] = useState<string | null>(null)
  const handleAccounts = useCallback(
    (list: Account[]) => setCaption(platformCaption(list)), [])
```

Replace line 423:

```tsx
        <p className="desk-label mt-1">FP Markets · cTrader</p>
```

with:

```tsx
        {caption && <p className="desk-label mt-1">{caption}</p>}
```

Replace line 512:

```tsx
        <DeskStrip />
```

with:

```tsx
        <DeskStrip onAccounts={handleAccounts} />
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/components/Layout.test.tsx
```

Expected: tsc clean; `Tests 27 passed (27)`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/components/Layout.tsx dashboard/src/components/Layout.test.tsx && git commit -m "feat(layout): the sidebar caption names the platforms in play" -m "'FP Markets · cTrader' was a hard-coded fact about one desk. The caption is now derived from the accounts the desk strip already polls: cTrader, MT5, or both." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: History, Trade and Performance name MT5 accounts by login

**Files:**
- Modify: `dashboard/src/pages/History.tsx` (lines 30-35), `dashboard/src/pages/Trade.tsx` (lines 58-62), `dashboard/src/pages/Performance.tsx` (lines 12-15) — plus one import line in each
- Test: `dashboard/src/pages/History.test.tsx`, `dashboard/src/pages/Trade.test.tsx`, `dashboard/src/pages/Performance.test.tsx` (one test appended to each)

**Interfaces**

Consumes:
- `accountWho(account: Account): string` from Task 1; `mt5Account` fixture.
- Each test file's `mockRoutes()` (History lines 70-105, Trade lines 75-125, Performance lines 51-64) returns the `vi.fn` fetch; the new tests wrap it so that only the exact list URL `/api/orgs/1/accounts` is answered with the MT5 fleet (those `mockRoutes` match overrides by substring, so `/api/orgs/1/accounts` as an override key would also swallow `/accounts/100/history/deals`).

Produces:
- `accountLabel` in each page renders `"<accountWho> · <Live|Demo>"` (History, Performance) and `"<accountWho> · <Live|Demo> (<role>)"` (Trade). cTrader with nickname: unchanged. cTrader without nickname on Trade/Performance loses the duplicated login (`"Account 90101 · 90101 · Demo (slave)"` → `"Account 90101 · Demo (slave)"`), which History already did; no test pins the old string.

**Steps**

- [ ] **Step 1: Write the failing tests**

In `dashboard/src/pages/History.test.tsx`, after line 6 (`import { mockUseOrg } from '../test/orgMock'`) add:

```ts
import { mt5Account } from '../test/mt5Fixtures'
```

Append at the end of the file:

```ts
test('an MT5 account is labelled by its MT5 login, never a cTID', async () => {
  const base = mockRoutes()
  const json = (payload: unknown) =>
    new Response(JSON.stringify(payload), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
    String(input) === '/api/orgs/1/accounts' ? json([...accounts, mt5Account]) : base(input)))
  renderHistory()
  await screen.findByText('21')

  expect(screen.getByRole('option', { name: 'VPS desk · MT5 · login 555 · Live' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: /1000000000001/ })).not.toBeInTheDocument()
})
```

In `dashboard/src/pages/Trade.test.tsx`, after line 8 (`import { mockUseOrg } from '../test/orgMock'`) add:

```ts
import { mt5Account } from '../test/mt5Fixtures'
```

Append at the end of the file:

```ts
test('an MT5 account is labelled by its MT5 login in the account picker', async () => {
  setRole('trader')
  const base = mockRoutes()
  const json = (payload: unknown) =>
    new Response(JSON.stringify(payload), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
    String(input) === '/api/orgs/1/accounts' ? json([...accounts, mt5Account]) : base(input)))
  renderTrade()

  const select = await screen.findByLabelText(/account/i)
  expect(within(select).getByRole('option', { name: 'VPS desk · MT5 · login 555 · Live (slave)' }))
    .toBeInTheDocument()
})
```

In `dashboard/src/pages/Performance.test.tsx`, after line 6 (`import { mockUseOrg } from '../test/orgMock'`) add:

```ts
import { mt5Account } from '../test/mt5Fixtures'
```

Append at the end of the file:

```ts
test('an MT5 account is labelled by its MT5 login in the account picker', async () => {
  const base = mockRoutes()
  const json = (payload: unknown) =>
    new Response(JSON.stringify(payload), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
    String(input) === '/api/orgs/1/accounts' ? json([...accounts, mt5Account]) : base(input)))
  renderPage()

  await screen.findByText('+116.70')
  expect(screen.getByRole('option', { name: 'VPS desk · MT5 · login 555 · Live' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/History.test.tsx src/pages/Trade.test.tsx src/pages/Performance.test.tsx
```

Expected: tsc clean; exactly the three new tests fail, each with `Unable to find role="option" and name "VPS desk · MT5 · login 555 · …"` (the pages still print `VPS desk · 555 · Live…`).

- [ ] **Step 3: Implement the labels**

In `dashboard/src/pages/History.tsx`, after line 5 (`import { money, signed, formatWhen, errorText } from '../lib/format'`) add:

```ts
import { accountWho } from '../lib/platform'
```

Replace lines 30-35:

```ts
function accountLabel(account: Account): string {
  const env = account.is_live ? 'Live' : 'Demo'
  return account.nickname
    ? `${account.nickname} · ${account.trader_login} · ${env}`
    : `Account ${account.trader_login} · ${env}`
}
```

with:

```ts
function accountLabel(account: Account): string {
  return `${accountWho(account)} · ${account.is_live ? 'Live' : 'Demo'}`
}
```

In `dashboard/src/pages/Trade.tsx`, after line 6 (`import { can } from '../lib/roles'`) add:

```ts
import { accountWho } from '../lib/platform'
```

Replace lines 58-62:

```ts
function accountLabel(account: Account): string {
  const name = account.nickname || `Account ${account.trader_login}`
  const env = account.is_live ? 'Live' : 'Demo'
  return `${name} · ${account.trader_login} · ${env} (${account.role})`
}
```

with:

```ts
function accountLabel(account: Account): string {
  const env = account.is_live ? 'Live' : 'Demo'
  return `${accountWho(account)} · ${env} (${account.role})`
}
```

In `dashboard/src/pages/Performance.tsx`, after line 4 (`import type { Account, Analytics } from '../lib/types'`) add:

```ts
import { accountWho } from '../lib/platform'
```

Replace lines 12-15:

```ts
function accountLabel(account: Account): string {
  const name = account.nickname || `Account ${account.trader_login}`
  return `${name} · ${account.trader_login} · ${account.is_live ? 'Live' : 'Demo'}`
}
```

with:

```ts
function accountLabel(account: Account): string {
  return `${accountWho(account)} · ${account.is_live ? 'Live' : 'Demo'}`
}
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/History.test.tsx src/pages/Trade.test.tsx src/pages/Performance.test.tsx
```

Expected: tsc clean; all three files pass, including History's `the account label does not repeat the login when there is no nickname` (`Account 90100 · Demo`) and every `/Second · 90101/` assertion.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/pages/History.tsx dashboard/src/pages/History.test.tsx dashboard/src/pages/Trade.tsx dashboard/src/pages/Trade.test.tsx dashboard/src/pages/Performance.tsx dashboard/src/pages/Performance.test.tsx && git commit -m "feat(dashboard): account pickers name MT5 accounts by login" -m "History, Trade and Performance build their account labels from one shared accountWho(): nickname plus the platform's own login. An MT5 account reads 'MT5 · login 555', never its synthetic id and never 'login 0' before its terminal has reported. Trade and Performance also stop repeating the login for a cTrader account without a nickname, as History already did." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Overview slave tile — an offline terminal warns like a failed refresh

**Files:**
- Modify: `dashboard/src/pages/Overview.tsx` (line 777; lines 793-795; lines 824-832)
- Test: `dashboard/src/pages/Overview.test.tsx` (import after line 8; one test appended)

**Interfaces**

Consumes:
- `Account.connection_status === 'offline'` (MT5, contract §3); `StatusDot({ tone: 'warn' })`; `mt5Account` fixture; `stubApi`, `mockAccounts`, `mockSettings`, `mockState`, `setRole` from `Overview.test.tsx` (lines 13-134).

Produces:
- In the slave tile: `isOffline = slave.connection_status === 'offline'`; the tile border is `border-warn` when `isRefreshFailed || isOffline`; a marker `data-testid="slave-offline-marker"` reading `Terminal offline — copies wait until the EA reports again`, styled exactly like `slave-refresh-failed-marker`.

**Steps**

- [ ] **Step 1: Write the failing test**

In `dashboard/src/pages/Overview.test.tsx`, after line 8 (`import { mockUseOrg } from '../test/orgMock'`) add:

```ts
import { mt5Account } from '../test/mt5Fixtures'
```

Append at the end of the file:

```ts
test('an MT5 slave whose terminal is offline gets the warn marker, like a failed token refresh', async () => {
  setRole('owner')
  const accounts: Account[] = [
    mockAccounts[0],
    { ...mt5Account, connection_status: 'offline', mt5: { ...mt5Account.mt5!, connected: false } },
  ]
  stubApi({
    '/api/orgs/1/accounts': accounts,
    '/api/orgs/1/settings': mockSettings,
    '/api/orgs/1/state': mockState,
  })

  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>
  )

  const marker = await screen.findByTestId('slave-offline-marker')
  expect(marker).toHaveTextContent(/terminal offline/i)
  // It is a connection problem, not a send failure: no Degraded, and the
  // cTrader token banner stays down.
  expect(screen.queryByText('Degraded')).not.toBeInTheDocument()
  expect(screen.queryByTestId('refresh-failed-banner')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run the test and watch it fail**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Overview.test.tsx
```

Expected: tsc clean; the new test fails with `Unable to find an element by: [data-testid="slave-offline-marker"]`; every other test passes.

- [ ] **Step 3: Implement the marker**

In `dashboard/src/pages/Overview.tsx`, replace line 777:

```ts
              const isRefreshFailed = slave.connection_status === 'refresh_failed'
```

with:

```ts
              const isRefreshFailed = slave.connection_status === 'refresh_failed'
              // An MT5 terminal that has stopped reporting: copies queue and
              // market opens expire after 30 s, so it is the same class of
              // problem as a failed token refresh.
              const isOffline = slave.connection_status === 'offline'
```

Replace lines 793-795:

```tsx
                  className={`bg-card p-5 rounded-lg border transition-colors hover:border-line-strong ${
                    isRefreshFailed ? 'border-warn' : 'border-line'
                  }`}
```

with:

```tsx
                  className={`bg-card p-5 rounded-lg border transition-colors hover:border-line-strong ${
                    isRefreshFailed || isOffline ? 'border-warn' : 'border-line'
                  }`}
```

Replace lines 824-832:

```tsx
                  {/* Refresh-failed marker - distinct from the degraded badge above */}
                  {isRefreshFailed && (
                    <div
                      data-testid="slave-refresh-failed-marker"
                      className="mb-4 px-3 py-2 bg-warn-wash border border-warn/40 text-warn-deep text-xs font-semibold rounded flex items-center gap-1.5"
                    >
                      <StatusDot tone="warn" /> Token refresh failed — reconnect required
                    </div>
                  )}
```

with:

```tsx
                  {/* Connection markers - distinct from the degraded badge above */}
                  {isRefreshFailed && (
                    <div
                      data-testid="slave-refresh-failed-marker"
                      className="mb-4 px-3 py-2 bg-warn-wash border border-warn/40 text-warn-deep text-xs font-semibold rounded flex items-center gap-1.5"
                    >
                      <StatusDot tone="warn" /> Token refresh failed — reconnect required
                    </div>
                  )}
                  {isOffline && (
                    <div
                      data-testid="slave-offline-marker"
                      className="mb-4 px-3 py-2 bg-warn-wash border border-warn/40 text-warn-deep text-xs font-semibold rounded flex items-center gap-1.5"
                    >
                      <StatusDot tone="warn" /> Terminal offline — copies wait until the EA reports again
                    </div>
                  )}
```

- [ ] **Step 4: Typecheck and run the tests**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run src/pages/Overview.test.tsx
```

Expected: tsc clean; every Overview test passes, including `slave tile shows a refresh-failed marker distinct from degraded styling`.

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git add dashboard/src/pages/Overview.tsx dashboard/src/pages/Overview.test.tsx && git commit -m "feat(overview): an offline MT5 terminal warns like a failed refresh" -m "A slave whose terminal has gone quiet is not degraded -- nothing was sent and refused -- but copying to it has stopped all the same, and its market opens will expire in 30 s. The tile shows the same warn marker a failed token refresh gets, worded for the terminal." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Whole-suite verification

**Files:**
- No changes. Read-only verification of every file above.

**Interfaces**

Consumes: everything produced by Tasks 1-8. Produces: nothing — evidence only.

**Steps**

- [ ] **Step 1: Typecheck and run the entire dashboard suite**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System/dashboard" && npx tsc -p tsconfig.app.json --noEmit && npx vitest run
```

Expected: tsc clean; every test file passes (`Test Files N passed (N)`, `Tests M passed (M)`, no `failed`, no `skipped` beyond what the suite had before this plan). If anything fails, fix it inside the task that owns the file and amend nothing: make a new commit on that task's files.

- [ ] **Step 2: Confirm the key never leaks**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && grep -n "localStorage\|sessionStorage\|console\." dashboard/src/pages/Accounts.tsx
```

Expected: no output — the Accounts page neither stores nor logs anything, so the one-time key lives only in `keyReveal` state.

- [ ] **Step 3: Confirm the working tree is clean**

```bash
cd "/c/Users/Sherwyn joel/OneDrive/Desktop/Forex-Automated-Copy-Trading-System" && git status --short && git log --oneline -8
```

Expected: `git status --short` prints nothing; the log shows the eight commits from Tasks 1-8 on top of `cb09f65`.
