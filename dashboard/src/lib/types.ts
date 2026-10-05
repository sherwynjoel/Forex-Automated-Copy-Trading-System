import type { Role } from './roles'

export interface OrgSummary {
  id: number
  name: string
  role: Role
}

export interface MpinState {
  /** True on a half session: email and password were right, the MPIN is still owed. */
  pending: boolean
  /** False for a user who has never set an MPIN (the /mpin page shows Set mode). */
  set: boolean
}

export interface Me {
  user: { id: number; email: string; display_name: string }
  orgs: OrgSummary[]
  mpin?: MpinState
}

/** What /api/me answers on a half session: only what to do next. */
export interface MpinPending {
  mpin: MpinState
}

export function isMpinPending(me: Me | MpinPending): me is MpinPending {
  return !('user' in me) && me.mpin?.pending === true
}

export interface Member {
  user_id: number
  email: string
  display_name: string
  role: Role
  joined_at: string
}

export interface Invite {
  id: number
  role: Role
  created_at: string
  expires_at: string
  consumed: boolean
}

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

export interface OpenPosition {
  position_id: number
  symbol_id: number
  symbol?: string | null
  side: string
  volume: number
  volume_lots?: string | null
  price: number
  label: string
  stop_loss?: number | null
  take_profit?: number | null
  swap?: number | null
  open_timestamp?: number | null
}

export interface WorkingOrder {
  order_id: number
  symbol_id: number
  symbol?: string | null
  side: string
  volume: number
  volume_lots?: string | null
  order_type: string
  limit_price?: number | null
  stop_price?: number | null
  label: string
}

export interface AccountDetails {
  account_id: number
  trader_login?: number | null
  balance?: number | null
  deposit_currency?: string | null
  leverage?: number | null
  max_leverage?: number | null
  broker_name?: string | null
  registration_timestamp?: number | null
  account_type?: string | null
  access_rights?: string | null
  swap_free?: boolean | null
  is_limited_risk?: boolean
  open_positions: OpenPosition[]
  pending_orders: WorkingOrder[]
  // Merged in by the api from its own database:
  nickname?: string | null
  role?: string
  enabled?: boolean
  multiplier?: number
  status?: string
  last_error?: string | null
  is_live?: boolean
  connection?: {
    granted_at: string
    expires_at: string
    status: string
    scope: string
  }
}

export interface DealClose {
  entry_price: number
  gross_profit: number
  swap: number
  commission: number
  balance: number
  closed_volume: number
  closed_volume_lots?: string | null
}

export interface Deal {
  deal_id: number
  order_id: number
  position_id: number
  symbol_id: number
  symbol?: string | null
  side: string
  volume: number
  filled_volume: number
  volume_lots?: string | null
  execution_price?: number | null
  status: string
  commission?: number | null
  create_timestamp: number
  execution_timestamp: number
  close: DealClose | null
}

export interface HistoricalOrder {
  order_id: number
  symbol_id: number
  symbol?: string | null
  side: string
  volume: number
  volume_lots?: string | null
  order_type: string
  status: string
  limit_price?: number | null
  stop_price?: number | null
  execution_price?: number | null
  executed_volume?: number | null
  position_id?: number | null
  label: string
  open_timestamp?: number | null
  update_timestamp?: number | null
  stop_loss?: number | null
  take_profit?: number | null
}

export interface TradeSymbol {
  name: string
  symbol_id: number
  digits: number
  min_volume_lots?: number | null
  step_volume_lots?: number | null
  /** Protocol units per 1.00 lot; needed to price a money-denominated
   *  stop or target. Absent on older responses. */
  lot_size?: number | null
  /** Round-trip commission on one unit, learned from this account's own
   *  closed trades. Null means never observed -- adjust nothing. */
  commission_per_unit?: number | null
}

