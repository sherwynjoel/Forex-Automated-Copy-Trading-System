# AI Prime Groups client portal — survey (2026-09-29)

Source: https://trade.aiprimegroups.com/user/… (user's own account, signed in by the user).
Purpose: inventory of every screen, flow and data field so the same capabilities can be
planned for MirrorFleet. Recorded page by page while browsing; states seen were mostly empty
(new account, $0 balances, KYC 40%).

## Shell

- Left sidebar (collapsible groups): Dashboard · Trading ▸ Live Accounts, Demo Accounts ·
  PAMM · Social Trading ▸ Create New Account, My Accounts, Transfer, Commissions,
  Subscriptions, My Managers · Wallet ▸ My Wallet, Deposit, Withdraw, Transfer,
  Transactions, Bonus.
- Sidebar footer card: "40% · Verification · Complete KYC · Action required" → /user/kyc.
- Top bar: Downloads (→ /user/download-links) · Theme toggle (light/dark) · Wallet
  balance dropdown ("$0.00 ▾": Hide Balance; Credit Wallet / PAMM Wallet / My Wallet /
  Social Trading Wallet, each with a balance and a Transfer shortcut) · Notifications bell ·
  Profile menu (Profile, Password, Security, KYC, Withdrawal Accounts, IB Request, Support,
  Notifications, Activity Logs, Settings, Sign out).
- Toast on login: "Welcome back".

## /user/dashboard

1. PORTFOLIO — "Trading & Activity" card: tabs Live / Demo / PAMM / Social, platform chip
   (MT5), empty tray with "Create account" button, "View all →" to /user/trading/live.
2. Welcome panel: greeting by time of day ("Good afternoon · Welcome back, <first name>"),
   "Wallet balance · Live" with eye toggle (hide amounts), big balance, chip "$0.00 lifetime
   P&L", "Secure session · 3 minutes ago", buttons Deposit / Withdraw / Transfer.
   Right: Verification ring (40%, "Complete KYC · Action required" → /user/kyc) and
   "7-day net flow" sparkline (Mon…Sun, +$0.00).
3. Four wallet tiles with mini sparklines: My Wallet (Available · USD), Total Deposit,
   Total Withdraw (Lifetime totals), Total Transfer (Lifetime totals).
4. PAMM — "PAMM portfolio · Your follower performance at a glance" [Open PAMM →]:
   Total deposit, Total PnL, Success fees, Penalty fees, Total withdraw, Current equity;
   "Performance snapshot" with range All / Today / 7D / 30D: Win trades (n/n, %),
   Loss trades, PnL ($0.0000), Average PnL.
5. SOCIAL TRADING — "Social portfolio" [Open Social →]: Current equity, Total P&L,
   Accounts (count), Performance fees.
6. TREASURY — "Balance breakdown · LIVE": donut (Total) + rows My Wallet, Credit Wallet,
   PAMM Wallet, Social Trading Wallet with amount and % share.
7. Four account-type cards with arrows: Live accounts (badge LIVE), Demo accounts
   (PRACTICE), PAMM accounts (MANAGED), Social accounts (COPY): "0 · 0 accounts", $0.00.
8. "Trading platforms" [View all →]: MT5 card with "Open →".
9. CASH FLOW — "Deposits & Withdrawals": legend Deposits / Withdraws, range 7D / 30D / 90D,
   totals strip (Total deposits, Total withdrawals, Net flow), line chart by date.

## /user/download-links

"Download Links · Get our apps and downloadable resources": one card, MT5, "download mt5
apk file", Download button.

## /user/trading/live and /user/trading/demo (redirect to /user/trading/tx7a/{live|demo})

Header "Your Trading Accounts · Manage Live and Demo accounts across MT5" with counters
Total accounts, MT5. Tabs Live / Demo, refresh icon, "Open Live Account" / "Open Demo
Account" button. Platform card MT5 (count). Stat tiles: Live account no (count), Live
account balance. Empty state: "No Live Accounts Yet · You haven't created any live trading
accounts on MT5 yet."

