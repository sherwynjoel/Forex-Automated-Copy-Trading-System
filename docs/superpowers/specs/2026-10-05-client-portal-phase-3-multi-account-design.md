# Client portal, phase 3: several live accounts per investor

**Date:** 2026-10-05
**Status:** implemented on branch client-portal-phase-3 (plan docs/superpowers/plans/2026-10-05-client-portal-phase-3.md); awaiting deploy
**Builds on:** phase 1 (`2026-09-29-client-portal-phase-1-money-design.md`) and phase 2
(`2026-10-01-client-portal-phase-2-identity-design.md`), both on main.
**Reference:** `docs/reference/aiprime-portal-survey.md` (Your Trading Accounts, Live tab).

## 1. Goal

An investor can hold several live MT5 accounts in one workspace, up to a cap the admin
sets. Every screen that today shows "the account" lets the investor pick one, and the
dashboard shows all of them with totals.

## 2. Decisions (owner)

- 2026-10-01: no copy trading, PAMM or social marketplace in the portal; no strategy
  fees; investors may have several live accounts.
- 2026-10-05: **live accounts only** (no demo). The cap is a **workspace setting,
  default 5**. Still **one open account request at a time**.

## 3. Non-goals

- Demo accounts; investor-chosen nicknames; transfers directly between two trading
  accounts (wallet <-> account already covers it); a per-investor cap override.

## 4. Data (migration `024_multi_account.sql`)

- `DROP INDEX accounts_one_per_investor;` An account still has exactly one owner
  (`accounts.investor_user_id`, unchanged); an investor may now own several.
- `ALTER TABLE portal_settings ADD COLUMN max_live_accounts INTEGER NOT NULL DEFAULT 5
  CHECK (max_live_accounts BETWEEN 1 AND 50);`
- Keep a plain (non-unique) index on `accounts (org_id, investor_user_id) WHERE
  investor_user_id IS NOT NULL` for the per-investor lookups.

Existing data is valid as is: every investor has 0 or 1 account today.

## 5. Rules

**Ownership helper.** `portal_common.linked_account` (one id or None) is replaced by
`linked_accounts(conn, org_id, user_id) -> list[int]` (ordered by account id) and
`owns_account(conn, org_id, user_id, account_id) -> bool`. Every investor route that
names an account checks `owns_account`; a foreign or unknown id is 404 "Account not
found" (no hint whether it exists).

**Choosing an account on read routes** (`/investor/positions`, `/investor/analytics`,
`/investor/history/{kind}`): optional `account_id` query parameter.
- given: must be owned (404 otherwise);
- omitted and the investor owns exactly one: that one (current dashboard keeps working);
- omitted and owns none: 409 "no account linked yet" (unchanged);
- omitted and owns several: 400 "account_id is required".

**Transfers.** When a side is `account`, its `account_id` must be owned (404 otherwise);
"no account linked yet" (409) only when the investor owns none. Everything else about
transfers (caps, holds, equity check, admin decision) is unchanged and already per
account id.

**Deposits to an account.** `target_account_id` is required when `target = account`
and the investor owns several; with exactly one it may be omitted (that one). It must
be owned. On admin confirmation the deposit is credited to `target_account_id` if the
investor **still owns it**; otherwise to the main wallet with the existing
"(no account linked; credited to wallet)" note. (Today the check uses whatever single
account is linked at decision time; with several it must be the named one.)

**Cap.** `used = owned accounts + open account requests` (open = `requested`).
- Investor account request: refused 409 "you have reached the limit of N live
  accounts" when `used >= max_live_accounts`. The KYC gate and one-open-request rule
  stay. The old "an investor with an account cannot request" rule is removed.
- Admin link (Investors page) and fulfil-with-link: refused 409 with the same message
  when the investor already owns `max_live_accounts` accounts (the open request being
  fulfilled is not counted against itself).
- Lowering the cap below what an investor already has is allowed; it only blocks new
  requests and links.

**Admin link / unlink.**
- `POST /investors/{user_id}/accounts {account_id}` adds one link under the existing
  rules (investor member, not the master, any platform as today, account not linked to anyone) plus
  the cap. Audit `investor_account_linked`.