export interface FlattenSummary {
  account_id: number
  /** VERIFIED closed: what was open when the flatten began, minus what is
   *  still open now. Not the number of close requests that were sent --
   *  that was the old meaning, and it reported 66 positions closed on a run
   *  where the broker refused all 66. */
  positions_closed: number
  orders_cancelled: number
  /** Still open after every attempt. Empty means the account is flat; a
   *  non-empty list is risk the operator is still carrying. Null when the
   *  account could not be reached at all, which is not the same as flat. */
  positions_remaining?: number[] | null
  orders_remaining?: number[] | null
  /** Close-and-verify rounds this account needed. */
  rounds?: number
  error?: string | null
}

export interface CloseAllResult {
  status: string
  paused: boolean
  accounts: FlattenSummary[]
}

export interface Settings {
  copying_enabled: boolean
  dry_run: boolean
}

/** One TradingView alert as the server recorded it. `body` is never
 *  returned -- only what the operator needs to see what happened. */
export interface WebhookReceipt {
  id: number
  received_at: string
  outcome: 'accepted' | 'duplicate' | 'unknown' | 'rejected' | 'failed' | 'nothing_to_close' | 'nothing_to_cancel' | string
  reason: string | null
  action: string | null
  symbol: string | null
  lots: number | null
  source_ip: string | null
  latency_ms: number | null
}

export interface WebhookSettings {
  configured: boolean
  hook_id: string | null
  url: string | null
  url_hint: string | null
  has_secret: boolean
  secret_created_at: string | null
  enabled: boolean
  max_lots: number
  max_per_minute: number
  max_open_positions: number
  symbol_aliases: Record<string, string>
  vt_ltf_timeframes: string[]
  master_account_id: number | null
  dry_run: boolean
  copying_enabled: boolean
  template: string
  recent: WebhookReceipt[]
}

/** A per-symbol stop/target/trailing default the server fills in when an
 *  alert doesn't send its own. */
export interface RiskRule {
  symbol: string
  stop_points: number | null
  target_points: number | null
  trailing_enabled: boolean
  trail_start_points: number | null
  trail_step_points: number | null
}

/** Returned exactly once by POST .../webhook/secret. Hold it only while
 *  the reveal dialog is open. */
export interface WebhookSecret {
  secret: string
  hook_id: string
  url: string | null
  template: string
}

export interface EventResponse {
  id: number
  ts: string
  account_id?: number | null
  category: string
  severity: string
  latency_ms?: number | null
  payload: Record<string, unknown>
  /** Operator who requested the action; absent for autonomous copier work. */
  actor_email?: string | null
}

export type DriftKind =
  | 'orphan_slave_position'
  | 'missing_slave_copy'
  | 'unmapped_master_position'
  | 'unfilled_slave_order'
  // N8: a copy that was dispatched but never activated -- invisible to every
  // other drift category, because it has no slave_position_id and the master
  // position still has other slaves' active mapping rows.
  | 'stale_pending_copy'

export interface DriftItem {
  id: string
  kind: DriftKind
  account_id?: number | null
  position_id?: number | null
  order_id?: number | null
  detail: string
}

export interface PositionData {
  position_id: number
  symbol_id: number
  symbol?: string | null
  side: string
  volume: number
  entry_price: number
  /** This position's own protection; null means none is set on it. */
  stop_loss?: number | null
  take_profit?: number | null
  pnl_quote?: number | null
  current_price?: number | null
}

export interface AccountStateData {
  balance?: number | null
  open_pnl: number
  equity?: number | null
  positions: PositionData[]
}

export interface StateSnapshot {
  [account_id: string]: AccountStateData
}

export interface PositionCopy {
  slave_account_id: number
  slave_position_id?: number | null
  slave_order_id?: number | null
  slave_volume: number
  status: string
  error?: string | null
  fill_price?: number | null
  /** Protection actually on the slave position, not the master's intent. */
  stop_loss?: number | null
  take_profit?: number | null
  volume_lots?: string | null
}