## /user/invest/pamm — "My PAMM Accounts"

"Track your PAMM investor accounts, performance and transactions." Button "Salary".
Sub-tabs: My PAMM Accounts · Create New Account · Transfer · Commissions · My Managers.
Six tiles: Total deposit, Total PnL, Success fees, Penalty fees, Total withdraw, Current
equity. Performance snapshot (All / Today / 7 Days / 30 Days): Win trades, Loss trades, PnL,
Average PnL. "Investor Accounts (0)" list; empty state "No investor account yet · Browse top
managers and start following a strategy" [Browse Managers].

## /user/invest/social/browse — "Browse Social Trading Managers"

"Discover managers you can follow across supported platforms." Header shortcuts: "My
Accounts · View your invested account details →" and "Wallet $0.00". Stat strip: Total
managers (2), In profit (1/2), Avg. min deposit ($3,000), Top P&L ($1,239.80). Filter
"Platform: MT5", count "2 managers". Manager cards: login (900102), name (MANAGER BOT 2),
P&L chip (−$906.68 red / +$1,239.80 green, "TOP PERFORMER" ribbon), equity sparkline,
Min. deposit, Success fees (50%), Balance, Trades (48/65 = wins/total), Win rate bar (74%),
buttons "View details" and "Follow".

## /user/invest/social/manager/tx7a/{login} — "Social Trading Manager Statistics"

Back link. Header: avatar, platform (MT5), login (900102), name, "Manager", masked email
(abu*****@gmail.com). "Performance · Trade P&L trend" line chart with total (+$-1,029.30,
10 trades). Button "Follow Manager". Tiles: Success fees (%), Penalty fees (%), Trading
period (days), Total profit, Minimum deposit. Balance ($860.50, Leverage 400), Credit
($0.00, "Available bonus credit"). "General information": Equity, Free margin, Margin
leverage, Total commission, Total deposit, Total withdraw, Total trades (65), Best trade
($ + timestamp), Worst trade, Best pip, Worst pip, Total pips, Avg profit trade, Avg loss
trade, Buy (win/total) 35/35, Sell (win/total) 13/30, Minimum lots. "Trading Activity"
table [Refresh]: Side, Symbol, Price, Volume, Position (close price), Profit/Loss, Date.

## Follow manager dialog (from Browse or the manager page)

Read-only rows: Account (MT5-900102), Manager, Min. deposit ($1,000), Wallet balance.
Fields: Investment amount* (prefilled with the minimum), Auto renew* (Yes/No), Trade
type* (select), Main password* with "Generate" (an MT5 investor account is created for the
follower), further fields below the fold. Buttons Cancel / "Deposit & Follow".

## /user/invest/social — "My Social Accounts"

"Track your Social Trading follower accounts, performance and transactions." Buttons
Salary, My Managers, Browse Managers. Tiles: Total deposit, Total PnL, Success fees, Total
withdraw, Current equity. "Investor Accounts (0)" with empty state [Browse Managers].

## /user/invest/social/transfer — "Social Transfer"

"Move Social Trading commissions to your main wallet." Social wallet balance ($0.0000).
Form: Amount ($)* → "Process Transfer". "Transfer History · Past transfer requests and
their outcomes" (empty: "No transfers yet").

## /user/invest/social/commissions — "Social Commissions"

"Commission payouts credited to your Social Trading wallet." Total commission. "Payout
History · All commission payouts with source and amount."

## /user/invest/social/subscriptions — "Subscribers"

"Everyone currently following your Social Trading manager account." "Subscription Log ·
Each investor who subscribed to your manager, with the fee charged."

## /user/invest/social/managers — "My Social Trading Manager Accounts"

