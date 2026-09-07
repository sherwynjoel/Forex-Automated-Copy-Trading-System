# MT5 Bridge — Interface Contract

Binding for all four implementation plans (copier, api, dashboard, EA). A plan
may add private helpers freely but MUST use these names, shapes and paths
exactly where it touches another subsystem. Spec: `docs/superpowers/specs/2026-09-07-mt5-bridge-design.md`.

## 1. Database — `db/migrations/014_mt5_bridge.sql`

```sql
-- accounts: platform + cTrader link becomes optional
ALTER TABLE accounts ADD COLUMN platform TEXT NOT NULL DEFAULT 'ctrader'
    CHECK (platform IN ('ctrader', 'mt5'));
ALTER TABLE accounts ALTER COLUMN ctid_connection_id DROP NOT NULL;
ALTER TABLE accounts ADD CONSTRAINT accounts_platform_link
    CHECK ((platform = 'ctrader') = (ctid_connection_id IS NOT NULL));

CREATE SEQUENCE mt5_account_id_seq START 1000000000000;

CREATE TABLE mt5_links (
    account_id     BIGINT PRIMARY KEY REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    key_hash       TEXT NOT NULL UNIQUE,          -- sha256 hex of the key; the key itself is never stored
    key_created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    login          BIGINT,
    broker         TEXT,
    server         TEXT,
    currency       TEXT,
    hedging        BOOLEAN,
    trade_mode     TEXT,                          -- 'demo' | 'contest' | 'real'
    leverage       INTEGER,
    ea_version     TEXT,
    ea_build       INTEGER,
    last_seen_at   TIMESTAMPTZ,
    last_ip        TEXT,
    balance        DOUBLE PRECISION,
    equity         DOUBLE PRECISION,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mt5_commands (
    id              BIGSERIAL PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    org_id          BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('open','close','amend','place_pending','amend_pending','cancel_pending')),
    payload         JSONB NOT NULL,
    client_order_id TEXT,
    status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','done','failed')),
    result          JSONB,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    done_at         TIMESTAMPTZ
);
CREATE INDEX mt5_commands_open ON mt5_commands (account_id, status) WHERE status IN ('queued','sent');

CREATE TABLE symbol_aliases (
    account_id  BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    canonical   TEXT NOT NULL,                    -- the name master events carry, e.g. XAUUSD
    broker_name TEXT NOT NULL,                    -- the MT5 broker's name, e.g. XAUUSD.r
    source      TEXT NOT NULL CHECK (source IN ('auto','manual')),
    PRIMARY KEY (account_id, canonical)
);

CREATE TABLE mt5_deal_watermark (
    account_id        BIGINT PRIMARY KEY REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    last_deal_ticket  BIGINT NOT NULL DEFAULT 0,
    last_deal_time_ms BIGINT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`payload` shapes by `kind` (all prices are floats already rounded to the
symbol's digits; `0`/absent = none; `lots` is a float in lots, NOT centilots):

| kind | payload |
|---|---|
| open | `{"symbol": "XAUUSD.r", "side": "BUY"|"SELL", "lots": 0.01, "sl": 4460.0, "tp": 4480.0, "comment": "copy:m669607900"}` |
| close | `{"position": 669607966, "lots": 0.01}` (`lots` absent or `0` = full close) |
| amend | `{"position": 669607966, "sl": 4462.0, "tp": 4482.0}` |
| place_pending | `{"symbol": "...", "type": "BUY_LIMIT"|"SELL_LIMIT"|"BUY_STOP"|"SELL_STOP", "lots": 0.01, "price": 4450.0, "sl": 0, "tp": 0, "expiry_ms": 0, "comment": "copy:o5551"}` |
| amend_pending | `{"order": 5551, "lots": 0.01, "price": 4451.0, "sl": 0, "tp": 0}` |
| cancel_pending | `{"order": 5551}` |

`result` on done/failed: `{"ok": bool, "retcode": int, "message": str, "position": int|null, "deal": int|null, "order": int|null, "price": float|null, "lots": float|null}`.

Keys: `mt5_` + `secrets.token_urlsafe(32)` (prefix constant `MT5_KEY_PREFIX = "mt5_"`); redaction by value on that prefix exactly like `tvw_`.

## 2. Copier — package `copier/src/copier/mt5/`

Units are **centilots** everywhere inside the copier for MT5 accounts:
`lot_size = 100`, `volume = round(lots * 100)`. Conversions happen only in
`protocol.py` (report → centilots) and `outbox.py` (payload lots ← centilots).

### `protocol.py`

```python
from dataclasses import dataclass, field

