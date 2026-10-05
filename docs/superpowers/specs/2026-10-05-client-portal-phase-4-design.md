# Client portal, phase 4: notifications, support tickets, bonus and settings

**Date:** 2026-10-05
**Status:** approved design; plan to follow
**Builds on:** phases 1-3 (`2026-09-29-client-portal-phase-1-money-design.md`,
`2026-10-01-client-portal-phase-2-identity-design.md`,
`2026-10-05-client-portal-phase-3-multi-account-design.md`), all on main.
**Reference:** `docs/reference/aiprime-portal-survey.md` (Bonus, Support Chat, Notifications,
Settings).

## 1. Goal

Finish the broker-style portal: an in-app notification centre, support tickets between
investors and the desk, bonuses paid into the Credit wallet by admin rules or by hand,
and a Settings page for appearance and email preferences.

## 2. Decisions (owner)

- 2026-10-01: no IB / referral / salary; no copy trading, PAMM or social in the portal.
- 2026-10-05: bonuses by **admin rules + manual grants**; bonus credit may **only be
  transferred to one of the investor's trading accounts** (never to Main, never
  withdrawn); support tickets **like AI Prime** (admin subjects, up to 3 images per
  message, statuses, threaded replies; no live chat); Settings = **appearance + email
  notification preferences** (no keyboard shortcuts).

## 3. Non-goals

Trade-event bonuses (need copier trade data), live chat over the websocket, keyboard
shortcuts, bonus expiry / turnover requirements, admin-to-admin notifications beyond
support, push or SMS notifications.

## 4. Data (migration `025_portal_engagement.sql`)

**notifications** — one row per recipient per event.

    id BIGSERIAL PK; org_id FK orgs CASCADE; user_id FK users CASCADE;
    topic TEXT NOT NULL CHECK (topic IN ('money','identity','support','bonus'));
    title TEXT NOT NULL (<= 120); body TEXT NOT NULL (<= 500); link TEXT NULL (an
    in-app path, starts with '/'); read_at TIMESTAMPTZ NULL; created_at default now()
    INDEX (org_id, user_id, created_at DESC, id DESC); partial INDEX unread
    (org_id, user_id) WHERE read_at IS NULL

**notification_prefs** — email switches per user per org; no row = all on.

    org_id, user_id PK; money, identity, support, bonus BOOLEAN NOT NULL DEFAULT true;
    updated_at

**user_settings** — appearance per user (global, not per org).

    user_id PK FK users CASCADE; theme TEXT NOT NULL CHECK (theme IN
    ('light','dim','dark','system')) DEFAULT 'system'; updated_at

**ticket_subjects** — admin-defined list. `id, org_id, label (<= 80), enabled bool,
sort int, created_at`; UNIQUE (org_id, lower(label)).

**tickets** — `id, org_id, user_id (the investor), subject_id FK SET NULL,
subject_label TEXT (snapshot), status CHECK IN ('new','open','closed'), created_at,
updated_at, last_message_at, closed_at, closed_by`. INDEX (org_id, status,
last_message_at DESC); INDEX (org_id, user_id, last_message_at DESC).

**ticket_messages** — `id, ticket_id FK CASCADE, org_id, author_id FK users SET NULL,
from_desk BOOLEAN, body TEXT (1-4000), file_ids BIGINT[] NOT NULL DEFAULT '{}'
CHECK (cardinality(file_ids) <= 3), created_at`. INDEX (ticket_id, created_at, id).

**bonus_rules** — one row per org, created on first read (like `portal_settings`).

    org_id PK; signup_enabled bool false, signup_amount NUMERIC(18,2) >= 0;
    kyc_enabled bool false, kyc_amount NUMERIC(18,2) >= 0;
    deposit_enabled bool false, deposit_pct NUMERIC(6,3) 0..100,
    deposit_cap NUMERIC(18,2) NULL (> 0 when set); updated_by, updated_at

**bonuses** — the record of every bonus paid (the ledger entry references it).

    id; org_id; user_id; source TEXT CHECK IN ('signup','kyc','deposit','manual');
    source_id BIGINT NULL (deposits.id for 'deposit'; NULL otherwise);
    amount NUMERIC(18,2) <> 0 (negative only for a manual claw-back);
    note TEXT NULL; created_by NULL; created_at
    UNIQUE (org_id, user_id, source) WHERE source IN ('signup','kyc')
    UNIQUE (org_id, source_id) WHERE source = 'deposit'

`files.purpose` already allows `ticket_attachment`; `wallet_entries.kind` already allows
`bonus`; `wallet_entries.wallet` already allows `credit`.

## 5. Rules

### Notifications
- `portal_common.notify(conn, request, org_id, user_id, topic, title, body, link)`
  inserts the notification and then emails it unless the user's pref for `topic` is
  off. It replaces `notify_investor` at every caller (today 9); the email subject is the
  title, the text the body. A failing email never fails the request (unchanged).
- Topics: deposit / withdrawal / transfer / payout-destination decisions and admin
  adjustments = `money`; KYC and account-request decisions = `identity`; ticket replies
  and status changes = `support`; bonuses = `bonus`.
- Desk side: a new ticket and an investor reply notify every **admin** of the org
  (`topic = support`, link to the ticket in the Requests desk).