"Manage your Social Trading manager accounts, subscriptions and follower requests."
Buttons "Social Account", "Create Manager Account". Tiles: Social trade wallet, Total
investor equity, Total investors, Total manager accounts. "Account Management · Switch
between your manager accounts and pending requests" tabs Accounts / Account Request;
platform chip MT5; empty "No manager accounts yet · Create your first manager account to
start earning subscriptions."

## /user/invest/social/managers/new — "Create Social Trading Manager Account"

Sections: Manager Profile (Name* "My Growth Fund", Subscription fee*, Follower minimum
investment*, Subscription duration (days)* "e.g. 30", Performance fees (%)*, Listing*
select), Platform & Account (Trading platform* MT5, Account* = one of the user's existing
MT5 accounts), Trading & Distribution (Profit distribution* select, Trade type* select).
Button "Submit Request" (goes to admin approval → "Account Request" tab).

## /user/wallet — "My Wallet · Wallet overview"

Total balance, Pay-in, Available; buttons Deposit / Withdraw / Transfer. "Net flow · 30D"
chart with 7D/30D/90D and legend Net flow / Deposits ("No flow data yet"). Totals: Total
deposits, Total withdraws, Total transfers, Net flow. "Quick · Wallet actions" cards:
Deposit funds (UPI, card, or bank transfer), Withdraw funds (back to your bank account),
Transfer (between wallets & trading accounts), Transactions (full payment history &
receipts). "Activity · Recent transactions" [View all]: Date, Type, Channel, Amount.

## /user/deposit → /user/deposit/{crypto-gateway|bank} — "Deposit Funds"

"Fund your wallet via bank, UPI, crypto, card or instant gateways." Wallet balance chip.
Methods: Crypto Gateway, Bank.
- Crypto gateway: gateway list (Cryptrum, currency USDT), "Pick a coin" cards per network
  (BEP20 / POLYGON / TRC20 · USDT · "Deposit Now"), history table: Transaction hash,
  Amount, Coin, Network, Status, Remarks, Date.
- Bank: bank cards (name, Account/IBAN, Holder, SWIFT/IFSC, Currency, Conversion rate,
  Min. deposit $500, Fees 0.00%), right pane "Choose an account" then a form (amount +
  receipt upload), lock note "Your transaction is encrypted end-to-end", history: Bank,
  Transaction ID, Account, Amount, Receipt, Status, Remarks, Date.

## /user/withdraw → /user/withdraw/{bank|crypto} — "Withdraw Funds"

"Send funds from your wallet to a saved payout destination." Channel toggle Bank / Crypto.
Saved destinations list ("Your Saved Banks" / "Your Saved Wallets", "Add New"), empty state
"No Bank Accounts Saved Yet · Add your first payout destination". History: Code, Tx hash,
Account, Amount, Net, Remarks, Status, Date, Approved at.

## /user/transfer — "Transfer · Move funds between your wallets and accounts"

From / To pickers (wallet or trading account; "≈ $0.00 USD"), amount inputs both sides,
side panel "Select source wallet · Your wallets & accounts · 1 active" with search, rows
like "My Wallet $0.0000 PRIMARY". "Recent transfers" tabs All / Pending / Approved /
Failed; table Wallet movement, Requested amount, Received amount, Comment, Status, Date.

## /user/transactions — "Transactions · Wallet history, deposits and withdrawals"

Wallet tabs: All, Deposit, Withdraw, PAMM Wallet, Social Trading Wallet, My Wallet, Credit
Wallet. "Wallet breakdown · Per-wallet running totals across credits and debits". Toolbar
Filter, Download Excel. Table: Amount, Wallet, Txn ID, Type, Source, Description, Date.

## /user/bonus — "Bonus · Manage your credit balance, account transfers and bonus payouts"

Credit wallet balance. Tabs Bonus History / Transfer History. Filters: From user, Source
(All / KYC-EVENT / DEPOSIT-EVENT / TRADE-EVENT / SIGNUP-EVENT), Status (All / Paid /
Unpaid), From date, To date, Apply, Clear. Table: From user, Wallet, Amount, Description,
Source, Status, Date.

