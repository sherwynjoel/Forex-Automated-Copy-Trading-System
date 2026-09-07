import type { Account } from '../lib/types'

/** An MT5 account whose terminal has already said hello -- the shape
 *  GET /api/orgs/{org}/accounts returns once the EA is connected. Spread
 *  over it for the offline / never-reported variants. */
export const mt5Account: Account = {
  ctid_trader_account_id: 1000000000001,
  trader_login: 555,
  is_live: true,
  role: 'slave',
  enabled: false,
  multiplier: 1.0,
  status: 'ok',
  last_error: null,
  connection_status: 'connected',
  nickname: 'VPS desk',
  cutoff_date: null,
  platform: 'mt5',
  mt5: {
    login: 555,
    broker: 'XYZ Ltd',
    server: 'XYZ-Live3',
    currency: 'USD',
    hedging: true,
    trade_mode: 'real',
    ea_version: '1.0.0',
    last_seen_at: '2026-09-07T10:00:00+00:00',
    connected: true,
  },
}
