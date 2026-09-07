# MT5 Bridge — Design

Status: approved by the owner on 2026-09-07 (Option 1 of three; see "Alternatives
rejected"). Supersedes the transport section of `docs/mt45-support-plan.md`
(MetaApi), which is withdrawn.

## Goal

An MT5 account connects to MirrorFleet through a single Expert Advisor file
(`MirrorFleet.mq5`) running in the owner's own MT5 terminal on their Windows
VPS. Once connected, the account behaves like any other MirrorFleet account:

- **As a follower** it copies the org's master (cTrader or MT5) — market fills,
  partial closes, stop/target changes, pending orders — with the same sizing
  rules as cTrader followers.
- **As a master** its trades are copied to the org's **cTrader** followers.
  MT5→MT5 copying is explicitly out of scope: the owner's MT5 setup does that
  itself.
- **Every operator action** in the dashboard works on it: Trade ticket orders,
  Positions close/amend, cancel, per-account flatten, org Close all.
- **Its history** (deals, balance operations) is stored by MirrorFleet and
  shown on History, Performance and Overview like a cTrader account's.
- **It shows live** on Overview, Positions and the top strip: balance, equity,
  open P&L, positions with current price.

Non-goals for this build: MT4; MT5→MT5 copying; copying
pending-order *expiry* changes; MetaApi.

## Ground truth the design rests on

- MT5 terminals can only make **outbound** HTTPS calls (`WebRequest`), and only
  to URLs the user has allowed in *Tools → Options → Expert Advisors*. Nothing
  can push to a terminal. Therefore the terminal **polls**.
- `WebRequest` is synchronous and runs on the EA's thread; a long-held request
  would delay the EA's own trade-event handling. Therefore polls are **short**
  (default every 250 ms, 3 s timeout), never long-polls.
- MQL5 has no JSON library. The EA **builds** JSON (easy) but **parses** a
  line-based, tab-separated response (trivial with `StringSplit`).
- MT5 volumes are lots (doubles). The copier's engine works in integer
  "protocol units" with a per-symbol `lot_size`. For MT5 symbols the bridge
  fixes `lot_size = 100` (units are **centilots**), so the existing sizing
  arithmetic (`mirror_volume`, `partial_close_volume`, `_lots()`) works
  unchanged and `volume_lots` strings render correctly.
- MT5 has no numeric symbol id. The bridge assigns `symbol_id = crc32(name)`
  per account (collision within one account's list resolved by +1 probing) so
  `symbol_cache` and every `symbol_id`-keyed path work unchanged.
- MT5 brokers may overwrite order comments on fill. Copy lineage therefore
  rests on **our mapping table** (position ticket ↔ mapping row) and the EA's
  magic number, never on the comment. The comment is still set (`copy:m<id>`)
  as a courtesy for the terminal's own history view.
- The `accounts` table's primary key is a BIGINT the broker assigned. MT5
  logins can collide with cTrader ids across brokers, so MT5 accounts get a
  **synthetic id** from a sequence starting at 1 000 000 000 000; the MT5 login
  lives in `mt5_links.login` and is copied into `accounts.trader_login` once
  known.
- One master per org is a database constraint and stays so. The master is
  either the cTrader account or an MT5 account; the owner switches by role.

## Architecture: the MT5 lane

The copier's decision engine (`domain/decision.py`) already produces
platform-neutral `SlaveIntent`s from platform-neutral `MasterEvent`s. cTrader
is bound at exactly two points: `dispatch.build_request` (intent → protobuf)
and `normalize.normalize` (protobuf → MasterEvent), plus the snapshot and
query paths that call `client.send(ProtoOA…)`. The MT5 lane plugs in at those
same points; nothing in `decide`, sizing, mapping bookkeeping or the dashboard
data shapes changes.

```
  MT5 terminal (EA)  ──HTTPS every 250 ms──►  api  /api/mt5/sync   (key checked first)
                                               │  proxies with account_id
                                               ▼
                                        copier POST /mt5/sync
                          ┌────────────────────┴─────────────────────┐
                          ▼                                          ▼
                report ingestion                              command outbox
        (snapshot, deals, acks, events)                  (queued → sent → done/failed)
                          │                                          ▲
      MT5 master ─► MasterEvent ─► decide ─► intents ──► cTrader followers (existing)
      cTrader master ─► normalize ─► decide ─► intents ─┘  MT5 followers → outbox
```

### Components

**copier/src/copier/mt5/** (new package)

- `registry.py` — `MT5Registry`: in-memory per-account state fed by reports:
  last snapshot (positions, orders), balance/equity/margin, last-seen time,
  hedging flag, symbol map. Read by the reconciler, `get_state`, flatten, and
  queries. Persists positions via `repo.upsert_positions`/`close_missing_positions`.
- `outbox.py` — intent/operator-action → `mt5_commands` row; delivery on the
  next sync; ack handling (activate/reduce/fail mapping; degraded status on
  broker rejection); **expiry**: `open` and `place_pending` commands not
  delivered within `OPEN_COMMAND_TTL_S = 30` are failed with "terminal offline"
  and the mapping marked failed — a market copy delivered minutes late is a
  different trade. `close`, `amend`, `cancel` never expire.
- `ingress.py` — report → `MasterEvent`s for an MT5 master (deal ENTRY_IN →
  `MasterPositionOpened`; ENTRY_OUT / INOUT → `MasterPositionClosed`; SL/TP
  change → `MasterPositionSLTPAmended`; pending add/modify/delete/fill →
  `MasterPending*`). Symbol names are translated broker → canonical via the
  account's aliases before they reach `decide`.
- `symbols.py` — hello symbol list → `SymbolInfo` rows (centilots), crc32 ids;
  auto-matching (normalise: uppercase, strip non-alphanumerics, strip known
  suffixes `.r .x .m .i .ecn .pro _i` and trailing `m`/`c`/`i` when the stem
  is a known instrument; synonym table: GOLD↔XAUUSD, SILVER↔XAGUSD,
  USTEC/NAS100↔NAS100, DE40/GER40↔GER40, US30/DJ30↔US30, US500/SPX500↔US500,
  UK100↔UK100, USOIL/WTI/OIL↔USOIL, BRENT/UKOIL↔UKOIL, BTCUSD/BITCOIN↔BTCUSD,
  ETHUSD↔ETHUSD). Manual override wins.
- `protocol.py` — the wire format (below), shared by the copier and the
  Python fake EA used in tests.

**copier changes**

- `Dispatcher`: before `build_request`, if the intent's account is MT5 →
  `outbox.enqueue(intent)`; mapping creation unchanged (same `client_order_id`
  scheme `cm<master>.<slave>` / `co<order>.<slave>`).
- `CopierService`: split `_handle_master_event` into normalise + `act`
  (`_act_on_master_event(org_id, master_id, normalized, …)`); the ingress lane
  calls `act` directly. Slave fills from MT5 reports go through the same
  `_handle_slave_fill` logic via a normalised `SlaveFill` dataclass (new)
  instead of the protobuf event.
- `Reconciler._fetch_snapshot`: MT5 account → registry snapshot (labels
  derived from mappings by position ticket).
- `CopierApp`: `_query_context` gains an MT5 branch returning an `MT5Lane`
  object; `place_order`, `close_position`, `amend_position_sltp`,
  `cancel_order`, `_flatten_account` branch on it. Flatten for MT5: enqueue
  closes/cancels for every position/order in the last snapshot, then wait up to
  `FLATTEN_SETTLE_S * 2**attempt` per round for reports, re-check, up to
  `FLATTEN_ROUNDS`; summary semantics identical (verified counts,
  `positions_remaining`).
- `get_state`: MT5 accounts' `accounts[id]` block and MT5 master positions
  come from the registry (balance, equity, open_pnl, positions with
  current_price and pnl_quote as reported by the terminal).
- Queries: `get_account_details`, `get_deal_history`, `get_order_history`,
  `get_cash_flow`, `get_position_deals`, `get_analytics` for MT5 accounts read
  from `deals`/`mt5_links` in Postgres. Order history is derived from deals
  (one FILLED order per deal, `has_more=false`). Cash flow = deals of type
  BALANCE/CREDIT.
- `routing.build_routing`: an MT5 slave's `SlaveConfig.symbols` is keyed by
  **canonical** name (what master events carry) with `SymbolInfo.name` = the
  MT5 broker name (what the outbox sends).
- Control endpoints: `POST /mt5/hello`, `POST /mt5/sync` (body includes
  `account_id`, resolved by the api), `GET /mt5/status?account_id`.
- `wire_client`/auth loops skip MT5 accounts (no cTrader client to authorise).
  `set_account_status` semantics: `ok` while a report arrived within
  `OFFLINE_AFTER_S = 15`; `degraded` "terminal offline since …" after that
  (checked by a 5 s timer); `degraded` with the broker reason on a rejected
  command. Netting accounts are accepted in both roles; see "Netting
  accounts" under Behaviour rules.

**db/migrations/014_mt5_bridge.sql**

- `accounts.platform TEXT NOT NULL DEFAULT 'ctrader' CHECK (platform IN ('ctrader','mt5'))`
- `accounts.ctid_connection_id` → nullable; CHECK `(platform = 'ctrader') = (ctid_connection_id IS NOT NULL)`
- `CREATE SEQUENCE mt5_account_id_seq START 1000000000000`
- `mt5_links(account_id PK → accounts ON DELETE CASCADE, key_hash TEXT NOT NULL UNIQUE, key_created_at, login BIGINT, broker TEXT, server TEXT, currency TEXT, hedging BOOLEAN, ea_version TEXT, ea_build INT, last_seen_at TIMESTAMPTZ, last_ip TEXT, balance DOUBLE PRECISION, equity DOUBLE PRECISION, created_at)`
- `mt5_commands(id BIGSERIAL PK, account_id → accounts CASCADE, org_id, kind TEXT CHECK IN ('open','close','amend','place_pending','amend_pending','cancel_pending'), payload JSONB NOT NULL, client_order_id TEXT, status TEXT CHECK IN ('queued','sent','done','failed') DEFAULT 'queued', result JSONB, attempts INT DEFAULT 0, created_at, sent_at, done_at)`; index `(account_id, status)`.
- `symbol_aliases(account_id → accounts CASCADE, canonical TEXT, broker_name TEXT, source TEXT CHECK IN ('auto','manual'), PRIMARY KEY (account_id, canonical))`
- `mt5_deal_watermark(account_id PK, last_deal_ticket BIGINT, last_deal_time_ms BIGINT)`
- Events category CHECK unchanged (`control`/`connection`/`slave_action`/`master_event` cover the lane).

**api**

- New router `api/src/api/routes/mt5.py`:
  - Machine door (no session, CSRF-exempt under the new `/api/mt5/` prefix,
    added to `auth.py` beside `/api/webhooks/`):
    - `POST /api/mt5/hello` and `POST /api/mt5/sync`. Order of checks: body
      size cap (hello 512 KB, sync 256 KB) → key from header `X-MirrorFleet-Key`
      (never the URL) → sha256 lookup in `mt5_links` (single indexed read on a
      dedicated connection) → mismatch: `mt5-badkey:{ip}` bucket (10/min) and
      401 → proxy to copier `/mt5/{hello|sync}` with `account_id` → pass the
      copier's response through → throttled `last_seen_at`/`last_ip` write
      (at most every 10 s). Client IP via the same `_client_ip`/trusted-proxy
      logic as the webhook route (shared helper moved to `api/src/api/netaddr.py`).
    - Copier down → 503 with `retry_ms`; the EA backs off to 2 s polls until a
      2xx returns.
  - Operator endpoints (session + role):
    - `POST /api/orgs/{org}/mt5/accounts` (admin) `{nickname}` → creates the
      account (`platform='mt5'`, synthetic id, `role='slave'`, `enabled=false`)
      and link; returns `{account_id, key, install: {...}}` **once**.
    - `POST /api/orgs/{org}/mt5/accounts/{id}/key` (admin) → rotate; old key
      stops immediately.
    - `GET /api/orgs/{org}/accounts` gains `platform` and, for MT5,
      `mt5: {login, broker, server, currency, hedging, ea_version, last_seen_at, connected}`;
      the join to `ctid_connections` becomes LEFT JOIN; `connection_status`
      for MT5 = `connected` / `offline` / `never`.
    - `GET/PUT /api/orgs/{org}/accounts/{id}/symbol-aliases` (trader/admin).
    - `GET /api/orgs/{org}/accounts/{id}/details` for MT5 merges the link
      fields instead of the OAuth grant.
- `GET /downloads/MirrorFleet.mq5` — the EA file, served from the api's static
  dir (copied from `mt5/MirrorFleet.mq5` at image build).
- `require_account_in_org` and every trading route work unchanged (they key on
  the account id).

**dashboard**

- Accounts: *Add MT5 account* (admin) → nickname → one-time key dialog with the
  five install steps and the download link. Platform badge on every row
  (`cTrader` / `MT5`). MT5 rows: subtitle `MT5 · login · broker`, connection
  badge `connected` (green) / `offline` (warn, with "since") / `waiting for the
  terminal` (muted). Details drawer: *Terminal* section (broker, server,
  currency, hedging, EA version, last seen, *Rotate key*) in place of the OAuth
  section; *Symbol mapping* section listing canonical → broker name with an
  inline edit and an "auto" / "manual" tag. Copy that hard-codes cTrader
  ("One cTrader ID grant covers…", "revocable at ctrader.com") becomes
  platform-aware.
- Trade, Positions, History, Performance, Overview: no structural change;
  labels that read `cTID {id}` show `login {login}` for MT5. The Layout caption
  `FP Markets · cTrader` becomes the org's platform list.
- `lib/types.ts`: `Account.platform?: 'ctrader' | 'mt5'`, `Account.mt5?: {...}`
  (optional so existing fixtures stay valid).

**mt5/MirrorFleet.mq5** (new, single file, `#include <Trade/Trade.mqh>` only)

- Inputs: `InpKey` (string), `InpServer` (default `https://mirrorfleet.com`),
  `InpPollMs` (250, min 100), `InpMagic` (20260907), `InpDeviationPoints` (50).
- `OnInit`: report the margin mode in hello (hedging and netting are both
  accepted and shown on the Accounts page); probe `WebRequest` and print the exact "allow this URL" instruction on
  error 4014; send hello (account, broker, server, currency, trade mode,
  symbols in chunks of 300); start `EventSetMillisecondTimer(InpPollMs)`.
- `OnTimer`: one `sync`. `OnTradeTransaction`: on `TRADE_TRANSACTION_DEAL_ADD`
  and order/position changes set `g_dirty = true` so the next timer tick
  syncs immediately (and the tick is advanced to "now" if the previous sync
  finished).
- `sync`: POST JSON `{seq, ts, balance, equity, margin, margin_free,
  positions[], orders[], deals[], acks[]}`; deals are every history deal with
  ticket > watermark (HistorySelect from the last watermark time − 1 h, then
  filter by ticket; at most 200 deals per sync, the rest follow on the next
  syncs so a long-offline backlog never exceeds the body cap); acks carry `{id, ok, retcode, message, position, deal,
  order, price, volume}` for commands executed since the last sync. Parse the
  response lines; execute each command; remember executed ids in memory and in
  `MQL5/Files/MirrorFleet/<login>.acks` (last 500) so a re-delivered id is
  re-acked, never re-executed.
- Command execution via `CTrade` with `SetExpertMagicNumber(InpMagic)`,
  `SetDeviationInPoints`, and the symbol's supported filling mode
  (`SYMBOL_FILLING_MODE`: FOK → IOC → RETURN). `open` → `PositionOpen(symbol,
  type, lots, 0, sl, tp, comment)`; on success read `ResultDeal()` →
  `HistoryDealSelect` → `DEAL_POSITION_ID`, `DEAL_PRICE`, `DEAL_VOLUME`.
  `close` → `PositionClosePartial(ticket, lots)` or `PositionClose(ticket)`.
  `amend` → `PositionModify(ticket, sl, tp)`. `place_pending` → `OrderOpen`;
  `amend_pending` → `OrderModify`; `cancel_pending` → `OrderDelete`.
- Volumes are normalised to the symbol's `SYMBOL_VOLUME_STEP` and clamped to
  min/max before sending; a volume that rounds to zero is acked as failed.
- Chart comment shows `MirrorFleet · connected · last sync 00:00:00` or the
  last error.

## Wire protocol (`copier/src/copier/mt5/protocol.py`)

Request (EA → server), JSON, header `X-MirrorFleet-Key: <key>`:

```
POST /api/mt5/hello
{"v":1,"ea":"1.0.0","build":4400,"login":12345678,"broker":"XYZ Ltd","server":"XYZ-Live3",
 "currency":"USD","hedging":true,"trade_mode":"real","leverage":500,
 "symbols":[{"n":"XAUUSD.r","d":2,"cs":100,"vmin":0.01,"vstep":0.01,"vmax":50,"tm":4}, ...],
 "chunk":1,"chunks":3}

POST /api/mt5/sync
{"v":1,"seq":1042,"ts":1757203200123,"balance":9784.04,"equity":9790.10,"margin":120.5,"margin_free":9669.6,
 "positions":[{"t":669607966,"s":"XAUUSD.r","side":"BUY","lots":0.01,"open":4468.49,"sl":4460.0,"tp":4480.0,
               "price":4470.1,"pnl":1.61,"swap":0,"comment":"copy:m669607900","magic":20260907,"time":1757203100}],
 "orders":[{"t":5551,"s":"XAUUSD.r","type":"BUY_LIMIT","lots":0.01,"price":4450.0,"sl":0,"tp":0,"comment":"","magic":20260907}],
 "deals":[{"t":700001,"pos":669607966,"order":700000,"s":"XAUUSD.r","type":"BUY","entry":"IN","lots":0.01,
           "price":4468.49,"profit":0,"swap":0,"commission":-0.03,"time":1757203100456,"comment":"copy:m669607900","magic":20260907}],
 "acks":[{"id":88,"ok":true,"retcode":10009,"msg":"done","pos":669607966,"deal":700001,"order":700000,"price":4468.49,"lots":0.01}]}
```

The **hello** reply is the one JSON answer the EA reads: `{"last_deal_ticket": N}`
(a single integer the EA extracts with a string search, so a restarted terminal
resumes its deal watermark from the server's); any non-2xx hello answer means
"back off and retry". The **sync** reply is `text/plain`, one command per line,
tab-separated, first line a status line; the api turns any copier failure
(unreachable, timeout, non-200) into `RETRY <server_ms> 2000` so the terminal
never has to parse JSON on the sync path:

```
OK	1757203200400	250
CMD	88	open	XAUUSD.r	BUY	0.01	4460.00	4480.00	copy:m669607900	cm669607900.1000000000001
CMD	89	close	669607966	0.01
CMD	90	amend	669607966	4462.00	4482.00
CMD	91	place_pending	XAUUSD.r	BUY_LIMIT	0.01	4450.00	0	0	0	copy:o5551	co5551.1000000000001
CMD	92	amend_pending	5551	0.01	4451.00	0	0
CMD	93	cancel_pending	5551
```

Status line: `OK <server_ms> <next_poll_ms>` or `RETRY <server_ms> <next_poll_ms>`
(copier unreachable; nothing was applied) or `STOP <server_ms> <reason>` (key revoked,
account removed — the EA stops polling and shows the reason).
Command fields are positional and never contain tabs; the copier escapes none
because symbols, comments and ids cannot contain tabs. Prices use the symbol's
digits; `0` means "none".

Ordering guarantees: commands are delivered in id order; the EA executes them
in order; the server marks a command `sent` when delivered, `done`/`failed`
on ack, and re-delivers a `sent` command without an ack after 10 s at most
`3` times before failing it as "no ack from terminal".

## Behaviour rules

- **Sizing**: MT5 follower volume = `mirror_volume(master_volume, master_lot_size, multiplier, 100, step_centilots)`; a result below the symbol's min is an `Alert` "mirrored volume rounds to 0", like cTrader.
- **Protection on copies**: SL/TP travel inside the `open` command (MT5 accepts absolute prices on market orders), so a copy is protected from its first tick; the `_protect_new_copy` amend is skipped for MT5 targets. If the master's protection changes before the copy is acked, the pending change is applied on ack (same rule as today).
- **Opposite positions, hedging**: followers copy exactly what the master does; two opposite copies may coexist, as on cTrader. On a netting follower they net against each other inside the symbol's single position; see "Netting accounts".
- **Disconnected terminal**: commands queue; `open`/`place_pending` expire after 30 s (mapping failed, `slave_action` warning); the account shows *offline* after 15 s without a report; on reconnect the EA's first sync re-sends the full snapshot and any deals since the watermark, so History catches up and reconcile sees the truth.
- **Restart safety (EA)**: executed command ids persist to a file; re-delivered ids are re-acked without execution. **Restart safety (copier)**: outbox and watermark are in Postgres; the registry rebuilds from the next report.
- **Kill switch**: Close all includes MT5 accounts; each MT5 account's summary reports verified counts from the terminal's reports, never sends.
- **Netting accounts** (`ACCOUNT_MARGIN_MODE_RETAIL_NETTING`; hello says `hedging:false`): supported in both roles. `mt5_links.hedging` records the mode; `mt5_status` and the Accounts page show it; the EA never stops on netting.
  - *As a follower.* Every copy on a symbol lives inside that symbol's single net position; a mapping keeps its own `slave_volume`, and its side is recorded on its `open` command. A master close for a mapping is executed as a **market order on the opposite side for the mapping's volume** (that is how a netting account reduces, closes or flips), queued as an `open` command whose `client_order_id` is the mapping's id with the suffix `:close`, so the ack is matched to exactly that mapping. A close the terminal reports on its own (stop, target, manual close) reduces the mappings that share that net position **oldest-first** (`reduce_position_mappings_fifo`). One stop and one target per symbol: the most recently opened or amended master position on that symbol sets them, and an info event records each override. Reconcile compares the sum of active mapping volumes per net position with the terminal's net volume; a shortfall is `missing_slave_copy`, an excess `orphan_slave_position`, as today.
  - *As a master.* The terminal's net position is expanded into **virtual master positions** kept in `mt5_net_ledger` (persisted, so a restart keeps them). A deal with entry `IN` opens a virtual position whose id is the deal ticket → `MasterPositionOpened`; entry `OUT` consumes that symbol's virtual positions oldest-first → one `MasterPositionClosed` per consumed position (partial when it survives); `INOUT` closes them all and opens the remainder in the new direction; a stop/target change on the net position → `MasterPositionSLTPAmended` for every virtual position of that symbol. The reconciler and `get_state` see the virtual positions (current price from the net position, P&L split by volume share), so drift and copies line up by virtual id.
  - *Trade page and Close all* act on the net position (`close` = `PositionClose` on the net ticket), on both modes.
- **Symbol without a match**: an intent for an unmatched symbol becomes an `Alert` naming the symbol and the Details panel where the mapping is set; nothing is sent.
- **Balance-after on deals**: MT5 stores no per-deal balance. On ingest the copier computes `balance_after` for the batch backwards from the reported current balance (each deal's `profit + swap + commission` subtracted in reverse order), stores it, and marks the field approximate in the API (`balance_after_estimated: true`); the History page shows it unchanged.
- **Money**: `profit`, `swap`, `commission` are in the account currency as MT5 reports them; `gross_profit` = `profit`.
- **Security**: key checked before any other read; only sha256 stored; wrong key → 401 after the per-IP bucket; no key ever in a URL or log (`_redact` scrubs the `mt5_` prefix by value exactly like `tvw_`); the EA file contains no secrets; the api never accepts commands from the EA — the EA only reports and acknowledges.

## Testing

- **copier unit** (`tests/unit/test_mt5_*.py`): protocol encode/decode; outbox enqueue from each intent type; ack → mapping activate/reduce/fail; expiry; ingress deal → MasterEvent (IN/OUT/INOUT, partials, SL/TP, pendings) with alias translation; symbol auto-match table and crc32 ids; registry snapshot → reconciler; get_state block; queries from DB (deals, orders derived, cashflow, balance_after estimation); flatten via outbox; offline detection.
- **copier integration**: `copier/src/copier/testing/fake_ea.py` — a Python stand-in for the EA speaking the exact wire protocol against an in-memory book in hedging or netting mode (opens, partial closes, SL/TP, pendings, rejections, disconnects, restarts). Scenarios: cTrader master (existing fake server) → MT5 follower; MT5 master → cTrader followers; Trade-page order on MT5; Close all across both platforms; terminal offline then back; duplicate delivery; command expiry.
- **api tests** (`api/tests/test_mt5.py`): door ordering (size → key → lookup → proxy), bad-key bucket, CSRF exemption, last-seen throttling, 503/RETRY mapping; operator endpoints (create, key shown once, rotate, aliases, accounts listing with platform, details merge, cross-org 404s).
- **dashboard tests**: Accounts (add dialog, key once, badges, MT5 details, symbol mapping edit), Layout caption, History labels.
- **EA**: cannot be compiled in this environment (no MetaEditor). Written to compile on build 4400+, with a `#property strict`-equivalent discipline; the owner compiles once in MetaEditor and reports errors, which are fixed before release. The fake EA keeps the server side honest in the meantime.

## Alternatives rejected

- **MT5 imitating a cTrader client** (protobuf as the interface): fragile, couples MT5 to cTrader's wire format, fakes responses.
- **Broker-adapter carve-out first**: correct long-term shape, but a refactor of the live cTrader path before any MT5 value, with regression risk on a system trading real money. Revisit when a third platform arrives.
- **MetaApi** (previous plan): monthly fee per account and password custody; unnecessary once the EA runs on the owner's VPS.

## Rollout

1. Migration 014 (additive; `ctid_connection_id` nullable is safe for existing rows).
2. Copier + api deploy (MT5 lane dormant until an MT5 account exists).
3. Dashboard deploy; owner adds an MT5 account on a **demo** MT5 login first, installs the EA, confirms *connected*.
4. Follower test: cTrader master trade → MT5 copy with SL/TP; Trade-page order on MT5; Close all.
5. Master test: switch role, trade on MT5 → cTrader followers.
6. Live account.