export interface MasterPosition {
  position_id: number
  symbol_id: number
  symbol?: string | null
  side: string
  volume: number
  price: number
  label: string
  /** The master account this position lives on -- amend targets it. */
  account_id: number
  /** Decimals this symbol is quoted to; a price with more is refused. */
  digits?: number | null
  /** Round-trip commission on one unit, learned from this account's own
   *  closed trades. Null means never observed -- adjust nothing. */
  commission_per_unit?: number | null
  stop_loss?: number | null
  take_profit?: number | null
  pnl_quote?: number | null
  current_price?: number | null
  volume_lots?: string | null
  copies: PositionCopy[]
}

export interface PendingOrder {
  order_id: number
  symbol_id: number
  symbol?: string | null
  side?: string | null
  order_type?: string | null
  /** Trigger price the order is waiting for (limit or stop). */
  price?: number | null
  volume: number
  label: string
  volume_lots?: string | null
  copies: PositionCopy[]
}

export interface ApiState {
  accounts: Record<string, AccountStateData>
  master_positions: MasterPosition[]
  pending_orders: PendingOrder[]
  drift: DriftItem[]
}

export interface EquityCurvePoint {
  timestamp: number
  balance: number
}

export interface SymbolPerformance {
  symbol: string
  trades: number
  gross_pnl: number
}

export interface WeeklyPerformance {
  week_start: number
  trades: number
  gross_pnl: number
}

export interface Analytics {
  closed_trades: number
  wins: number
  losses: number
  win_rate: number | null
  profit_factor: number | null
  best_trade: number | null
  worst_trade: number | null
  avg_win: number | null
  avg_loss: number | null
  net_pnl: number
  gross_wins: number
  gross_losses: number
  max_drawdown: number
  max_drawdown_pct: number
  equity_curve: EquityCurvePoint[]
  per_symbol: SymbolPerformance[]
  weekly: WeeklyPerformance[]
  weeks: number
  truncated: boolean
}

export interface Trendbars {
  symbol: string
  period: string
  bars: {
    timestamp: number
    open: number
    high: number
    low: number
    close: number | null
    volume: number
  }[]
}

export interface MarginEstimate {
  symbol: string
  volume: number
  volume_lots?: string | null
  buy_margin: number
  sell_margin: number
}

export interface CashFlowEntry {
  id: number
  type: string
  amount: number
  balance_after: number
  timestamp: number
  note?: string | null
}

export interface RecentCopy {
  status: string
  master_position_id?: number | null
  master_order_id?: number | null
  slave_account_id: number
  slave_login: number
  slave_nickname?: string | null
  symbol?: string | null
  slave_volume?: number | null
  fill_price?: number | null
  error?: string | null
  updated_at: string
}

export interface OverviewStats {
  accounts_connected: number
  masters: number
  active_slaves: number
  disabled_or_paused: number
  degraded: number
  copied_today: number
  yesterday: {
    total_balance: number | null
    total_equity: number | null
    /** account_id -> equity at yesterday's snapshot. Lets a caller compare
     *  only the accounts that existed on both days. */
    equity_by_account?: Record<string, number> | null
  } | null
  recent_copies: RecentCopy[]
}

export interface InvestorPositions {
  equity_source: 'live' | 'last known' | 'unknown'
  positions: Array<{
    position_id: number; symbol: string | null; side: string; volume: number
    entry_price: number | null; current_price: number | null
    stop_loss: number | null; take_profit: number | null; pnl_quote: number | null
  }>
}

// ---------------------------------------------------------------------------
// Client portal, phase 1 (spec docs/superpowers/specs/2026-09-29-client-portal-
// phase-1-money-design.md, section 8). Every money figure is a float rounded
// to cents; every row and summary carries `currency` ("USD" in phase 1).
// ---------------------------------------------------------------------------

export type WalletKind = 'main' | 'credit' | 'pamm' | 'social'

