import type { Role } from './roles'

export interface OrgSummary {
  id: number
  name: string
  role: Role
}

export interface Me {
  user: { id: number; email: string; display_name: string }
  orgs: OrgSummary[]
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
}

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
}

export interface FlattenSummary {
  account_id: number
  positions_closed: number
  orders_cancelled: number
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