## /user/kyc — "KYC Verification · Verify your identity to unlock deposits, trading and withdrawals"

Status chip (Not Started). Stepper: 01 Profile, 02 Identity, 03 Address, 04 Photo (each
"Not Submitted"). Step 1 form: Full name, Email, Gender*, Date of birth*, Phone, Pincode,
Flat/house no./building, Area/street/village, Landmark, Country of residence* (India),
City, State, Country of citizenship*. "Save changes". "Submission history · All KYC
documents you have submitted with their current status."

## Profile menu pages

- /user/profile — avatar upload, name, email (Verified chip), phone, "Complete your
  profile"; Member since, Account ID (6921153), KYC status. Sections Identity (Full name*,
  Email*, Gender*, DOB*, Pincode*, Phone*), Where you are (Flat*, Area, Landmark, City*,
  State*, Country of residence*), Compliance (Country of citizenship*). Footer "All
  saved · Last edited just now", Discard / Save changes.
- /user/change-password — Step 1 Current password, New password (+Generate), Confirm;
  Step 2 "Verify with 4-digit PIN"; "Update password". Side: live strength checklist
  (8+ chars, upper, lower, number, special), security tips.
- /user/authentication — Two-factor authentication: status (2FA Disabled), Active method
  (4-digit PIN), Account email, Authenticator (Not set), PIN (Configured). Step 1 choose
  method: Authenticator app / Email OTP / 4-digit PIN / No method; Step 2 verify current
  PIN; Step 3 create PIN (new + confirm). "Apply changes".
- /user/withdraw-accounts — "Withdrawal Accounts · Add a payout account and review the ones
  you have saved" (0 saved): Bank section (Add New Bank), Crypto section (Add New Crypto).
- /user/ib — "IB Dashboard · Partner program": Introducing Broker programme pitch, 5 steps
  (apply, approval, referral link, refer, earn), "Request For IB" button.
- /user/chat — "Support Chat · Raise a ticket and chat with our support team": ticket list
  with search and status tabs All / New / Open / Reopen / Closed, "Raise Ticket", thread
  pane.
- /user/notifications — "Updates · Notifications", "Mark all read", list (empty). Bell
  popover shows the same ("All caught up").
- /user/logs — "Activity Logs · Sign-in history": Total sign-ins, table #, IP address,
  Country, Time.
- /user/settings — "Settings & appearance": Appearance (Light / Dark), Keyboard shortcuts
  toggle with a full map (Alt+1…0 navigation, [ ] paging, J/K rows, Ctrl+, settings, ?
  help, Alt+Q sign out, Esc close), "My Shortcuts" (user-defined page shortcuts).
- Sign out.

## Account opening wizards

- /user/trading/tx7a/live/new — "Open MT5 Live Account": stepper Package → Details → Done.
  Step 1 package cards: Standard (min deposit $100, USD, spread 30-35), Pro ($500, 25-30),
  ECN ($1,000, 20-25), each with "Select". Step 2 "Configure your new account": selected
  package chip, Leverage (select), Credentials "Save these securely — they cannot be
  recovered later": Main password and Investor password (min 8 chars, "Generate password",
  "Copy password"), "Create account". Step 3 Done (credentials shown once).