MT5_KEY_PREFIX = "mt5_"
PROTOCOL_VERSION = 1
CENTILOTS = 100                       # protocol units per 1.00 lot for every MT5 symbol
DEFAULT_POLL_MS = 250
RETRY_POLL_MS = 2000
MAX_DEALS_PER_SYNC = 200

@dataclass(frozen=True)
class HelloSymbol:
    name: str; digits: int; contract_size: float
    volume_min: float; volume_step: float; volume_max: float; trade_mode: int

@dataclass(frozen=True)
class HelloReport:
    ea_version: str; ea_build: int; login: int; broker: str; server: str
    currency: str; hedging: bool; trade_mode: str; leverage: int
    symbols: list[HelloSymbol]; chunk: int; chunks: int

@dataclass(frozen=True)
class ReportPosition:
    ticket: int; symbol: str; side: str          # "BUY"|"SELL"
    volume: int                                  # centilots
    open_price: float; stop_loss: float | None; take_profit: float | None
    current_price: float | None; pnl: float; swap: float
    comment: str; magic: int; opened_at_ms: int

@dataclass(frozen=True)
class ReportOrder:
    ticket: int; symbol: str; order_type: str    # "BUY_LIMIT"|"SELL_LIMIT"|"BUY_STOP"|"SELL_STOP"
    volume: int; price: float; stop_loss: float | None; take_profit: float | None
    comment: str; magic: int

@dataclass(frozen=True)
class ReportDeal:
    ticket: int; position: int; order: int; symbol: str
    deal_type: str                               # "BUY"|"SELL"|"BALANCE"|"CREDIT"|"OTHER"
    entry: str                                   # "IN"|"OUT"|"INOUT"|"OUT_BY"|"" (balance ops)
    volume: int; price: float; profit: float; swap: float; commission: float
    time_ms: int; comment: str; magic: int

@dataclass(frozen=True)
class Ack:
    command_id: int; ok: bool; retcode: int; message: str
    position: int | None; deal: int | None; order: int | None
    price: float | None; volume: int | None      # centilots

@dataclass(frozen=True)
class SyncReport:
    seq: int; ts_ms: int; balance: float; equity: float; margin: float; margin_free: float
    positions: list[ReportPosition]; orders: list[ReportOrder]
    deals: list[ReportDeal]; acks: list[Ack]

@dataclass(frozen=True)
class Command:
    id: int; kind: str; payload: dict            # payload per the table in section 1
    client_order_id: str | None = None

class ProtocolError(ValueError): ...

def parse_hello(body: dict) -> HelloReport: ...   # raises ProtocolError on shape errors
def parse_sync(body: dict) -> SyncReport: ...     # lots → centilots; missing optional fields → None/0
def encode_response(status: str, server_ms: int, next_poll_ms: int,
                    commands: list[Command], last_deal_ticket: int | None = None) -> str
    # status: "OK" | "RETRY" | "STOP"; for STOP the third field is the reason text
    # Lines: "OK\t<server_ms>\t<next_poll_ms>" or "RETRY\t<server_ms>\t<next_poll_ms>" or
    #        "STOP\t<server_ms>\t<reason>", then one "CMD\t..." per command (OK only).
    # The sync response never carries last_deal_ticket; the hello reply is JSON (control section below).
    # (The optional last_deal_ticket fourth field is kept for callers that want it; the EA ignores it.)
def command_line(cmd: Command) -> str         # exact field order per spec §Wire protocol
def parse_response(text: str) -> tuple[list[str], list[Command]]   # used by the fake EA and tests
```

Command line field orders (tab-separated, after `CMD\t<id>\t<kind>`):
- open: `symbol side lots sl tp comment client_order_id`
- close: `position lots` (`0` = full)
- amend: `position sl tp`
- place_pending: `symbol type lots price sl tp expiry_ms comment client_order_id`
- amend_pending: `order lots price sl tp`
- cancel_pending: `order`

### `symbols.py`

```python
def normalise_symbol(name: str) -> str            # "XAUUSD.r" -> "XAUUSD", "GOLDm" -> "XAUUSD" (via SYNONYMS)
SYNONYMS: dict[str, str]                           # normalised stem -> canonical
def symbol_id_for(name: str, taken: set[int]) -> int     # zlib.crc32(name) & 0x7FFFFFFF, +1 probing
def symbol_infos_from_hello(symbols: list[HelloSymbol]) -> list[SymbolInfo]
    # SymbolInfo(symbol_id, name=broker name, digits, lot_size=100,
    #            min_volume=round(volume_min*100), step_volume=max(1, round(volume_step*100)))
