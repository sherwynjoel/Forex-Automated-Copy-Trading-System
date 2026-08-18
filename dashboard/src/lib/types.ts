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
  shards: number
}

export interface EventResponse {
  id: number
  ts: string
  account_id?: number | null
  category: string
  severity: string
  latency_ms?: number | null
  payload: Record<string, unknown>
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
  pnl_quote?: number | null
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
  pnl_quote?: number | null
  volume_lots?: string | null
  copies: PositionCopy[]
}

export interface PendingOrder {
  order_id: number
  symbol_id: number
  symbol?: string | null
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