- /user/trading/tx7a/demo/new — same wizard; this broker has no demo packages ("No packages
  available · Please contact support").

## Withdrawal account forms (/user/withdraw-accounts, also "Add New" on Withdraw)

- Add Bank account: Nick name ("e.g. My salary account"), Payment method (select), Account
  number / IBAN, Bank name, Bank address, Account holder name, SWIFT / IFSC code, Country
  (select), Image (proof upload). Cancel / Save account. Saved accounts need admin
  approval before they can be used ("Pick an approved bank account").
- Add Crypto account: Nick name, Payment method (network select), Wallet address, Image.

## Deposit flows

- Crypto "Deposit Now" → dialog "Where should this deposit go? · Pick the wallet or trading
  account to credit. The deposit address is issued for this target." Deposit to: My Wallet
  / MT5 (a trading account); note "Funds credit directly to My Wallet (balance: $0.00)";
  Cancel / Continue → gateway issues a network address (+ QR) and the history table tracks
  the hash.
- Bank → select a bank card → "Submit deposit" pane: Bank, Holder, A/C, Code; Amount (USD)
  with quick chips $50 / $100 / $250 / $500 / MIN; Deposit to (My Wallet / MT5);
  Transaction ID; Receipt image (file); Comment (optional); note "Funds usually credit in
  3 to 4 business days. Reference number helps us reconcile faster."; "Continue to
  deposit".

## PAMM sub-pages

- /user/invest/pamm/browse — "Browse PAMM Managers" (same layout as social browse;
  platform filter, count, wallet chip; empty "No managers available").
- /user/invest/pamm/managers — "My PAMM Manager Accounts": PAMM wallet balance, Total PAMM
  accounts, Accounts / Account Request tabs, "Create Manager Account".
- /user/invest/pamm/managers/new — "Create PAMM Manager Account": PAMM name*, Minimum
  deposit*, Success fees (%)*, Listing* (select), Profit distribution* (select),
  Credentials: Main password*, Investor password*. Cancel / Submit Request.
- /user/invest/pamm/transfer — "PAMM Transfer · Move PAMM commissions to your main wallet"
  (PAMM wallet balance, Amount → Process Transfer, Transfer history).
- /user/invest/pamm/commissions — Total commission, Success commission, Penalty commission,
  Payout history.
- /user/salary — "Salary · Salary rules, qualification progress, and payout history":
  chips "5 active rules · 0 paid"; "Next salary progress" (target salary, days left,
  overall %, Self balance required, Team balance, Team strong leg, Team remaining leg each
  as $x of $y with %); tabs Salary rules / Salary history; rules table: Name, Social
  balance required, Social change team balance required, Social self balance required,
  Salary amount (5 tiers $100 → $2,000). This is a referral/team (IB) reward scheme.

## Support, IB and transactions dialogs

- Raise New Ticket: Subject (select, populated by admin), Message (textarea "Describe your
  issue in detail…"), Attachments (images), Cancel / Submit Ticket. Ticket statuses New /
  Open / Reopen / Closed; thread view with chat.
- Request For IB: "Answer the questions": years in the Forex industry, worked as an IB or
  affiliate before, existing client base. Cancel / Submit (admin approves; then a referral
  link and commissions).
- Transactions Filter: From date, To date, Type (All Type / …), Clear / Close / Apply
  Filter. "Download Excel" exports the list.
- Transfer picker: side panel lists wallets and trading accounts ("My Wallet $0.0000
  PRIMARY", MT5 accounts when present); From → To with amount and USD estimate; requests
  land in "Recent transfers" with Pending / Approved / Failed.

## Cross-cutting behaviour noticed

- Four wallets per user: My Wallet (cash), Credit Wallet (bonus credit), PAMM Wallet
  (manager commissions), Social Trading Wallet (manager commissions); trading accounts
  are separate balances on MT5.
- Money movements are requests with admin approval: deposits (bank receipt review, crypto
  gateway auto-confirm), withdrawals (to approved destinations), transfers (Pending /
  Approved / Failed), manager-account creation (Account Request tab).
- Sensitive actions (password change, 2FA change, withdrawals) are gated by the 4-digit
  PIN, email OTP or an authenticator app.
- Amounts show four decimals in wallets and two in headline balances; hide-balance toggle
  masks every amount.
- Light / dark theme, keyboard shortcuts with a help overlay (Ctrl+/ or ?), custom
  shortcuts, welcome toast, greeting by time of day, "Secure session · n minutes ago".
