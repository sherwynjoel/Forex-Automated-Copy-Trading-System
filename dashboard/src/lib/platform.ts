import type { Account } from './types'

/** Absent `platform` means an api that predates MT5: the account is cTrader. */
export function isMt5(account: Pick<Account, 'platform'>): boolean {
  return account.platform === 'mt5'
}

/** The figure that identifies an account in a label. cTrader: the trader
 *  login. MT5: the terminal's login once it has reported one -- before the
 *  first hello there is none, and the row's trader_login of 0 must never be
 *  printed as if it were one. */
export function accountIdent(account: Account): string {
  if (!isMt5(account)) return String(account.trader_login)
  const login = account.mt5?.login
  return login ? `MT5 · login ${login}` : 'MT5 · waiting for the terminal'
}

/** The nickname, else the identifying figure: "Account 12345" for cTrader,
 *  "MT5 · login 555" for MT5. */
export function accountName(account: Account): string {
  if (account.nickname) return account.nickname
  return isMt5(account) ? accountIdent(account) : `Account ${account.trader_login}`
}

/** "<nickname> · <ident>", or just the name when there is no nickname --
 *  never the login twice ("Account 12345 · 12345"). */
export function accountWho(account: Account): string {
  return account.nickname ? `${account.nickname} · ${accountIdent(account)}` : accountName(account)
}

/** The platforms present in a fleet, cTrader first: "cTrader", "MT5" or
 *  "cTrader · MT5". An empty fleet names both, since either can be added. */
export function platformCaption(accounts: Pick<Account, 'platform'>[]): string {
  if (accounts.length === 0) return 'cTrader · MT5'
  const parts: string[] = []
  if (accounts.some((a) => !isMt5(a))) parts.push('cTrader')
  if (accounts.some((a) => isMt5(a))) parts.push('MT5')
  return parts.join(' · ')
}
