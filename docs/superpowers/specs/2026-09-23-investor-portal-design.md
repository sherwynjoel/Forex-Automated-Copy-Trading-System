# Investor portal: deposits, withdrawals and a restricted investor view

**Date:** 2026-09-23
**Status:** approved in discussion, awaiting review of this document

## 1. Goal

Let a person invest with the operator of a MirrorFleet workspace without
ever seeing the desk. The investor deposits crypto to the operator's wallet,
the operator opens and funds an MT5 account for them at the broker and
connects it to MirrorFleet as a follower of the workspace's master, and the
existing copier does the trading. The investor logs in and sees only their
own account: live equity, real trade history and performance, what they have
deposited and withdrawn, and their profit. They cash out by raising a
withdrawal request that an admin approves, pays from their own wallet, and
records.

The app keeps an honest ledger and runs approval workflows. It never holds
private keys, never signs a transaction, and never moves money. Every
transfer of value is done by a person outside the app and recorded inside it.

## 2. Non-goals (follow-up work, each its own design)

- Performance or success fees. Under this model they would be deducted at
  withdrawal time; that needs its own pass.
- KYC document upload and verification.
- A "browse strategies" marketplace page. In this slice the admin assigns an
  investor's account to the workspace's master, as today.
- An in-app notifications center (sub-project 2).
- Pooled-fund (PAMM) accounting. Every investor has their own MT5 account,
  so no unit or NAV accounting exists anywhere in this design.
- Automatic deposit detection from a chain or a payment gateway. Deposits
  are noticed by the investor and confirmed by an admin.

## 3. Roles

A new membership role, `investor`, ranked **below** `viewer`:

    investor < viewer < trader < admin < owner

Every existing endpoint requires `viewer` or higher, so an investor is
refused by all of them without any per-endpoint change. Investors reach
only the endpoints in section 6, which resolve the account linked to the
calling user before doing anything else.

Where the role appears:

- `api/src/api/rbac.py` `ROLE_RANK` gains `"investor": -1` so existing
  ranks are untouched.
- `org_memberships.role` CHECK constraint is extended (migration, section 4).
- Invite creation (`routes/orgs.py`) accepts `investor` as an invite role;
  membership PATCH accepts it too. An owner can change an investor to a
  desk role and vice versa; changing away from `investor` does not unlink
  the account (the link is simply unused until the role is `investor` again).
- `dashboard/src/lib/roles.ts` mirrors the rank; the Members page lists
  "Investor" in the invite-role picker.

## 4. Data (migration `019_investor_portal.sql`)

**Account link.** `accounts.investor_user_id BIGINT NULL REFERENCES
users(id) ON DELETE SET NULL`, with a partial unique index on
`(org_id, investor_user_id) WHERE investor_user_id IS NOT NULL`: one
investor has at most one account per workspace, and an account belongs to
at most one investor. An unlink sets it NULL. Linking an account that is
not in the same workspace as the membership is refused.

**Workspace wallet settings.** `org_investor_wallets(org_id PK REFERENCES
orgs ON DELETE CASCADE, coin TEXT NOT NULL, network TEXT NOT NULL, address
TEXT NOT NULL, memo TEXT NULL, updated_by BIGINT NULL, updated_at
TIMESTAMPTZ NOT NULL DEFAULT now())`. Exactly one row per workspace, or
none: with no row, the investor Deposit page says deposits are not open yet.

**Deposits.** `investor_deposits(id BIGSERIAL PK, org_id, user_id,
account_id BIGINT NULL, amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),
coin TEXT NOT NULL, txid TEXT NOT NULL, note TEXT NULL, status TEXT NOT NULL
CHECK (status IN ('pending','confirmed','rejected')) DEFAULT 'pending',
decided_by BIGINT NULL, decided_at TIMESTAMPTZ NULL, decision_note TEXT
NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`, index on
`(org_id, status, created_at)`. `account_id` is filled at confirmation time
from the investor's current link, or left NULL when no account is linked
yet; the summary counts confirmed deposits by `user_id`, not by account.