export type RequestStatus =
  | 'pending' | 'confirmed' | 'rejected' | 'cancelled'
  | 'requested' | 'approved' | 'paid' | 'done' | 'removed'

/** One end of a transfer: a wallet or the linked trading account. An
 *  account end's `account_id` is null once that account has been removed
 *  (the transfer row outlives it). */
export interface MoneyRef { kind: 'wallet' | 'account'; wallet?: WalletKind; account_id?: number | null }

export interface PaymentMethod {
  id: number
  kind: 'crypto' | 'bank'
  label: string
  enabled: boolean
  currency: string
  details: Record<string, string>
  min_amount: number
  fee_pct: number
  instructions: string | null
  sort_order: number
}

export interface PortalDeposit {
  id: number
  user_id: number
  method_id: number | null
  method_kind: 'crypto' | 'bank'
  method_label: string
  amount: number
  fee: number
  credited_amount: number | null
  reference: string
  receipt_file_id: number | null
  target: 'wallet' | 'account'
  target_account_id: number | null
  note: string | null
  status: RequestStatus
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  currency: string
  /** Present in the admin queue only. */
  email?: string
  display_name?: string
}

export interface PortalWithdrawal {
  id: number
  user_id: number
  destination_id: number
  destination_kind: 'bank' | 'crypto'
  destination_summary: string
  amount: number
  fee: number
  net_amount: number
  status: RequestStatus
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  paid_by: number | null
  paid_at: string | null
  txid: string | null
  created_at: string
  currency: string
  email?: string
  display_name?: string
}

export interface PortalTransfer {
  id: number
  user_id: number
  source: MoneyRef
  target: MoneyRef
  amount: number
  status: RequestStatus
  equity_at_request: number | null
  equity_verified: boolean
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  done_by: number | null
  done_at: string | null
  note: string | null
  created_at: string
  currency: string
  email?: string
  display_name?: string
}

export interface PayoutDestination {
  id: number
  user_id: number
  kind: 'bank' | 'crypto'
  nickname: string
  details: Record<string, string>
  proof_file_id: number | null
  status: RequestStatus
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  /** Built server-side: "ICICI Bank ••4543" or "TRC20 T…9f". */
  summary: string
  email?: string
  display_name?: string
}

export interface WalletEntry {
  id: number
  wallet: WalletKind
  /** Signed: credits positive, debits negative. */
  amount: number
  kind: 'deposit' | 'withdrawal' | 'transfer' | 'adjustment' | 'bonus' | 'commission' | 'fee'
  ref_table: string | null
  ref_id: number | null
  note: string | null
  created_at: string
  currency: string
}

export interface WalletEntriesPage { entries: WalletEntry[]; has_more: boolean; next_before: number | null }

export interface WalletFigures { balance: number; on_hold: number; available: number }

export type EquitySource = 'live' | 'last known' | 'unknown'

/** One of the investor's live accounts on GET investor/summary, oldest first. */
export interface AccountSummary {
  account_id: number; nickname: string | null; platform: string
  status: string; last_error: string | null; connected: boolean
  /** From the fulfilled account request that linked it, else null. */
  mt5_login: number | null; mt5_server: string | null
  equity_source: EquitySource; equity: number | null; net_funded: number; profit: number | null
  /** Equity less open account -> wallet transfers, floored to cents; null while equity is unknown. */
  account_available: number | null
  open_positions: number
}

/** One account on a row of the admin's GET investors. */
export interface InvestorAccount { account_id: number; nickname: string | null; equity: number | null; equity_source: EquitySource }

