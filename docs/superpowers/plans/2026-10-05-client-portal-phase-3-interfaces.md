# Client portal phase 3 — interfaces and task map

Companion to `docs/superpowers/plans/2026-10-05-client-portal-phase-3.md`. Every task in
the plan consumes and produces exactly the names below. An implementer may add private
helpers, never rename or reshape anything here. Spec (binding):
`docs/superpowers/specs/2026-10-05-client-portal-phase-3-multi-account-design.md`.

## Global constraints (copied into the plan header)

- Branch `client-portal-phase-3` in the worktree `.worktrees/phase3`; never `cd` to the
  main checkout, never `git stash`. Commit after every task; every commit ends with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN`.
  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`.
- `copier/` is never touched.
- API tests from the worktree's `api/`: `PYTHONPATH=$(pwd -W)/src`, the main checkout's
  `api/.venv/Scripts/python.exe`, `TEST_POSTGRES_ADMIN_DSN=…@127.0.0.1:5433/copytrader`,
  `TEST_POSTGRES_DSN=…@127.0.0.1:5433/copytrader_test_p3` (never `localhost`). Seven
  `test_events_ws.py` errors and one EA-download CRLF failure are pre-existing.
- Dashboard: `npm ci` once; one file `npx vitest run <path>`; gate `npm test` and
  `npm run build`; zero `act(...)` warnings.
- Investor routes `Depends(require_investor)`; admin routes
  `Depends(require_org_role("admin"))`; every new org route has an RBAC matrix row (Task 8).
- Mutations audit through `pc.audit_control` (`payload.user_id` = the investor, the
  account in the events `account_id` column). Ledger transactions take
  `pc.lock_investor_ledger` first and await nothing while it is held.

## Task map

| # | Task | Produces for later tasks |
|---|---|---|
| 1 | Migration `024_multi_account.sql`, `test_migration_024.py`; 019 test retired; 022 columns | schema |
| 2 | `portal_common` helpers and `link_account`; `portal_settings` returns `max_live_accounts` | every `pc.*` name below |
| 3 | Read routes `?account_id=`; transfer and deposit-notice ownership; `test_portal_multi_account.py` | test helpers `READS`, `W`, `A`, `_q`, `_live`, `_copier`, fixture `two` |
| 4 | Deposit confirmation funds the named, still-owned account | |
| 5 | Cap on `portal-settings`, account requests, fulfil; `_link_account` deleted | `_set_cap`, `_cancel_open` (account-requests tests) |
| 6 | `POST/DELETE investors/{id}/accounts`; `investors` rows carry `accounts[]`; PUT removed | |
| 7 | Summary `accounts[]`, totals, `account_limit`; `pc.linked_account` deleted | complete API |
| 8 | RBAC rows; full API suite | |
| 9 | Dashboard types (additive), `accountName`, `pickAccount`, fixtures | primitives |
| 10 | Live accounts tray | |
| 11 | `AccountSwitcher`; Account and History pages | |
| 12 | Transfer and Deposit account choices | |
| 13 | Open account cap state | |
| 14 | Investors accounts column, `AccountsDrawer`, cap field | |
| 15 | Transitional types removed; gates; README; spec status | |

## Database (Task 1) — `db/migrations/024_multi_account.sql`

- `DROP INDEX accounts_one_per_investor;`
- `CREATE INDEX accounts_by_investor ON accounts (org_id, investor_user_id) WHERE investor_user_id IS NOT NULL;`
- `ALTER TABLE portal_settings ADD COLUMN max_live_accounts INTEGER NOT NULL DEFAULT 5 CHECK (max_live_accounts BETWEEN 1 AND 50);`
  (last column of `portal_settings`).

No new table, so the conftest TRUNCATE list is unchanged.

## Python — `api/src/api/portal_common.py` (Task 2; `linked_account` deleted in Task 7)

