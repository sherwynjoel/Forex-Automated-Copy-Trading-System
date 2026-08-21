# MT4/MT5 Support — Implementation Plan

Goal: MirrorFleet copies between cTrader, MT5, and MT4 accounts in one fleet,
like TradersConnect ($10/acct/mo, MT-first) and Duplikium. Decided 2026-08-21.

## Ground truth (researched 2026-08-21)

- No official MetaQuotes API and no OAuth exist for retail MT4/MT5. Every
  cloud copier collects account login + broker server + password.
- Investor (read-only) password suffices for a MASTER; slaves need the full
  trading password (orders must be placed).
- Bridge options: MetaApi (metaapi.cloud — hosted terminals, official Python
  SDK, ~$10–30/acct/mo, free single-account tier to prototype) or mtapi.io
  (protocol emulation, $5→$1/acct/mo at scale, $1,000/mo self-hosted
  unlimited; fragility risk when MetaQuotes changes the protocol).
  MT5 Manager/Web API is brokers-only — not an option.
- Decision: **Phase 1 uses MetaApi, slaves-only** (cTrader master → MT
  slaves). Migrate the adapter to mtapi.io/self-hosted later if volume makes
  the unit economics matter.

## Phases

### Phase 0 — Broker-adapter carve-out (pure refactor, no behavior change)
- Define `copier/src/copier/broker/base.py`: BrokerAdapter protocol derived
  from how the engine actually uses CTraderClient + queries today:
  connection lifecycle (connect/auth/disconnected callbacks), account
  discovery, symbol catalog, reconcile snapshot (positions + working
  orders), execution-event stream, place/close/cancel, spot subscribe +
  quote, deal history, balance.
- Wrap the existing cTrader client as `CTraderAdapter`; reroute
  main.py/dispatch/reconcile/state through the interface incrementally,
  keeping all 375 copier tests green at every step.
- DB: `accounts.platform` column (default 'ctrader'); surface it through
  /accounts and show a platform badge chip on the Accounts page.

### Phase 1 — MT5/MT4 slave accounts via MetaApi
- `MetaApiAdapter` implementing the same protocol (metaapi-cloud-sdk is
  asyncio; bridge to Twisted via deferToThread or asyncioreactor — decide at
  the start of the phase).
- Credential vault: encrypted login/server/password per MT account using the
  existing Fernet setup (never returned by any API).
- Connect UX: "Add MT4/MT5 account" on the Accounts page — platform picker,
  login, server, password form (next to Connect cTrader ID).
- Symbol mapping table (per broker/account: canonical name ↔ broker name,
  e.g. XAUUSD ↔ GOLD ↔ XAUUSD.r) with sensible auto-matching
  (suffix-stripping) and a manual override UI on account Details.
- Copy path: master fill events → sizing (existing) → MetaApi order on MT
  slaves; SL/TP + partial closes included; kill-switch flatten covers MT.
- Needs from the owner: a metaapi.cloud account + API token (free tier =
  1 account for prototyping), and a demo MT5 account (any broker / prop-firm
  demo) to copy into.

### Phase 2 — MT masters + full parity
- Investor-password (read-only) MT masters; MT accounts on the Trade ticket,
  Positions, History, Performance, quotes; per-platform latency stats.

### Later / optional
- mtapi.io or self-hosted terminal adapter for cost; TradeLocker/DXtrade
  adapters (real REST APIs); per-account billing to cover bridge fees.

## Risks
- Bridge cost per connected MT account (price above the bridge fee).
- Trading-password custody: encrypt at rest, never log, document clearly.
- MetaApi reliability is mixed in reviews — keep the adapter swappable.
- asyncio↔Twisted interop is the main technical unknown of Phase 1.