/** GET investor/summary. `available` figures are floored to cents server-side. */
export interface InvestorSummary {
  org: { id: number; name: string }
  currency: string
  investor: { display_name: string; first_name: string; member_since: string }
  wallets: Record<WalletKind, WalletFigures>
  totals: { deposited: number; withdrawn: number; transferred_in: number; transferred_out: number }
  cash_flow: { date: string; deposits: number; withdrawals: number }[]
  pending: { deposits: number; withdrawals: number; transfers: number; payout_destinations: number }
  deposits_open: boolean
  withdrawal_rules: { min: number; fee_pct: number }
  /** The top-level equity, net_funded, profit, open_positions and equity_source are totals over these. */
  accounts: AccountSummary[]
  /** used = owned accounts + open account requests. */
  account_limit: { max: number; used: number }
  // Transitional (phase 3 Task 15 removes them): link_state, account, account_available.
  link_state: 'linked' | 'unlinked'
  account: {
    account_id: number; nickname: string | null; platform: string
    status: string; last_error: string | null; connected: boolean
  } | null
  equity_source: 'live' | 'last known' | 'unknown'
  equity: number | null
  net_funded: number
  profit: number | null
  account_available: number | null
  open_positions: number
  kyc_status: KycStatus
}

/** One row of the admin's Investors table (GET investors). */
export interface InvestorRow {
  user_id: number
  email: string
  display_name: string
  joined_at: string
  accounts: InvestorAccount[]
  // Transitional (phase 3 Task 15 removes them): account_id, nickname, equity, equity_source.
  account_id: number | null
  nickname: string | null
  equity: number | null
  equity_source: string
  balances: Record<WalletKind, number>
  on_hold: number
  available: number
  pending: { deposits: number; withdrawals: number; transfers: number; payout_destinations: number }
  kyc_status: KycStatus
}

export interface PortalSettings { withdrawal_min: number; withdrawal_fee_pct: number; max_live_accounts: number }

export interface RequestsSummary {
  deposits: number; withdrawals: number; transfers: number; payout_destinations: number
  kyc: number; account_requests: number; total: number
}

/** What POST investor/files answers. */
export interface UploadedFile { id: number; purpose: string; content_type: string; size_bytes: number; created_at: string }

// ------------------------------------------------------------ phase 2: identity

export type KycStatus = 'draft' | 'submitted' | 'approved' | 'rejected'
export type Gender = 'male' | 'female' | 'other'
export type IdType = 'passport' | 'national_id' | 'driving_licence'
export type KycTextField =
  | 'full_name' | 'gender' | 'date_of_birth' | 'phone' | 'address_line' | 'area' | 'landmark'
  | 'city' | 'state' | 'postal_code' | 'country_residence' | 'country_citizenship' | 'id_type'
  | 'id_number'
export type KycFileField = 'id_front_file_id' | 'id_back_file_id' | 'address_proof_file_id' | 'photo_file_id'

/** GET investor/profile, PUT investor/profile, and each row of the admin's GET kyc
 *  (which adds email and display_name). `missing` lists the required fields still empty. */
export type KycProfile = { user_id: number }
  & Record<KycTextField, string | null>
  & Record<KycFileField, number | null>
  & {
    status: KycStatus
    submitted_at: string | null
    decided_by: number | null
    decided_at: string | null
    decision_note: string | null
    updated_at: string | null
    missing: string[]
    email?: string
    display_name?: string
  }

export interface AccountPackage {
  id: number; name: string; min_deposit: number; currency: string; spread_label: string | null
  leverage_options: number[]; enabled: boolean; sort_order: number
}

export type AccountRequestStatus = 'requested' | 'fulfilled' | 'rejected' | 'cancelled'

/** Never carries the passwords; the admin reads them through POST .../reveal. */
export interface AccountRequest {
  id: number; user_id: number; package_id: number | null; package_name: string; leverage: number
  status: AccountRequestStatus; mt5_login: number | null; mt5_server: string | null
  account_id: number | null; decided_by: number | null; decided_at: string | null
  decision_note: string | null; created_at: string; email?: string; display_name?: string
}

export interface RevealedPasswords { main_password: string; investor_password: string }

export interface SignIn {
  id: number; ip: string; user_agent: string | null
  outcome: 'password_ok' | 'mpin_ok' | 'failed'; created_at: string
}
