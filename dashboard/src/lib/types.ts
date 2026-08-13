export interface Account {
  id: string
  broker: string
  login: string
  password: string
  status: 'active' | 'paused' | 'error'
  balance: number
  equity: number
  created_at: string
  updated_at: string
}

export interface Settings {
  dry_run: boolean
  max_lot_size: number
  slippage_pct: number
}

export interface EventRow {
  id: string
  timestamp: string
  account_id: string | null
  severity: 'info' | 'warning' | 'error'
  category: string
  message: string
}

export interface DriftItem {
  slave_account_id: string
  slave_position_id?: string
  master_position_id: string
  status: 'orphan' | 'pending' | 'adopted' | 'dismissed'
  details?: Record<string, unknown>
}

export interface SlaveCopy {
  slave_account_id: string
  status: 'open' | 'closed' | 'error'
  slave_position_id?: string
  slave_volume: number
  fill_price: number
  error?: string
}

export interface MasterPosition {
  position_id: string
  symbol: string
  side: 'long' | 'short'
  volume: number
  entry_price: number
  pnl_quote: number
  copies: SlaveCopy[]
}

export interface PendingOrder {
  order_id: string
  symbol: string
  side: 'buy' | 'sell'
  volume: number
  price: number
  account_id: string
  created_at: string
}

export interface AccountState {
  balance: number
  equity: number
  positions: number
}

export interface StateSnapshot {
  accounts: Record<string, AccountState>
  master_positions: MasterPosition[]
  pending_orders: PendingOrder[]
  drift: DriftItem[]
}