```python
def linked_accounts(conn, org_id: int, user_id: int) -> list[int]          # ordered by account id
def owns_account(conn, org_id: int, user_id: int, account_id: Optional[int]) -> bool
def pick_account(conn, org_id: int, user_id: int, account_id: Optional[int]) -> int
    # given: 404 "Account not found" unless owned; omitted: the only one,
    # 409 "no account linked yet" with none, 400 "account_id is required" with several
def accounts_used(conn, org_id: int, user_id: int) -> int                  # owned + 'requested' account_requests
def account_limit_text(limit: int) -> str   # "you have reached the limit of {limit} live accounts"
def link_account(conn, org_id: int, user_id: int, account_id: int, *, mt5_only: bool = False) -> None
    # inside the caller's transaction; already this investor's: no-op;
    # 400 "The master account cannot be linked to an investor"
    # 400 "Only an MT5 account can be linked here" (mt5_only=True: fulfil only)
    # 409 account_limit_text(max) when the investor already owns max accounts
    # 404 "Account not found in this workspace, or already linked"
def portal_settings(conn, org_id: int) -> dict
    # {"withdrawal_min": Decimal, "withdrawal_fee_pct": Decimal, "max_live_accounts": int}
```

`routes/portal_investor.py` gains `EQUITY_RANK = {"live": 0, "last known": 1, "unknown": 2}`
and loses the nested `_require_linked`. `routes/portal_identity.py` loses `_link_account`.
`routes/portal_admin.py`: `LinkBody {account_id: int}`, `SettingsBody` gains
`max_live_accounts: Any = None`.

## Routes (under `/api/orgs/{org_id}`)

| Route | Task | Contract |
|---|---|---|
| `GET investor/positions`, `GET investor/analytics`, `GET investor/history/{kind}` | 3 | optional `account_id` query through `pc.pick_account`; `kind` is validated first |
| `POST investor/transfers` | 3 | an account end must be owned: 404 `Account not found`; 409 `no account linked yet` only when the investor owns none |
| `POST investor/deposits` | 3 | `target=account`: none owned 409 `no account linked yet`; `target_account_id` omitted with several 400 `target_account_id is required`; not owned 404 `Account not found`; omitted with one = that one |
| `POST deposits/{id}/decision` | 4 | confirmed + `target=account`: transfer to `target_account_id` if still owned, else main only with note suffix `(no account linked; credited to wallet)` |
| `GET/PUT portal-settings` | 5 | `{withdrawal_min, withdrawal_fee_pct, max_live_accounts}`; PUT `max_live_accounts` optional, else int 1-50 or 400 `max_live_accounts must be a whole number from 1 to 50`; audit `portal_settings_changed` |
| `POST investor/account-requests` | 5 | order: MPIN, KYC 409 `verify your identity first`, 409 `a request is already open`, 409 `account_limit_text(max)` when `accounts_used >= max`; `you already have a trading account` is gone |
| `POST account-requests/{id}/fulfil` | 5 | optional `account_id` linked through `pc.link_account` (adds; cap; the open request is not counted) |
| `GET investors` | 6 | rows: `user_id, email, display_name, joined_at, accounts: [{account_id, nickname, equity, equity_source}], balances, on_hold, available, pending, kyc_status`; one `/state` call |
| `POST investors/{user_id}/accounts` | 6 | body `{account_id: int}`; 404 `Investor not found`; `pc.link_account` refusals; 201 `{user_id, account_id}`; audit `investor_account_linked` |
| `DELETE investors/{user_id}/accounts/{account_id}` | 6 | 404 `Investor not found`; 404 `Account not found` unless that investor owns it; 204; audit `investor_account_unlinked` |
| `PUT investors/{user_id}/account` | 6 | removed |
| `GET investor/summary` | 7 | `link_state`, `account`, `account_available` removed; `accounts: AccountSummary[]`; totals `equity` (null if any unknown or none), `equity_source` (worst, `unknown` with none), `net_funded`, `profit`, `open_positions`; `account_limit: {max, used}`; one `/state` call |

`AccountSummary` = `{account_id, nickname, platform, status, last_error, connected,
mt5_login, mt5_server, equity_source, equity, net_funded, profit, account_available,
open_positions}`; `mt5_login`/`mt5_server` from the latest-decided fulfilled
`account_requests` row naming the account, else null.

## Test helpers