def auto_match(canonical_names: Iterable[str], broker_names: Iterable[str]) -> dict[str, str]
    # canonical -> broker_name; exact match first, then normalised, then synonyms; unmatched omitted
```

### `registry.py`

```python
OFFLINE_AFTER_S = 15.0

class MT5Registry:
    def __init__(self, repo, clock=None): ...
    def update_from_hello(self, account_id: int, org_id: int, hello: HelloReport, now: float) -> None
    def update_from_sync(self, account_id: int, org_id: int, report: SyncReport, now: float) -> None
        # stores snapshot; persists via repo.upsert_positions / close_missing_positions
    def snapshot(self, account_id: int) -> tuple[list[PositionSnapshot], list[OrderSnapshot]] | None
        # PositionSnapshot/OrderSnapshot from engine/reconcile.py; label = "copy:m<master>" when a
        # mapping row with slave_position_id == ticket exists (looked up via repo.mapping_rows), else the comment
    def account_block(self, account_id: int) -> dict | None
        # {"balance","equity","open_pnl","positions":[{position_id,symbol_id,symbol,side,volume,entry_price,
        #   stop_loss,take_profit,pnl_quote,current_price}]}  -- get_state shape
    def last_seen(self, account_id: int) -> float | None
    def is_online(self, account_id: int, now: float) -> bool
    def hedging(self, account_id: int) -> bool | None
    def symbol_by_name(self, account_id: int, name: str) -> SymbolInfo | None
```

### `outbox.py`

```python
OPEN_COMMAND_TTL_S = 30.0
REDELIVER_AFTER_S = 10.0
MAX_ATTEMPTS = 3

@dataclass(frozen=True)
class AckOutcome:
    command_id: int; kind: str; client_order_id: str | None
    ok: bool; message: str; position: int | None; order: int | None
    price: float | None; volume: int | None      # centilots

class MT5Outbox:
    def __init__(self, repo, clock=None): ...
    def enqueue_intent(self, intent: SlaveIntent, org_id: int, broker_symbol: str) -> int
        # maps OpenMarket/ClosePosition/AmendPositionSLTP/PlacePending/AmendPending/CancelPending → row
        # client_order_id = dispatch.client_order_id_for(intent)
    def enqueue(self, account_id: int, org_id: int, kind: str, payload: dict,
                client_order_id: str | None = None) -> int
    def deliverable(self, account_id: int, now: float) -> list[Command]
        # expires stale opens (status failed, result message "terminal offline"), re-delivers
        # 'sent' rows older than REDELIVER_AFTER_S (attempts < MAX_ATTEMPTS), marks returned rows 'sent'
    def apply_acks(self, account_id: int, acks: list[Ack]) -> list[AckOutcome]
        # unknown/duplicate command ids are ignored (idempotent)
    def pending_count(self, account_id: int) -> int
```

Repo additions (`copier/src/copier/db/repo.py`):

```python
def load_mt5_link(self, account_id) -> dict | None
def upsert_mt5_link_hello(self, account_id, *, login, broker, server, currency, hedging, trade_mode, leverage, ea_version, ea_build) -> None
def touch_mt5_link(self, account_id, *, balance, equity, seen_at) -> None   # both the api door and the copier's mt5_sync call it, each throttled to once per 10 s per account
def enqueue_mt5_command(self, account_id, org_id, kind, payload, client_order_id) -> int
def mt5_commands_open(self, account_id) -> list[dict]          # status in (queued, sent), id order
def mark_mt5_commands_sent(self, ids: list[int], sent_at) -> None   # attempts += 1
def complete_mt5_command(self, command_id, ok: bool, result: dict, done_at) -> dict | None  # returns the row (kind, client_order_id, payload)
def fail_stale_mt5_opens(self, account_id, older_than) -> list[int]
def load_symbol_aliases(self, account_id) -> dict[str, str]     # canonical -> broker_name
def save_symbol_aliases(self, account_id, aliases: dict[str, str], source: str) -> None   # upsert; manual never overwritten by auto
def mt5_watermark(self, account_id) -> tuple[int, int]          # (last_deal_ticket, last_deal_time_ms)
# mapping_rows() is unchanged; tests that need master_fill_price read it with raw SQL as test_repo.py does
def set_mt5_watermark(self, account_id, ticket, time_ms) -> None
def upsert_mt5_deals(self, account_id, org_id, rows: list[dict]) -> int   # into deals; balance_after estimated by caller
```

`load_accounts()` gains `platform: str` on `AccountRow`. `build_routing` keys an
MT5 slave's `SlaveConfig.symbols` by canonical name using
`load_symbol_aliases` (alias → SymbolInfo of the broker symbol; symbols with no
alias are keyed by their own name).

### `ingress.py`

```python
def master_events_from_report(report: SyncReport, previous: tuple[list[PositionSnapshot], list[OrderSnapshot]] | None,
                              aliases_reverse: dict[str, str], symbols: dict[str, SymbolInfo]) -> list[MasterEvent]
    # deals: entry IN → MasterPositionOpened(position_id=deal.position, symbol_name=canonical, side, volume, lot_size=100,
    #        stop_loss/take_profit from the matching ReportPosition if present, entry_price=deal.price)
    #        entry OUT/OUT_BY/INOUT → MasterPositionClosed(position_id, symbol_name, closed_volume=deal.volume,
    #        remaining_volume = volume of that position still in report.positions, else 0)
    # positions vs previous: SL/TP changed → MasterPositionSLTPAmended
    # orders vs previous: new → MasterPendingPlaced; changed → MasterPendingReplaced; gone without a deal → MasterPendingCancelled;
    #        gone with a deal whose order == ticket → MasterPendingFilled(order_id, position_id)
