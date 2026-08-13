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

export type DriftKind = 'orphan_slave_position' | 'missing_slave_copy' | 'unmapped_master_position' | 'unfilled_slave_order'

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
  symbol?: string
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
  volume_lots?: string
}

export interface MasterPosition {
  position_id: number
  symbol_id: number
  symbol?: string
  side: string
  volume: number
  price: number
  label: string
  pnl_quote?: number | null
  volume_lots?: string
  copies: PositionCopy[]
}

export interface PendingOrder {
  order_id: number
  symbol_id: number
  symbol?: string
  volume: number
  label: string
  volume_lots?: string
  copies: PositionCopy[]
}

export interface ApiState {
  accounts: Record<string, AccountStateData>
  master_positions: MasterPosition[]
  pending_orders: PendingOrder[]
  drift: DriftItem[]
}
