import type {
  AccountPackage, AccountRequest, AccountSummary, Bonus, BonusRules, InvestorRow, InvestorSummary, KycProfile,
  PayoutDestination, PaymentMethod, PortalDeposit, PortalNotification, PortalTransfer, PortalWithdrawal, SignIn,
  Ticket, TicketMessage, TicketSubject, TicketThread, WalletEntry,
} from '../lib/types'

/**
 * Realistic portal rows for page tests. Every builder takes overrides so a
 * test states only what it cares about. Figures: main balance 5,120.50,
 * on hold 100.00, available 5,020.50; a 250.00 USDT deposit pending; an
 * approved ICICI bank payout account; a 250.00 withdrawal requested.
 */

const WHEN = '2026-09-29T10:05:06Z'

export function accountSummaryFixture(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return {
    account_id: 555, nickname: 'Growth', platform: 'mt5', status: 'ok', last_error: null, connected: true,
    mt5_login: 5001, mt5_server: 'Broker-Live', equity_source: 'live', equity: 1240.25, net_funded: 1000,
    profit: 240.25, account_available: 1240.25, open_positions: 2,
    ...overrides,
  }
}

export function summaryFixture(overrides: Partial<InvestorSummary> = {}): InvestorSummary {
  return {
    org: { id: 1, name: 'Acme' },
    currency: 'USD',
    investor: { display_name: 'Sherwyn Joel', first_name: 'Sherwyn', member_since: '2026-09-01T09:00:00Z' },
    wallets: {
      main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
      credit: { balance: 0, on_hold: 0, available: 0 },
      pamm: { balance: 250, on_hold: 0, available: 250 },
      social: { balance: 0, on_hold: 0, available: 0 },
    },
    totals: { deposited: 6000, withdrawn: 500, transferred_in: 0, transferred_out: 1000 },
    cash_flow: [
      { date: '2026-09-27', deposits: 1000, withdrawals: 0 },
      { date: '2026-09-28', deposits: 0, withdrawals: 500 },
      { date: '2026-09-29', deposits: 250, withdrawals: 0 },
    ],
    pending: { deposits: 1, withdrawals: 0, transfers: 0, payout_destinations: 0 },
    deposits_open: true,
    withdrawal_rules: { min: 50, fee_pct: 1 },
    accounts: [accountSummaryFixture()],
    account_limit: { max: 5, used: 1 },
    equity_source: 'live',
    equity: 1240.25,
    net_funded: 1000,
    profit: 240.25,
    open_positions: 2,
    kyc_status: 'approved',
    ...overrides,
  }
}