```

### `lane.py` — operator actions and queries for one MT5 account

```python
class MT5Lane:
    def __init__(self, app): ...                    # app: CopierApp (repo, registry, outbox, clock)
    def place_order(self, account_id, org_id, symbol, side, order_type, volume_lots, limit_price, stop_price,
                    stop_loss, take_profit, actor) -> dict     # {"status":"submitted", ..., "command_id"}
    def close_position(self, account_id, position_id, volume_lots, actor) -> dict
    def amend_position_sltp(self, account_id, position_id, stop_loss, take_profit, actor) -> dict
    def cancel_order(self, account_id, order_id, actor) -> dict
    def flatten(self, account_id) -> Deferred[dict]  # same summary keys as _flatten_account
    def details(self, account_id) -> dict            # AccountDetails shape (+ "platform":"mt5", "mt5":{...})
    def deal_history(self, account_id, from_ms, to_ms) -> dict   # {"deals":[...], "has_more": False}
    def order_history(self, account_id, from_ms, to_ms) -> dict  # derived from deals
    def cash_flow(self, account_id, from_ms, to_ms) -> dict      # {"entries":[...]}
    def position_deals(self, account_id, position_id, from_ms, to_ms) -> dict
```

### Service and control

```python
# engine/service.py
@dataclass(frozen=True)
class SlaveFill:            # platform-neutral slave fill
    account_id: int; client_order_id: str | None; position_id: int; filled_volume: int
    fill_price: float | None; closed_volume: int | None; label: str

class CopierService:
    def act_on_master_event(self, org_id, master_account_id, normalized: MasterEvent, *, source: str) -> None
    def handle_slave_fill(self, org_id, fill: SlaveFill) -> None
    def handle_slave_rejection(self, org_id, account_id, client_order_id, reason) -> None

# main.py (CopierApp)
self.mt5_registry: MT5Registry; self.mt5_outbox: MT5Outbox; self.mt5_lane: MT5Lane
def mt5_hello(self, account_id: int, body: dict) -> dict      # {"last_deal_ticket": int}
def mt5_sync(self, account_id: int, body: dict) -> str        # the EA response text (encode_response)
def mt5_status(self, account_id: int) -> dict                  # {"online","last_seen_at","pending_commands","hedging"}

