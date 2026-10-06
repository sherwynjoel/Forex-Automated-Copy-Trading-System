# Client portal phase 4 — interfaces and task map

Companion to `docs/superpowers/plans/2026-10-05-client-portal-phase-4.md`. Every task in
the plan consumes and produces exactly the names below. An implementer may add private
helpers, never rename or reshape anything here. Spec (binding):
`docs/superpowers/specs/2026-10-05-client-portal-phase-4-design.md`.

## Global constraints (copied into the plan header)

- Branch `client-portal-phase-4` in the worktree `.worktrees/phase4`; never `cd` to the
  main checkout, never `git stash`. Commit after every task; every commit ends with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0172Z1YU9U49b8Hx22j96ooN`.
  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`.
- `copier/` is never touched.
- API tests from the worktree's `api/`: `PYTHONPATH=$(pwd -W)/src`, the main checkout's
  `api/.venv/Scripts/python.exe`, `TEST_POSTGRES_ADMIN_DSN=…@127.0.0.1:5433/copytrader`,
  `TEST_POSTGRES_DSN=…@127.0.0.1:5433/copytrader_test_p4` (never `localhost`). Seven
  `test_events_ws.py` errors and one EA-download CRLF failure are pre-existing.
- Dashboard: `npm ci` once; one file `npx vitest run <path>`; gate `npm test` and
  `npm run build`; zero `act(...)` warnings.
- Investor routes `Depends(require_investor)`; admin routes
  `Depends(require_org_role("admin"))`; notification routes `require_org_role("investor")`
  (any member); every new org route has an RBAC matrix row (Task 12).
- Mutations audit through `pc.audit_control`; ticket audits never carry message text.
  Ledger transactions (every `pc.pay_bonus`) take `pc.lock_investor_ledger` first and
  await nothing while it is held.

## Task map