- `GET /api/orgs/{org}/notifications?before=&limit=` (own, newest first, limit 50
  default, 100 max), `GET .../notifications/unread-count`, `POST
  .../notifications/{id}/read`, `POST .../notifications/read-all`. Any member role; a
  user only ever sees their own rows (404 for another's id).

### Support tickets
- Investor: `POST investor/tickets {subject_id, body, file_ids[]}` (subject enabled,
  body 1-4000, at most 3 files each the investor's own `ticket_attachment` upload not
  used elsewhere) -> status `new`; `GET investor/tickets?status=&q=` (q searches subject
  and message text); `GET investor/tickets/{id}` (thread); `POST
  investor/tickets/{id}/messages {body, file_ids}` (a reply to a `closed` ticket reopens
  it to `open`); `POST investor/tickets/{id}/close`.
- Admin: `GET tickets?status=&q=`, `GET tickets/{id}`, `POST tickets/{id}/messages`
  (first desk reply moves `new` -> `open`), `POST tickets/{id}/close`; subjects CRUD
  `GET/POST ticket-subjects`, `PATCH/DELETE ticket-subjects/{id}` (delete of a used
  subject keeps old tickets via the snapshot label).
- Rate limit: an investor may open at most 10 tickets per hour (existing hourly
  limiter). Every change is audited (`ticket_opened`, `ticket_replied`,
  `ticket_closed`) without message text.
- Requests desk summary gains `tickets` = count of `new` + tickets whose last message
  is from the investor and still `open`.
- Attachments are served through the existing file routes (investor: own files; admin:
  org files).

### Bonus
- `bonus_rules` GET/PUT (admin), validated like portal settings; audited.
- `pay_bonus(conn, org_id, user_id, source, amount, source_id=None, note=None,
  created_by=None)` in portal_common: inside the caller's transaction, after
  `lock_investor_ledger`, inserts `bonuses` (ON CONFLICT DO NOTHING on the unique
  indexes = paid at most once) and, if inserted and amount != 0, settles a
  `wallet_entries` row (`wallet='credit', kind='bonus', ref_table='bonuses'`). Returns
  the bonus id or None. Then the caller notifies (`topic = bonus`).
- Triggers: **signup** when a member becomes an investor (invite accepted with role
  investor, or role changed to investor) and the rule is on; **kyc** when an admin
  approves KYC and the rule is on; **deposit** when an admin confirms a deposit and the
  rule is on: `round(confirmed amount * pct / 100, 2)`, capped by `deposit_cap`; zero
  pays nothing. Rules apply to events after they are switched on (no back-pay).
- **Manual**: `POST investors/{id}/bonuses {amount, note, mpin}` (admin MPIN step-up as
  adjustments; note required; negative amount = claw-back, refused if it would take
  the Credit wallet's available balance below zero).
- **Using credit**: `TRANSFER_PAIRS` gains `("credit", "account")` only. The transfer is
  an account transfer like main->account (requested, admin decision, MPIN, cap on
  credit available). Credit never moves to main/pamm/social and is never withdrawn
  (withdrawals already only debit main).
- Investor `GET investor/bonuses?source=&from=&to=` (own history: date, source, amount,
  note) and the Credit wallet balance (already in the summary wallets).

### Settings
- `GET/PUT /api/me/settings {theme}` (any signed-in user; global). The dashboard applies
  the server theme after sign-in and writes it on change; localStorage stays as the
  first-paint cache.
- `GET/PUT /api/orgs/{org}/notification-prefs {money, identity, support, bonus}` (any
  member, own row).

## 6. Dashboard

- **Bell** in the top bar (both desk and investor layouts): unread badge (polls
  unread-count with the existing 10 s cadence), popover with the latest 8 and "Mark all
  read", link to the Notifications page.
- **Notifications page** (`/org/:id/notifications` for the desk, `invest/notifications`
  for investors): list, newest first, "Load more", per-row read state, click = mark read
  + go to the link.
- **Investor Support** (`invest/support`): ticket list with status tabs All / New /
  Open / Closed and search; "Raise ticket" dialog (subject select, message, up to 3
  images via the existing upload component); thread view with replies and images;
  Close.
- **Desk Support** tab in the Requests desk (list, thread, reply, close) and a "Ticket
  subjects" card under Investors -> Payment methods (rename the tab "Portal settings").
- **Investor Bonus** page (`invest/bonus`): Credit balance, history table with Source
  filter and dates. Transfer page offers Credit -> trading account.
- **Admin**: "Bonus rules" card in Portal settings; "Grant bonus" action in the Investors
  row menu (amount, note, MPIN).
- **Settings page** (`invest/settings` and the desk's user menu): Appearance (light /
  dim / dark / system) and Email notifications (four switches).
- Investor nav gains Support, Bonus, Notifications, Settings.

## 7. Testing

API: migration 025 shape; notify() writes a row and respects prefs; every existing
notify_investor caller now writes a notification; notification routes own-rows only;
tickets lifecycle (new -> open on desk reply, closed -> open on investor reply),
attachment ownership and limit, search, rate limit, summary count; bonus rules
validation; each trigger pays once (retry / double confirm pays nothing more); deposit
pct + cap rounding; manual grant + claw-back floor; credit -> account transfer allowed,
credit -> main and credit withdrawal refused; settings + prefs round trip; RBAC rows for
every new route.
Dashboard: bell + page, support pages (investor + desk), bonus page, transfer credit
option, admin cards and grant dialog, settings page; full gate green.

## 8. Deploy

Migration 025 is additive (new tables only). Deploy with phases 1 follow-ups, 2 and 3
in the single end-of-project deploy. After deploy the admin should add ticket subjects
and, if wanted, switch bonus rules on.
