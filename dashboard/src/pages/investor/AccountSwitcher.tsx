import { accountName } from '../../lib/investor'
import Select from '../../components/Select'
import type { AccountSummary } from '../../lib/types'

/**
 * "Trading account" picker for the investor pages that show one account at
 * a time (Account, History). Hidden with fewer than two accounts; the page
 * keeps the pick in `?account=` so a refresh keeps it.
 */
export default function AccountSwitcher({ accounts, value, onChange }: {
  accounts: AccountSummary[]
  value: number | null
  onChange: (accountId: number) => void
}) {
  if (accounts.length < 2) return null
  return (
    <label className="block w-64">
      <span className="desk-label block mb-1">Trading account</span>
      <Select aria-label="Trading account" block value={value ?? ''}
              onChange={(e) => onChange(Number(e.target.value))}>
        {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{accountName(a)}</option>)}
      </Select>
    </label>
  )
}