export function depositFixture(overrides: Partial<PortalDeposit> = {}): PortalDeposit {
  return {
    id: 12, user_id: 1, method_id: 3, method_kind: 'crypto', method_label: 'USDT on TRC20',
    amount: 250, fee: 2.5, credited_amount: null, reference: 'abc123txhash', receipt_file_id: null,
    target: 'wallet', target_account_id: null, note: null, status: 'pending',
    decided_by: null, decided_at: null, decision_note: null, created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function withdrawalFixture(overrides: Partial<PortalWithdrawal> = {}): PortalWithdrawal {
  return {
    id: 4, user_id: 1, destination_id: 2, destination_kind: 'bank', destination_summary: 'ICICI Bank ••4543',
    amount: 250, fee: 2.5, net_amount: 247.5, status: 'requested',
    decided_by: null, decided_at: null, decision_note: null, paid_by: null, paid_at: null, txid: null,
    created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function transferFixture(overrides: Partial<PortalTransfer> = {}): PortalTransfer {
  return {
    id: 9, user_id: 1,
    source: { kind: 'wallet', wallet: 'main' },
    target: { kind: 'account', account_id: 555 },
    amount: 500, status: 'requested', equity_at_request: null, equity_verified: false,
    decided_by: null, decided_at: null, decision_note: null, done_by: null, done_at: null, note: null,
    created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function destinationFixture(overrides: Partial<PayoutDestination> = {}): PayoutDestination {
  return {
    id: 2, user_id: 1, kind: 'bank', nickname: 'Salary account',
    details: {
      bank_name: 'ICICI Bank', holder: 'Sherwyn Joel', account_number: '000401234543', code: 'ICIC0000004',
      bank_address: 'Mumbai', country: 'IN',
    },
    proof_file_id: null, status: 'approved',
    decided_by: 7, decided_at: '2026-09-20T12:00:00Z', decision_note: null, created_at: '2026-09-19T12:00:00Z',
    summary: 'ICICI Bank ••4543',
    ...overrides,
  }
}

export function entryFixture(overrides: Partial<WalletEntry> = {}): WalletEntry {
  return {
    id: 31, wallet: 'main', amount: 250, kind: 'deposit', ref_table: 'deposits', ref_id: 12, note: null,
    created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function methodFixture(overrides: Partial<PaymentMethod> = {}): PaymentMethod {
  return {
    id: 3, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
    details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
    min_amount: 50, fee_pct: 1, instructions: null, sort_order: 0,
    ...overrides,
  }
}

export function investorRowFixture(overrides: Partial<InvestorRow> = {}): InvestorRow {
  return {
    user_id: 1, email: 'investor@example.com', display_name: 'Sherwyn Joel', joined_at: '2026-09-01T09:00:00Z',
    accounts: [{ account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live' }],
    balances: { main: 5120.5, credit: 0, pamm: 250, social: 0 }, on_hold: 100, available: 5020.5,
    pending: { deposits: 1, withdrawals: 0, transfers: 0, payout_destinations: 0 },
    kyc_status: 'approved',
    ...overrides,
  }
}

export function profileFixture(overrides: Partial<KycProfile> = {}): KycProfile {
  return {
    user_id: 1, full_name: 'Sherwyn Joel', gender: 'male', date_of_birth: '1990-04-02',
    phone: '+91 98765 43210', address_line: '12 Lake Road', area: null, landmark: null,
    city: 'Coimbatore', state: 'Tamil Nadu', postal_code: '641001', country_residence: 'IN',
    country_citizenship: 'IN', id_type: 'passport', id_number: 'P1234567',
    id_front_file_id: 31, id_back_file_id: 32, address_proof_file_id: 33, photo_file_id: 34,
    status: 'draft', submitted_at: null, decided_by: null, decided_at: null, decision_note: null,
    updated_at: WHEN, missing: [],
    ...overrides,
  }
}

export function packageFixture(overrides: Partial<AccountPackage> = {}): AccountPackage {
  return {
    id: 1, name: 'Standard', min_deposit: 100, currency: 'USD', spread_label: '20-25',
    leverage_options: [100, 200, 500], enabled: true, sort_order: 0,
    ...overrides,
  }
}

export function accountRequestFixture(overrides: Partial<AccountRequest> = {}): AccountRequest {
  return {
    id: 7, user_id: 1, package_id: 1, package_name: 'Standard', leverage: 200, status: 'requested',
    mt5_login: null, mt5_server: null, account_id: null, decided_by: null, decided_at: null,
    decision_note: null, created_at: WHEN,
    ...overrides,
  }
}

export function signInFixture(overrides: Partial<SignIn> = {}): SignIn {
  return {
    id: 1, ip: '203.0.113.7',
    user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36',
    outcome: 'mpin_ok', created_at: WHEN,
    ...overrides,
  }
}

// ------------------------------------------------------------ phase 4: engagement

export function notificationFixture(overrides: Partial<PortalNotification> = {}): PortalNotification {
  return {
    id: 31, topic: 'money', title: 'Your deposit of 250.00 USD was confirmed',
    body: 'Status: confirmed\nAmount: 250.00 USD via USDT on TRC20', link: '/org/1/invest/deposit',
    read_at: null, created_at: WHEN,
    ...overrides,
  }
}

export function ticketFixture(overrides: Partial<Ticket> = {}): Ticket {
  return {
    id: 7, user_id: 1, subject_id: 2, subject_label: 'Deposits', status: 'new',
    created_at: WHEN, updated_at: WHEN, last_message_at: WHEN, closed_at: null, closed_by: null,
    last_from_desk: false, waiting_on_desk: true,
    ...overrides,
  }
}

export function ticketMessageFixture(overrides: Partial<TicketMessage> = {}): TicketMessage {
  return {
    id: 70, author_id: 1, author_name: 'Sherwyn Joel', from_desk: false,
    body: 'My deposit has not arrived.', file_ids: [], created_at: WHEN,
    ...overrides,
  }
}

export function threadFixture(overrides: Partial<TicketThread> = {}): TicketThread {
  return {
    ...ticketFixture(), email: 'inv@example.com', display_name: 'Sherwyn Joel',
    messages: [ticketMessageFixture()],
    ...overrides,
  }
}

export function subjectFixture(overrides: Partial<TicketSubject> = {}): TicketSubject {
  return { id: 2, label: 'Deposits', enabled: true, sort: 0, created_at: WHEN, ...overrides }
}

export function bonusFixture(overrides: Partial<Bonus> = {}): Bonus {
  return {
    id: 4, source: 'deposit', source_id: 12, amount: 25, note: 'deposit #12', created_at: WHEN,
    currency: 'USD',
    ...overrides,
  }
}

export function bonusRulesFixture(overrides: Partial<BonusRules> = {}): BonusRules {
  return {
    signup_enabled: false, signup_amount: 0, kyc_enabled: false, kyc_amount: 0,
    deposit_enabled: true, deposit_pct: 10, deposit_cap: 100, updated_at: WHEN,
    ...overrides,
  }
}