**Withdrawals.** `investor_withdrawals(id BIGSERIAL PK, org_id, user_id,
account_id BIGINT NOT NULL, amount NUMERIC(18,2) NOT NULL CHECK (amount >
0), destination TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN
('requested','approved','paid','rejected')) DEFAULT 'requested',
equity_at_request NUMERIC(18,2) NULL, equity_verified BOOLEAN NOT NULL
DEFAULT false, decided_by BIGINT NULL, decided_at TIMESTAMPTZ NULL,
decision_note TEXT NULL, paid_by BIGINT NULL, paid_at TIMESTAMPTZ NULL, txid
TEXT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`, index on
`(org_id, status, created_at)`.

Foreign keys: `org_id -> orgs ON DELETE CASCADE`, `user_id -> users ON
DELETE CASCADE`, `account_id -> accounts ON DELETE SET NULL` for deposits
and `ON DELETE RESTRICT` for withdrawals (an account with money-movement
history cannot be silently removed).

**Investor summary** is computed on every read, never stored:

    total_deposited   = sum(amount) of confirmed deposits for the user
    total_withdrawn   = sum(amount) of paid withdrawals for the user
    net_deposits      = total_deposited - total_withdrawn
    equity            = the linked account's live equity (NULL if unknown)
    profit            = equity - net_deposits (NULL if equity is NULL)
    pending_withdrawn = sum(amount) of requested + approved withdrawals
    available         = equity - pending_withdrawn (NULL if equity is NULL)

Equity comes from the same copier state snapshot the desk pages use,
filtered to the one linked account, and from the account's stored balance
when the terminal is offline (reported as `equity_source: 'live' | 'last
known' | 'unknown'`).

## 5. Workflows and state machines

**Onboarding.** Investor registers (existing form), opens an investor
invite link (existing join flow), and lands in the investor portal. With no
account linked, Overview shows "your account is being set up"; Deposit
works; Withdraw and History show the same setup notice.

**Deposit.**

1. Investor reads the wallet card (coin, network, address, QR, memo) and
   sends the crypto from their own wallet.
2. Investor files a deposit notice: amount, coin, transaction ID, optional
   note. Status `pending`.
3. Admin confirms (optionally with a note) or rejects (note required).
   Confirmed deposits count toward the ledger immediately.
4. Admin opens and funds the MT5 account at the broker, connects it to
   MirrorFleet as usual (Accounts page), and links it to the investor on
   the Investors page. This step is outside the ledger by design.

    pending --confirm--> confirmed
    pending --reject---> rejected

**Withdrawal.**

1. Investor enters amount and destination address. The server computes
   `available`; if equity is known and `amount > available`, the request is
   refused with the available figure. If equity is unknown (terminal
   offline), the request is accepted with `equity_verified = false` and the
   admin queue shows "equity unverified".
2. Admin approves (optionally with a note) or rejects (note required).
3. Admin withdraws the money from the MT5 account at the broker and sends
   the crypto from their own wallet, then marks the request paid with the
   transaction ID. Paid withdrawals count toward the ledger immediately.

    requested --approve--> approved --paid--> paid
    requested --reject---> rejected
    approved  --reject---> rejected   (money not yet sent; note required)

Transitions only move forward. A decision is a single conditional update
(`UPDATE ... WHERE id = %s AND status = <expected>`); zero rows updated
means someone else decided first and the caller gets 409 with the current
status. Decided records are never edited; a later note is appended to the
audit log, not written over the decision.

**Account link changes.** Linking, relinking or unlinking never touches
deposit or withdrawal rows. A withdrawal always refers to the account it
was requested against.

## 6. API

All routes are under `/api/orgs/{org_id}` and use the existing
`require_org_role` dependency and the existing audit/event helpers.