- `DELETE /investors/{user_id}/accounts/{account_id}` removes one link; 404 if that
  investor does not own it. Audit `investor_account_unlinked`. Pending transfers on that
  account keep their existing handling (the admin decision guard from the phase 1
  follow-ups).
- `PUT /investors/{user_id}/account` (replace the single link) is removed.
- Fulfil keeps its optional `account_id`; `_link_account` adds instead of refusing an
  investor who already has one.
- Removing a member / changing their role does not unlink accounts (unchanged); the
  investor routes already refuse non-investors.

## 6. API changes (under `/api/orgs/{org_id}`)

| Route | Change |
|---|---|
| `GET investor/summary` | `account`, `account_available`, `link_state` replaced by `accounts: AccountSummary[]`; top-level `equity`, `net_funded`, `profit`, `open_positions`, `equity_source` become **totals** across accounts; new `account_limit: {max, used}` |
| `GET investor/positions`, `investor/analytics`, `investor/history/{kind}` | optional `account_id` query |
| `POST investor/transfers`, `investor/deposits` | ownership check per account id |
| `POST investor/account-requests` | cap instead of "already has an account" |
| `GET investors` | `account_id`, `nickname`, `equity`, `equity_source` replaced by `accounts: [{account_id, nickname, equity, equity_source}]` |
| `POST investors/{id}/accounts`, `DELETE investors/{id}/accounts/{account_id}` | new |
| `PUT investors/{id}/account` | removed |
| `GET/PUT portal-settings` | adds `max_live_accounts` (integer 1-50) |
| `POST account-requests/{id}/fulfil` | link adds; cap checked |

`AccountSummary` = `{account_id, nickname, platform, status, last_error, connected,
mt5_login, mt5_server, equity_source, equity, net_funded, profit, account_available,
open_positions}`. `mt5_login`/`mt5_server` come from the fulfilled account request that
linked this account, else null.

Totals: `equity` is the sum of the accounts' equities, or null when any account's
equity is unknown (`equity_source` is then the worst of the parts: unknown > last
known > live); `profit = equity - net_funded` when equity is known. Equity for all
accounts comes from **one** copier `/state` round trip (`org_state` + `equity_from`, as
the admin Investors list does), not one call per account.

## 7. Dashboard

- **Investor dashboard:** the single account card becomes a "Live accounts" tray: one
  row per account (login or nickname, connection dot, equity, profit), total equity,
  and "Open live account" while `account_limit.used < account_limit.max`.
- **Account page:** an account switcher (a `Select`, shown only when there are several)
  above the existing cards; positions, analytics and history load for the picked
  account (`?account_id=`). The pick is kept in the URL (`?account=`), so a refresh
  keeps it. The login card shows the picked account's MT5 login.
- **Transfer and Deposit:** "Trading account" becomes a dropdown of the investor's
  accounts when there are several (with available-to-transfer per account on Transfer);
  a single account stays as today, fixed.
- **Open account page:** reachable while under the cap; at the cap it explains the
  limit instead of the form.
- **Admin Investors:** the account column shows the count and the first account's name
  ("2 accounts"); the investor drawer lists the accounts with Unlink each, and Link
  adds one (picker of unlinked non-master accounts, any platform). Portal settings gains "Max live accounts
  per investor".
- **Requests desk, Account requests tab:** unchanged except that the cap error is shown
  as returned.

## 8. Testing

API: migration 024 (index gone, column default 5, existing single links intact); the
read routes with 0 / 1 / several accounts and a foreign id; transfers and deposits
against a foreign account; deposit confirmation to a since-unlinked account; the cap on
request, link and fulfil (and lowering it); link / unlink routes and audits; summary
totals with mixed equity sources; investors list shape; RBAC rows for the new routes.
Dashboard: switcher, tray, dropdowns, Investors drawer link/unlink, settings field;
full gate (palette, tsc, vitest) green.

## 9. Deploy

Migration 024 drops a unique index and adds a defaulted column; no data rewrite. Deploy
together with phases 1-follow-ups, 2 and 4 (owner decision: one deploy at the end).
