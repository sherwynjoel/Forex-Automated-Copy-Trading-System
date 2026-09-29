/**
 * The 2026-09-23 investor-portal shapes. Kept ONLY so the pages that
 * Tasks 13-18 rewrite or delete (InvestorOverview, InvestorDeposit,
 * InvestorWithdraw, pages/Investors) keep compiling until their turn.
 * Nothing new may import this file; Task 18 deletes it together with its
 * last importer. The live shapes are in ./types.ts.
 */

export interface InvestorSummary {
  org: { id: number; name: string }
  link_state: 'linked' | 'unlinked'
  account: {
    account_id: number; nickname: string | null; platform: 'ctrader' | 'mt5' | string
    status: string; last_error: string | null; connected: boolean
  } | null
  equity_source: 'live' | 'last known' | 'unknown'
  wallet_configured: boolean
  total_deposited: number
  total_withdrawn: number
  net_deposits: number
  pending_withdrawn: number
  equity: number | null
  profit: number | null
  available: number | null
}

export interface InvestorWallet {
  coin: string
  network: string
  address: string
  memo: string | null
}

export interface InvestorDeposit {
  id: number
  user_id: number
  account_id: number | null
  amount: number
  coin: string
  txid: string
  note: string | null
  status: 'pending' | 'confirmed' | 'rejected' | string
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  /** Present in the admin queue only. */
  email?: string
  display_name?: string
}

export interface InvestorWithdrawal {
  id: number
  user_id: number
  account_id: number
  amount: number
  destination: string
  status: 'requested' | 'approved' | 'paid' | 'rejected' | string
  equity_at_request: number | null
  equity_verified: boolean
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  paid_by: number | null
  paid_at: string | null
  txid: string | null
  created_at: string
  email?: string
  display_name?: string
}

/** One row of the admin's Investors table. */
export interface InvestorRow {
  user_id: number
  email: string
  display_name: string
  account_id: number | null
  nickname: string | null
  equity: number | null
  net_deposits: number
  profit: number | null
  pending_deposits: number
  pending_withdrawals: number
}