**Investor endpoints** (`require_org_role("investor")`, so any member can
call them; each resolves the *caller's own* linked account and never takes
an account id as input):

| Method | Path | Returns |
|---|---|---|
| GET | `/investor/summary` | the summary in section 4 plus account status and link state |
| GET | `/investor/positions` | open positions of the linked account (read-only shape of the desk's positions) |
| GET | `/investor/analytics?weeks=` | the existing per-account analytics for the linked account |
| GET | `/investor/history/{deals\|positions}` | the existing per-account history for the linked account |
| GET | `/investor/wallet` | coin, network, address, memo (404 if not configured) |
| GET / POST | `/investor/deposits` | list own deposits / file a notice |
| GET / POST | `/investor/withdrawals` | list own withdrawals / raise a request |

With no linked account: `summary` returns `link_state: 'unlinked'` and null
figures; `positions`, `analytics`, `history` and `POST withdrawals` return
409 `no account linked yet`; `wallet` and `deposits` work.

**Admin endpoints** (`require_org_role("admin")`):

| Method | Path | Purpose |
|---|---|---|
| GET / PUT | `/investor-wallet` | read / set the workspace wallet card |
| GET | `/investors` | investor members with link state and summary figures |
| PUT | `/investors/{user_id}/account` | body `{account_id}` or `{account_id: null}` to link / unlink |
| GET | `/investor-deposits?status=` | queue (default: pending first, newest first) |
| POST | `/investor-deposits/{id}/decision` | body `{status: 'confirmed'\|'rejected', note}` |
| GET | `/investor-withdrawals?status=` | queue |
| POST | `/investor-withdrawals/{id}/decision` | body `{status: 'approved'\|'rejected', note}` |
| POST | `/investor-withdrawals/{id}/paid` | body `{txid}`; only from `approved` |

Validation, applied server-side and mirrored in the forms: amounts positive
with at most two decimals and at most 12 integer digits; `txid` and
`destination` non-empty, at most 128 characters, trimmed; `coin` on a
deposit must equal the workspace wallet's coin; a reject needs a non-empty
note; a `paid` needs a non-empty `txid`.

**Events written** (category `control`, actor = the acting user, account =
the linked account when known):

| action | severity | when |
|---|---|---|
| `investor_deposit_noticed` | warning | investor files a notice |
| `investor_withdrawal_requested` | warning | investor raises a request |
| `investor_deposit_decided` | info | admin confirms / rejects |
| `investor_withdrawal_decided` | info | admin approves / rejects |
| `investor_withdrawal_paid` | info | admin marks paid |
| `investor_account_linked` | info | admin links / unlinks |

## 7. Notifications

- **Admins**: `alerts.py` `ALERT_RULES` and the Telegram notifier's rules
  gain `("control","warning","investor_deposit_noticed")` and
  `("control","warning","investor_withdrawal_requested")`. Delivery,
  cooldown and configuration are exactly as today. The per-(action,
  account) cooldown keys on the investor's user id for these two actions
  so two investors filing in the same quarter hour both get through.
- **Investors**: on every admin decision and on `paid`, the API sends one
  email to the investor's own address through the existing Resend client
  (`EmailAlerter` gains a `send_to(address, subject, text)` method).
  Best-effort: a failed send is logged and never fails the request. With
  email unconfigured nothing is sent and the portal's status timeline is
  the source of truth.

## 8. Dashboard

**Shell.** `App.tsx` mounts the investor routes under `/org/:id/invest/*`.
When the caller's role in the selected workspace is `investor`, `Layout`
renders an investor sidebar (Overview, Deposit, Withdraw, History, Account)
and no desk strip, no kill switch, no prices. Desk routes redirect
investors to `/invest`; desk roles opening `/invest` see the setup notice
(the endpoints work for them too, they simply have no linked account).

**Investor pages** (`dashboard/src/pages/investor/`):

- `InvestorOverview` — tiles: equity (with `equity_source` caption),
  profit, net deposits, pending withdrawals; account status; open positions
  table (symbol, side, lots, entry, current, live P&L); performance snapshot
  (net P&L, win rate, max drawdown, equity curve) from `/investor/analytics`;
  recent activity timeline merging deposits and withdrawals.
- `InvestorDeposit` — wallet card with QR (generated in the browser from
  the address, no server involvement), coin/network warning, "I have sent
  it" form, own notices with status pills.
- `InvestorWithdraw` — available amount, request form, own requests with a
  status timeline and admin notes.
- `InvestorHistory` — closed positions and deals for the linked account,
  reusing the desk History page's table components.
- `Account` — the existing password-change card (from Members) and the
  investor's display name.

**Admin page** `Investors` (`dashboard/src/pages/Investors.tsx`, nav entry
visible to admin and owner): investor table (name, email, linked account or
"not linked", equity, net deposits, profit, pending counts) with a link /
unlink control listing unlinked accounts of the workspace; tabs for the
Deposits queue and the Withdrawals queue with confirm / reject / approve /
mark-paid dialogs (reject and paid dialogs require their note / txid); a
Wallet settings card. Everything refreshes on the existing WebSocket
control events, as the Automation page's recent-alerts list does.

**Members page**: "Investor" in the invite-role and role pickers.

**Types and client**: `lib/types.ts` gains the investor/deposit/withdrawal
shapes; `lib/api.ts` gains one function per endpoint.

## 9. Safety and audit

- Investors can only ever read or write rows where `user_id` is their own
  and the account is their linked one. The resolution happens once, in a
  shared dependency, and every investor endpoint goes through it.
- Amount and status rules are enforced in the database (CHECKs) and in the
  API (clear messages), not only in forms.
- No endpoint accepts or returns wallet secrets; the wallet card is an
  address to *receive* at, nothing more.
- Every state change writes an event with the actor's email, so the Logs
  page shows the full money-movement history alongside everything else.
- Rate limiting: deposit notices and withdrawal requests are limited to
  ten per investor per hour using the existing rate limiter.

## 10. Testing

**API** (`api/tests/test_investor_portal.py`, plus `test_migration_019.py`
in the pattern of the existing migration tests):

- an `investor` member gets 403 from every existing desk endpoint (accounts,
  state, settings, positions, orders, history, analytics, events, webhook);
- investor endpoints return only the caller's linked account; a second
  investor in the same workspace cannot see the first's rows;
- deposit notice → pending, admin confirm/reject, summary figures update;
  reject without a note is refused;
- withdrawal above `available` is refused with the figure; with unknown
  equity it is accepted and flagged; approve → paid needs a txid; paid from
  `requested` is refused; two concurrent decisions produce exactly one
  decision and one 409;
- link / unlink rules, including a foreign-workspace account being refused;
- events written with the right action, severity and actor; alert rules
  fire for the two warning actions; the investor email is attempted on
  decisions and never fails the request.

**Dashboard** (vitest, alongside the existing page tests): investor shell
shows the investor sidebar and hides desk pages; Overview renders the setup
notice when unlinked and the tiles when linked; Deposit form validation and
submission; Withdraw form shows `available` and the server's refusal
message; Investors page queues decide items and refresh.

**Manual, before real money**: one demo MT5 account end to end — invite an
investor, deposit notice, confirm, link, see live equity and history in the
portal, request a withdrawal, approve, mark paid; then check Logs, email
and Telegram.

## 11. Rollout notes

- Migration `019` must be applied with the `migrate` service rebuilt, per
  the deploy notes; `api` and `dashboard` (baked into the api image) are
  rebuilt; the copier is untouched.
- Holding and trading other people's money is regulated activity in most
  countries, including India. The operator should confirm registration,
  KYC and anti-money-laundering obligations with a lawyer before onboarding
  outside investors. This design does not change that obligation; it only
  keeps clean records of what was done.