| # | Task | Produces for later tasks |
|---|---|---|
| 1 | Migration `025_portal_engagement.sql`, `test_migration_025.py`, conftest TRUNCATE | schema |
| 2 | `pc.notify`, `pc.notify_admins`, `pc.email_wanted`, `pc.investor_link`, `pc.clip`; the nine `notify_investor` callers moved; `notify_investor` deleted | `notify*` |
| 3 | `routes/portal_notifications.py`: list, unread count, read one, read all | `note_json` |
| 4 | `GET/PUT /api/me/settings`, `GET/PUT notification-prefs` | |
| 5 | `routes/portal_support.py` subjects; uploads accept `ticket_attachment` (images only); `file_belongs` knows ticket messages | `subject_json`, test helpers `_subject`, `_events`, `_user_id` |
| 6 | Investor tickets: open, list (status, q), thread, reply, close; 10 tickets/hour, 60 replies/hour; investor thread names no desk staff | `ticket_json`, `message_json`, `load_thread`, `desk_link`, `WAITING_ON_DESK`, `TICKET_SELECT`, fixture `portal`, `_open`, `_reply` |
| 7 | Desk tickets: queue, thread, reply, close; `requests/summary.tickets` (counts `WAITING_ON_DESK`) | |
| 8 | `pl.deposit_bonus`; `pc.bonus_rules`, `rule_bonus`, `pay_bonus`, `announce_bonus`, `award_rule_bonus` (best effort: logs, never raises); `routes/portal_bonus.py` rules GET/PUT | `_rules`, `_credit_entries` (tests) |
| 9 | Triggers: signup (join, role change), kyc (approval), deposit (confirmation) | |
| 10 | `parse_signed_amount` extracted; manual grant/claw-back; investor bonus history; alert rules | `bonus_json` |
| 11 | `TRANSFER_PAIRS` gains `("credit", "account")`; `pc.credit_funded`, `pc.account_movable`; bonus credit never moves back out of an account (request cap, summary `account_available`, decision re-check) | |
| 12 | RBAC rows; full API suite | complete API |
| 13 | Dashboard types, `lib/engagement.ts`, fixtures | primitives |
| 14 | `useUnreadCount` (seq guard), `lib/notificationActions.ts`, `NotificationBell`, `Notifications` page, routes, nav | |
| 15 | `useTheme.choose`, `lib/themeSync.ts`, `Settings` page, routes, nav, rail link | |
| 16 | `pages/support/TicketMessages.tsx`, `InvestorSupport` (private image slots keep uploaded ids across a failed send) | |
| 17 | `SupportTab` in the Requests desk (follows `?tab`/`?ticket` while mounted, drops `?ticket` on close); `TicketSubjectsCard`; tab "Portal settings" | |
| 18 | `InvestorBonus`; Transfer offers Credit -> trading account | |
| 19 | `BonusRulesCard`; `GrantBonusDialog` (reuses `AdjustDialog`'s `checkSignedAmount`); Investors menu "Grant bonus" | |
| 20 | Gates; README runbook for 025; spec status | |

## Database (Task 1) — `db/migrations/025_portal_engagement.sql`

Eight new tables, nothing else touched: `notifications`, `notification_prefs`,
`user_settings`, `ticket_subjects`, `tickets`, `ticket_messages`, `bonus_rules`,
`bonuses`. Index names: `notifications_by_user`, `notifications_unread`,
`ticket_subjects_one_label`, `tickets_queue`, `tickets_by_user`, `ticket_messages_thread`,
`bonuses_once_per_user` (`(org_id, user_id, source) WHERE source IN ('signup','kyc')`),
`bonuses_once_per_deposit` (`(org_id, source_id) WHERE source = 'deposit'`),
`bonuses_by_user`. Extra CHECKs beyond the spec: `notifications.link LIKE '/%'`,
`tickets (status = 'closed') = (closed_at IS NOT NULL)`, `bonuses amount > 0 OR source =
'manual'`, `bonuses (source = 'deposit') = (source_id IS NOT NULL)`.
`conftest.py` TRUNCATE gains the eight tables.

## Python — `api/src/api/portal_common.py`

```python
TOPICS = ("money", "identity", "support", "bonus")                          # Task 2
def clip(text: str, limit: int) -> str                                     # "…" marks a cut
def investor_link(org_id: int, page: str) -> str                           # f"/org/{org_id}/invest/{page}"
def email_wanted(conn, org_id: int, user_id: int, topic: str) -> bool      # ValueError for a bad topic
async def notify(conn, request, org_id: int, user_id: int, topic: str,
                 title: str, body: str, link: Optional[str] = None) -> None # row (clip 120/500) + email unless pref off
async def notify_admins(conn, request, org_id: int, topic: str, title: str,
                        body: str, link: Optional[str] = None) -> None      # every admin member
# notify_investor is DELETED (Task 2)

BONUS_SOURCES = ("signup", "kyc", "deposit", "manual")                      # Task 8
BONUS_NAMES = {"signup": "welcome bonus", "kyc": "verification bonus",
               "deposit": "deposit bonus", "manual": "bonus"}
def bonus_rules(conn, org_id: int) -> dict        # row created on first read; Decimals, cap None
def rule_bonus(conn, org_id: int, source: str, base: Optional[Decimal] = None) -> Decimal  # 0 when off
def pay_bonus(conn, org_id: int, user_id: int, source: str, amount: Decimal,
              source_id: Optional[int] = None, note: Optional[str] = None,
              created_by: Optional[int] = None) -> Optional[int]   # RuntimeError outside a transaction
async def announce_bonus(conn, request, *, org_id: int, user_id: int, bonus_id: int,
                         source: str, amount: Decimal, actor_email: str,
                         note: Optional[str] = None) -> None       # audit investor_bonus_paid + notify
async def award_rule_bonus(conn, request, org_id: int, user_id: int, source: str,
                           *, actor_email: str) -> Optional[int]   # own transaction, lock first;
                                                                   # best effort: logs and answers None on failure

def credit_funded(conn, org_id: int, user_id: int, account_id: int) -> Decimal   # Task 11: done credit->account
def account_movable(conn, org_id: int, user_id: int, account_id: int,
                    equity: Decimal) -> Decimal   # Task 11: floor_cents(equity - open out - credit_funded), >= 0
```

`api/src/api/portal_ledger.py` (Task 8): `def deposit_bonus(amount: Decimal, pct: Decimal,
cap: Optional[Decimal]) -> Decimal` (half-up cents, then `min(cap)`). Task 11:
`TRANSFER_PAIRS` gains `("credit", "account")`; `request_transfer`'s account -> wallet cap and
the summary's per-account `account_available` use `pc.account_movable`, and `decide_transfer`
refuses to approve or mark done an account -> wallet transfer above `equity_at_request -
credit_funded` (409).

`api/src/api/routes/portal_admin.py` (Task 10): `def parse_signed_amount(raw: object) ->
Decimal` (HTTPException 400 with the adjustment route's messages; `post_adjustment` uses it).
Task 7: `requests/summary` gains `tickets`, counted with `portal_support.WAITING_ON_DESK`.

`api/src/api/routes/portal_support.py` (Task 6): `WAITING_ON_DESK` is the one SQL rule for
"waiting on the desk" (`t.status <> 'closed'` and the investor spoke last); `TICKET_SELECT`
selects it into every Ticket as `waiting_on_desk`. `REPLIES_PER_HOUR = 60`.

`api/src/api/uploads.py` (Task 5): `ACCEPTED_PURPOSES` gains `ticket_attachment`;
`routes/portal_files.py` refuses a non-image `ticket_attachment` with 400
`attachments must be images`; `file_belongs` excludes files in any `ticket_messages.file_ids`.

Bonus triggers (Task 9): `routes/orgs.py` `join_org` and `patch_member` take
`http_request: Request` and call `pc.award_rule_bonus(..., "signup", ...)` when the member
becomes an investor; `routes/portal_identity.py` `decide_kyc` calls it with `"kyc"` on
approval; `routes/portal_admin.py` `decide_deposit` calls `rule_bonus` + `pay_bonus` inside
its transaction and `announce_bonus` after.

## Routes

Under `/api/orgs/{org_id}` unless absolute.

| Route | Task | Who | Contract |
|---|---|---|---|
| `GET notifications?before=&limit=` | 3 | member | own rows, `ORDER BY id DESC`; limit default 50, clamped 1-100; `{notifications: Note[], has_more, next_before}` |
| `GET notifications/unread-count` | 3 | member | `{count}` |
| `POST notifications/{id}/read` | 3 | member | Note (first `read_at` kept); 404 `Notification not found` for another user's or unknown id |
| `POST notifications/read-all` | 3 | member | `{updated}` |
| `GET /api/me/settings` | 4 | signed in | `{theme, updated_at}`; no row = `{"theme": "system", "updated_at": null}` |
| `PUT /api/me/settings` | 4 | signed in | `{theme}`; 400 `theme must be light, dim, dark or system` |
| `GET notification-prefs` | 4 | member | `{money, identity, support, bonus}` (all true without a row) |
| `PUT notification-prefs` | 4 | member | all four required booleans, else 400 `<topic> must be true or false` |
| `GET ticket-subjects` | 5 | admin | Subject[] by `sort, id` |
| `POST ticket-subjects` | 5 | admin | `{label, enabled?, sort?}` 201; 400 `label is required` / `label must be at most 80 characters`; 409 `a subject with this label already exists`; audit `ticket_subject_changed` |
| `PATCH ticket-subjects/{id}` | 5 | admin | `{label?, enabled?, sort?}`; 404 `Subject not found`; 409 as POST |
| `DELETE ticket-subjects/{id}` | 5 | admin | 204; old tickets keep `subject_label` |
| `GET investor/ticket-subjects` | 5 | investor | enabled subjects only |
| `POST investor/tickets` | 6 | investor | `{subject_id, body, file_ids}` 201 Thread; 400 `subject_id is required`, `body is required`, `body must be at most 4000 characters`, `file_ids must be a list of file ids`, `at most 3 images per message`, `each image may be attached once`, `image not found`; 404 `Subject not found` (unknown or disabled); 429 `too many requests; try again later` (10/hour); audit `ticket_opened`; notifies every admin |
| `GET investor/tickets?status=&q=` | 6 | investor | own Ticket[] by `last_message_at DESC, id DESC`, LIMIT 500; 400 `status must be new, open or closed` |
| `GET investor/tickets/{id}` | 6 | investor | Thread; 404 `Ticket not found`; desk messages carry `author_id`/`author_name` null and a desk close `closed_by` null (the investor never sees desk staff) |
| `POST investor/tickets/{id}/messages` | 6 | investor | `{body, file_ids}` 201 Thread; closed -> open; 429 `too many requests; try again later` (60/hour, key `portal-ticket-reply`); audit `ticket_replied`; notifies every admin |
| `POST investor/tickets/{id}/close` | 6/7 | investor | Thread; 409 `ticket is already closed`; audit `ticket_closed` |
| `GET tickets?status=&q=` | 7 | admin | Ticket[] with `email, display_name`, open first then `last_message_at DESC` |
| `GET tickets/{id}` | 7 | admin | Thread |
| `POST tickets/{id}/messages` | 7 | admin | `{body}` (text only) 201 Thread; new -> open; 409 `ticket is closed`; notifies the investor |
| `POST tickets/{id}/close` | 7 | admin | Thread; 409 `ticket is already closed`; notifies the investor |
| `GET requests/summary` | 7 | admin | gains `tickets` = `WAITING_ON_DESK` (not closed, investor's message last); in `total` |
| `GET bonus-rules` | 8 | admin | Rules |
| `PUT bonus-rules` | 8 | admin | full Rules body; 400 per field (see plan Task 8; `deposit_pct` out of range `deposit_pct must be between 0 and 100`, more than three decimals `deposit_pct may have at most three decimals`); audit `bonus_rules_changed` |
| `POST investors/{user_id}/bonuses` | 10 | admin | `{amount, note, mpin}` 201 Bonus; MPIN first; 404 `Investor not found`; 400 `a claw-back cannot take the Credit wallet below zero (available N)`; audit `investor_bonus_paid` (warning) |
| `GET investor/bonuses?source=&from=&to=` | 10 | investor | own Bonus[] by id DESC, LIMIT 500; 400 `source must be one of signup, kyc, deposit, manual`, `from must be a date (YYYY-MM-DD)` |
| `POST investor/transfers` | 11 | investor | source credit -> account allowed (requested, admin decides); credit -> anything else 400 `that transfer is not allowed`; account -> wallet capped at `pc.account_movable` (bonus credit funded into the account excluded), 400 `amount exceeds the account's available equity (N)` |
| `POST transfers/{id}/decision` | 11 | admin | approve/done of an account -> wallet transfer above `equity_at_request - credit_funded`: 409 `bonus credit cannot leave the account (at most N may move out); reject this transfer instead` |
| `GET investor/summary` | 11 | investor | per-account `account_available` = `pc.account_movable` |

Shapes:
- Note = `{id, topic, title, body, link, read_at, created_at}`.
- Subject = `{id, label, enabled, sort, created_at}`.
- Ticket = `{id, user_id, subject_id, subject_label, status, created_at, updated_at,
  last_message_at, closed_at, closed_by, last_from_desk, waiting_on_desk}` (+ `email,
  display_name` on the desk queue and every Thread).
- Message = `{id, author_id, author_name, from_desk, body, file_ids, created_at}`.
- Thread = Ticket + `messages: Message[]` (oldest first).
- Rules = `{signup_enabled, signup_amount, kyc_enabled, kyc_amount, deposit_enabled,
  deposit_pct, deposit_cap, updated_at}` (amounts floats, cap float or null).
- Bonus = `{id, source, source_id, amount, note, created_at, currency}`.

Notification titles and links (Task 2 keeps the old email subjects as titles):

| Event | Topic | Link |
|---|---|---|
| deposit decided | money | `/org/<o>/invest/deposit` |
| withdrawal decided, paid | money | `/org/<o>/invest/withdraw` |
| payout account decided | money | `/org/<o>/invest/payout-accounts` |
| transfer decided | money | `/org/<o>/invest/transfer` |
| ledger adjusted | money | `/org/<o>/invest/transactions` |
| KYC decided | identity | `/org/<o>/invest/profile` |
| account request rejected, fulfilled | identity | `/org/<o>/invest/open-account` |
| desk reply `New reply on ticket #<id>: <subject>`; desk close `Ticket #<id> was closed` | support | `/org/<o>/invest/support?ticket=<id>` |
| to admins: `New ticket #<id>: <subject>`, `Reply on ticket #<id>: <subject>` | support | `/org/<o>/requests?tab=support&ticket=<id>` |
| bonus paid `You received a <n> USD <name>`; taken back `A bonus of <n> USD was taken back` | bonus | `/org/<o>/invest/bonus` |

## Dashboard

### `src/lib/types.ts` (Task 13)

```ts
export type NotificationTopic = 'money' | 'identity' | 'support' | 'bonus'
export interface PortalNotification { id: number; topic: NotificationTopic; title: string; body: string; link: string | null; read_at: string | null; created_at: string }
export interface NotificationsPage { notifications: PortalNotification[]; has_more: boolean; next_before: number | null }
export type NotificationPrefs = Record<NotificationTopic, boolean>
export type ThemePref = 'light' | 'dim' | 'dark' | 'system'
export interface UserSettings { theme: ThemePref; updated_at: string | null }
export type TicketStatus = 'new' | 'open' | 'closed'
export interface TicketSubject { id: number; label: string; enabled: boolean; sort: number; created_at: string }
export interface Ticket { id: number; user_id: number; subject_id: number | null; subject_label: string; status: TicketStatus; created_at: string; updated_at: string; last_message_at: string; closed_at: string | null; closed_by: number | null; last_from_desk: boolean; waiting_on_desk: boolean; email?: string; display_name?: string | null }
export interface TicketMessage { id: number; author_id: number | null; author_name: string | null; from_desk: boolean; body: string; file_ids: number[]; created_at: string }
export interface TicketThread extends Ticket { messages: TicketMessage[] }
export type BonusSource = 'signup' | 'kyc' | 'deposit' | 'manual'
export interface Bonus { id: number; source: BonusSource; source_id: number | null; amount: number; note: string | null; created_at: string; currency: string }
export interface BonusRules { signup_enabled: boolean; signup_amount: number; kyc_enabled: boolean; kyc_amount: number; deposit_enabled: boolean; deposit_pct: number; deposit_cap: number | null; updated_at: string | null }
RequestsSummary += tickets: number
```

### `src/lib/engagement.ts` (Task 13)

`TOPICS`, `TOPIC_LABELS`, `TOPIC_EMAIL_LABELS`, `TICKET_STATUS_LABELS`, `TICKET_STATUS_TONES`,
`BONUS_SOURCES`, `BONUS_SOURCE_LABELS`, `IMAGE_ACCEPT`, `MAX_IMAGES`, `TEXTAREA` (the one
textarea class InvestorSupport and SupportTab share),
`safeLink(link: string | null): string | null`,
`ticketsQuery(base: string, status: TicketStatus | 'all', q: string): string`.

### `src/test/portalFixtures.ts` (Task 13)

`notificationFixture`, `ticketFixture`, `ticketMessageFixture`, `threadFixture`,
`subjectFixture`, `bonusFixture`, `bonusRulesFixture` (each `(overrides = {})`).

### Hooks and libs

| Where | Task | Names |
|---|---|---|
| `hooks/useUnreadCount.ts` | 14 | `UNREAD_POLL_MS = 10000`; `useUnreadCount(orgId): { count: number \| undefined; refresh: () => void }` (a `seq` ref: only the newest poll or refresh lands) |
| `lib/notificationActions.ts` | 14 | `markRead(orgId, n): Promise<PortalNotification>` (no request for a read row), `markAllRead(orgId): Promise<void>`, `withAllRead(list, now?)`; the bell and the page both use them |
| `hooks/useTheme.ts` | 15 | `Theme`, `paletteFor(pref: ThemePref): Theme`; `useTheme(): { theme; toggle: () => ThemePref; choose: (pref: ThemePref) => void }` |
| `lib/themeSync.ts` | 15 | `THEME_PREFS`, `isThemePref`, `localPref(): ThemePref \| null`, `saveThemePref(pref): Promise<void>`, `syncThemeFromServer(choose): Promise<void>` |

### Components and copy

| Where | Task | Names |
|---|---|---|
| `components/layout/NotificationBell.tsx` (default) | 14 | props `{ orgId; pageHref; count: number \| undefined; onChange(): void }`; trigger 44 px square at every width; button aria-label `Notifications` / `Notifications, <n> unread`; popover `role="dialog"` aria-label `Latest notifications`; `Mark all read`; link `See all notifications` |
| `components/Layout.tsx` | 14/15 | one `useUnreadCount`; bell in the desktop rail header and the phone top bar; desk-only rail button `Settings`; theme toggle saves `saveThemePref(toggle())` |
| `pages/Notifications.tsx` (default; both groups export `Notifications`) | 14 | PageHeader `Notifications`; Card `Your notifications`; `Load more`; `Mark all read`; badges `Unread` + topic label |
| `pages/Settings.tsx` (default; both groups export `Settings`) | 15 | PageHeader `Settings`; Card `Appearance` radios `Light`, `Dim`, `System` (server `dark` shows as Dim); Card `Email notifications` four `role="switch"` checkboxes named by `TOPIC_EMAIL_LABELS`; notices `Appearance saved`, `Email preferences saved` |
| `pages/support/TicketMessages.tsx` (default) | 16 | props `{ messages; fileUrl(id): string; viewer: 'investor' \| 'desk' }`; images alt `Image <n>` |
| `pages/investor/InvestorSupport.tsx` (default; no named exports: `useImageSlots`/`ImageSlots` are private and keep an uploaded id once its upload lands, so a failed send neither re-uploads nor drops it; a slot holding one says `Uploaded; it goes with your message`) | 16 | PageHeader `Support`; button `Raise ticket`; dialog `Raise a ticket` (Select `Subject`, textarea `Message`, FileInputs `Image 1 (optional)`..`Image 3 (optional)`, confirm `Send ticket`); Tabs `Ticket status` All/New/Open/Closed; Input `Search tickets`; Card `Your tickets`; thread Card `#<id> <subject>`, textarea `Your reply`, button `Send reply`, button `Close ticket`, dialog confirm `Yes, close it`; `?ticket=<id>` |
| `pages/requests/SupportTab.tsx` (default) | 17 | props `DeskTabProps & { initialTicket: number \| null; onDrawerClosed: () => void }` (opens `initialTicket` on mount and whenever it changes); badge reads `Ticket.waiting_on_desk`; Input `Search tickets`; row button `Open ticket <id>`; badge `Waiting on desk`; Drawer `Ticket #<id>: <subject>`; textarea `Reply to the investor`; `Send reply`; `Close ticket`; done messages `Reply sent`, `Ticket closed` |
| `pages/requests/RequestTabs.tsx` | 17 | `DeskTab` += `'support'`; tab `Support (<tickets>)` |
| `pages/Requests.tsx` | 17 | `?tab` follows the chosen tab (and drops `?ticket`); a `?tab`/`?ticket` change while mounted switches tab and opens the ticket; closing the ticket drawer drops `?ticket` |
| `pages/investors/TicketSubjectsCard.tsx` (default) | 17 | props `{ orgId; control }`; Card `Ticket subjects`; Input `New subject`; `Add subject`; per row `Rename <label>` / Input `New name for <label>` / `Save name`, `Enable <label>` / `Disable <label>`, `Delete <label>` |
| `pages/Investors.tsx` | 17/19 | tab `Portal settings` (was Payment methods); menu `Grant bonus` |
| `pages/investors/PaymentMethodsTab.tsx` | 17/19 | the settings Card renamed `Withdrawal and account rules`; renders `TicketSubjectsCard`, `BonusRulesCard` |
| `pages/investor/InvestorBonus.tsx` (default) | 18 | PageHeader `Bonus`; Card `Credit wallet`; filters Select `Source`, Inputs `From date`, `To date`, `Apply`; Card `Bonus history` |
| `pages/investor/InvestorTransfer.tsx` | 18 | `PAIRS` += `['credit','account']`; wallets `main, credit, pamm, social` |
| `pages/investors/BonusRulesCard.tsx` (default) | 19 | Card `Bonus rules`; checkboxes `Welcome bonus on`, `Verification bonus on`, `Deposit bonus on`; inputs `Welcome bonus amount`, `Verification bonus amount`, `Deposit bonus %`, `Deposit bonus cap`; `Save bonus rules`; notice `Bonus rules saved` |
| `pages/investors/AdjustDialog.tsx` | 19 | named exports `SIGNED_AMOUNT`, `checkSignedAmount(amount, note): string` (throws the adjustment's messages) |
| `pages/investors/GrantBonusDialog.tsx` (default; validates with `checkSignedAmount`) | 19 | props `{ orgId; investor: InvestorRow \| null; onCancel(); onGranted(b: Bonus) }`; title `Grant <name> a bonus`; inputs `Bonus amount`, `Bonus note`; confirm `Pay bonus` |
| `components/layout/nav.ts` | 14-18 | investor Money += `Bonus`; Account += `Support`, `Notifications`, `Settings` (final order below) |

Final investor nav: Dashboard | Money: Wallet, Deposit, Withdraw, Transfer, Transactions,
Payout accounts, Bonus | Account: Trading account, Profile & verification, Open account,
Security, History, Support, Notifications, Settings (16 links). `adminNav` is unchanged.

Routes added under `/org/:orgId`: `notifications`, `settings` (desk, admin group);
`invest/notifications`, `invest/settings`, `invest/support`, `invest/bonus` (investor group).