- `api/tests/test_portal_multi_account.py` (Task 3): `ADMIN`, `READS`, `W`, `A(account_id)`,
  `_q(tail, account_id)`, `_live(equity, positions=())`, `_copier(client, accounts=None, down=False) -> list[str]`,
  fixture `two` -> `(client, org_id, investor)` (investor owns 1001 and 1002, another investor 1003,
  1000 in main, logged in); `_events(db, org_id, actions)` (Task 6).
- `api/tests/test_portal_account_requests.py` (Task 5): `_set_cap(db, org_id, cap)`, `_cancel_open(db, user_id)`.

## Dashboard

### `src/lib/types.ts` (Task 9; old fields removed in Task 15)

```ts
export type EquitySource = 'live' | 'last known' | 'unknown'
export interface AccountSummary { account_id: number; nickname: string | null; platform: string; status: string; last_error: string | null; connected: boolean; mt5_login: number | null; mt5_server: string | null; equity_source: EquitySource; equity: number | null; net_funded: number; profit: number | null; account_available: number | null; open_positions: number }
export interface InvestorAccount { account_id: number; nickname: string | null; equity: number | null; equity_source: EquitySource }
InvestorSummary += accounts: AccountSummary[]; account_limit: { max: number; used: number }
                -= link_state, account, account_available (Task 15)
InvestorRow     += accounts: InvestorAccount[]
                -= account_id, nickname, equity, equity_source (Task 15)
PortalSettings  += max_live_accounts: number
```

### `src/lib/investor.ts` (Task 9)

```ts
export function accountName(a: { account_id: number; nickname: string | null; mt5_login?: number | null }): string
    // "MT5 <login>" | nickname | "Account <id>"
export function pickAccount<T extends { account_id: number }>(accounts: T[], wanted: string | null): T | null
```

### `src/test/portalFixtures.ts` (Task 9)

`accountSummaryFixture(overrides)` (account 555 "Growth", MT5 login 5001 on "Broker-Live",
equity 1240.25); `summaryFixture` gains `accounts: [accountSummaryFixture()]`,
`account_limit: { max: 5, used: 1 }`; `investorRowFixture` gains
`accounts: [{ account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live' }]`.

### Components and copy

| Where | Task | Names |
|---|---|---|
| `pages/investor/InvestorDashboard.tsx` | 10 | Card `Live accounts`; row link text `accountName(a)` to `/org/<id>/invest/account?account=<account_id>`; `connected` / `terminal offline`; `Total equity`; header link `Open live account` while `used < max` |
| `pages/investor/AccountSwitcher.tsx` (default export) | 11 | props `{ accounts: AccountSummary[]; value: number | null; onChange: (accountId: number) => void }`; hidden below two accounts; `Select` aria-label `Trading account` |
| `pages/investor/InvestorAccount.tsx`, `InvestorHistory.tsx` | 11 | `?account=<id>` via `setSearchParams({ account }, { replace: true })`; API `account_id=<id>`; Card `Your MT5 login` from the picked account |
| `pages/investor/InvestorTransfer.tsx` | 12 | option values `account:<id>`; labels `Trading account` (one) / `Trading account <accountName>` (several) |
| `pages/investor/InvestorDeposit.tsx` | 12 | `Select` aria-label `Which trading account` (several accounts, target account only) |
| `pages/investor/InvestorOpenAccount.tsx` | 13 | reads `investor/summary`; NextStep `You have reached your account limit` / `This workspace allows <max> live accounts per investor. Ask your admin if you need another.` |
| `pages/Investors.tsx` | 14 | column `Accounts` (`not linked` / name / `<name> · <n> accounts`); equity total; menu item `Manage accounts`; notices `Account linked`, `Account unlinked` |
| `pages/investors/AccountsDrawer.tsx` (default export) | 14 | props `{ investor: InvestorRow | null; linkable: Account[]; busy: boolean; error: string | null; onLink(id); onUnlink(id); onClose() }`; dialog `<display_name>'s accounts`; buttons `Unlink <name>`, `Link`; `Select` aria-label `Account to link` |
| `pages/investors/PaymentMethodsTab.tsx` | 14 | Card `Portal settings`; input aria-label `Max live accounts per investor`; button `Save portal settings`; notice `Portal settings saved` |