# engine/control.py
POST /mt5/hello   {"account_id": int, "org_id": int, "report": {...}}   -> 200 JSON {"last_deal_ticket": N}
POST /mt5/sync    {"account_id": int, "org_id": int, "report": {...}}   -> 200 text/plain (EA response text)
GET  /mt5/status?account_id=N                                          -> 200 JSON
```

Fake EA: `copier/src/copier/testing/fake_ea.py` — `class FakeEA(base_url, key=None, account_id=None)`
speaking the exact wire format against an in-memory hedging book; used by
integration tests through the copier control port (JSON bodies with
`account_id`) and, optionally, through the api (headers).

## 3. API — `api/src/api/routes/mt5.py`

Shared IP helper moved to `api/src/api/netaddr.py`: `client_ip(request, cfg) -> str`
(the former `webhooks._client_ip` + `_is_trusted_proxy`; webhooks imports it).

### Machine door (no session; CSRF-exempt prefix `/api/mt5/`)

```
POST /api/mt5/hello     header X-MirrorFleet-Key: mt5_...   body: hello JSON (spec)
POST /api/mt5/sync      header X-MirrorFleet-Key: mt5_...   body: sync JSON (spec)
```
Order: size cap (hello 512 KB, sync 256 KB → 413) → key header present (400)
→ sha256 lookup `mt5_links.key_hash` joined to `accounts` (org_id, enabled,
status) on a dedicated connection → miss: bucket `mt5-badkey:{ip}` (10/min),
401 `{"status":"rejected","reason":"unknown key"}` → account removed/key
rotated: same 401 → proxy `POST {copier}/mt5/{hello|sync}` with
`{"account_id","org_id","report": body}` (timeout 2 s) → pass the copier's
body and content-type through (200) → throttled write of `last_seen_at`,
`last_ip`, `balance`, `equity` (at most once per 10 s per account).
Any copier failure on sync (unreachable, timeout, or ANY non-200 answer) → 200
`text/plain` `RETRY\t<ms>\t2000`, so the terminal never sees JSON on the sync
path. On hello, the same rule: any copier failure (unreachable, timeout, or ANY
non-200 answer, its own 400 included) → 503 JSON `{"status": "failed", "reason":
"<text for the log>", "retry_ms": 2000}`. A failed proxy never touches the link.
The EA treats any non-2xx hello as back-off-and-retry.

### Operator endpoints (session + role)

```
POST /api/orgs/{org}/mt5/accounts            admin   {"nickname": str}
  -> 201 {"account_id": int, "key": "mt5_...", "download_url": "/downloads/MirrorFleet.mq5",
          "install": ["...five steps..."]}
POST /api/orgs/{org}/mt5/accounts/{id}/key   admin   -> 200 {"key": "mt5_..."}   (rotation; old key dead)
GET  /api/orgs/{org}/accounts                 viewer  rows gain:
       "platform": "ctrader"|"mt5",
       "mt5": null | {"login","broker","server","currency","hedging","trade_mode","ea_version",
                      "last_seen_at","connected": bool}       (connected = last_seen_at within 15 s)
       "connection_status": for mt5 -> "connected"|"offline"|"never"
GET  /api/orgs/{org}/accounts/{id}/symbol-aliases   trader  -> {"aliases":[{"canonical","broker_name","source"}],
                                                                "broker_symbols":[names...]}
PUT  /api/orgs/{org}/accounts/{id}/symbol-aliases   admin   {"aliases": {"XAUUSD": "GOLD.r", ...}}  (manual; "" removes)
GET  /api/orgs/{org}/accounts/{id}/details          viewer  for mt5: copier details + "mt5": {...link fields...}
GET  /downloads/MirrorFleet.mq5                     public  the EA file (Content-Disposition: attachment)
```
Creating an MT5 account: `INSERT accounts (ctid_trader_account_id = nextval('mt5_account_id_seq'),
ctid_connection_id NULL, platform 'mt5', trader_login 0, is_live false, role 'slave', enabled false,
nickname, org_id)` + `mt5_links` row; audit event `control/info` action `mt5_account_added`;
then `POST {copier}/reload`.

`accounts.py` list query: `LEFT JOIN ctid_connections`; `connection_status`
for cTrader unchanged.

## 4. Dashboard — `dashboard/src/lib/types.ts`

```ts
export interface Account {
  // existing fields unchanged ...
  platform?: 'ctrader' | 'mt5'          // absent = ctrader (older api)
  mt5?: {
    login: number | null; broker: string | null; server: string | null; currency: string | null
    hedging: boolean | null; trade_mode: string | null; ea_version: string | null
    last_seen_at: string | null; connected: boolean
  } | null
}
export interface Mt5AccountCreated { account_id: number; key: string; download_url: string; install: string[] }
export interface SymbolAliases { aliases: { canonical: string; broker_name: string; source: 'auto'|'manual' }[]; broker_symbols: string[] }
```

## 5. EA — `mt5/MirrorFleet.mq5`

- Endpoints: `{InpServer}/api/mt5/hello`, `{InpServer}/api/mt5/sync`; header
  `X-MirrorFleet-Key: {InpKey}\r\nContent-Type: application/json\r\n`.
- Response grammar and command field orders: section 2 (`command_line`).
- Ack persistence: `MQL5/Files/MirrorFleet/<login>.acks` (one id per line, last 500).
- Magic default 20260907; comment on copies as given in the command; volumes
  normalised to `SYMBOL_VOLUME_STEP`, clamped to min/max.
- Version string `"1.0.0"`, `#property version "1.00"`.
