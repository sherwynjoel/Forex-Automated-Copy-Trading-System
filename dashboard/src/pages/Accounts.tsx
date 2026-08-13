import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Account } from '../lib/types'

export default function Accounts() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [roleErrors, setRoleErrors] = useState<Record<number, string>>({})
  const [multiplierErrors, setMultiplierErrors] = useState<Record<number, string>>({})
  const [pendingRows, setPendingRows] = useState<Set<number>>(new Set())
  const [originalMultipliers, setOriginalMultipliers] = useState<Record<number, number>>({})

  const fetchAccounts = async () => {
    try {
      setIsLoading(true)
      const data = await api<Account[]>('/api/accounts')
      setAccounts(data || [])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load accounts')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchAccounts()
  }, [])

  // Listen for OAuth popup close and refetch
  useEffect(() => {
    const handleFocus = () => {
      fetchAccounts()
    }

    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [])

  const handleConnectOAuth = () => {
    window.open('/api/oauth/connect', 'ctrader-oauth', 'width=520,height=680')
  }

  const handleRoleChange = async (accountId: number, newRole: string) => {
    try {
      setPendingRows((prev) => new Set([...prev, accountId]))
      setRoleErrors((prev) => ({ ...prev, [accountId]: '' }))
      await api(`/api/accounts/${accountId}`, {
        method: 'PATCH',
        body: JSON.stringify({ role: newRole }),
      })
      await fetchAccounts()
    } catch (err) {
      const errorCode = err instanceof Error ? err.message : 'Unknown error'
      if (errorCode === '409') {
        setRoleErrors((prev) => ({ ...prev, [accountId]: 'A master already exists' }))
      } else {
        setRoleErrors((prev) => ({ ...prev, [accountId]: `Failed to update role (${errorCode})` }))
      }
    } finally {
      setPendingRows((prev) => {
        const next = new Set(prev)
        next.delete(accountId)
        return next
      })
    }
  }

  const handleEnabledToggle = async (accountId: number, currentEnabled: boolean) => {
    try {
      setPendingRows((prev) => new Set([...prev, accountId]))
      await api(`/api/accounts/${accountId}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !currentEnabled }),
      })
      await fetchAccounts()
    } catch (err) {
      const errorCode = err instanceof Error ? err.message : 'Unknown error'
      setError(`Failed to update enabled status (${errorCode})`)
    } finally {
      setPendingRows((prev) => {
        const next = new Set(prev)
        next.delete(accountId)
        return next
      })
    }
  }

  const handleMultiplierChange = async (accountId: number, newMultiplier: string) => {
    const multiplier = parseFloat(newMultiplier)

    if (isNaN(multiplier) || multiplier <= 0) {
      setMultiplierErrors((prev) => ({ ...prev, [accountId]: 'Multiplier must be greater than 0' }))
      // Reset to original value
      const account = accounts.find((a) => a.ctid_trader_account_id === accountId)
      if (account) {
        const newAccounts = accounts.map((acc) =>
          acc.ctid_trader_account_id === accountId
            ? { ...acc, multiplier: originalMultipliers[accountId] ?? account.multiplier }
            : acc
        )
        setAccounts(newAccounts)
      }
      return
    }

    try {
      setPendingRows((prev) => new Set([...prev, accountId]))
      setMultiplierErrors((prev) => ({ ...prev, [accountId]: '' }))
      await api(`/api/accounts/${accountId}`, {
        method: 'PATCH',
        body: JSON.stringify({ multiplier }),
      })
      await fetchAccounts()
    } catch (err) {
      const errorCode = err instanceof Error ? err.message : 'Unknown error'
      setMultiplierErrors((prev) => ({ ...prev, [accountId]: `Failed to update multiplier (${errorCode})` }))
      // Reset to original value on error
      const account = accounts.find((a) => a.ctid_trader_account_id === accountId)
      if (account) {
        const newAccounts = accounts.map((acc) =>
          acc.ctid_trader_account_id === accountId
            ? { ...acc, multiplier: originalMultipliers[accountId] ?? account.multiplier }
            : acc
        )
        setAccounts(newAccounts)
      }
    } finally {
      setPendingRows((prev) => {
        const next = new Set(prev)
        next.delete(accountId)
        return next
      })
    }
  }

  const handleDisconnect = async (accountId: number) => {
    if (!confirm('Are you sure you want to disconnect this account?')) {
      return
    }

    try {
      setPendingRows((prev) => new Set([...prev, accountId]))
      await api(`/api/accounts/connections/${accountId}`, {
        method: 'DELETE',
      })
      await fetchAccounts()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disconnect account')
    } finally {
      setPendingRows((prev) => {
        const next = new Set(prev)
        next.delete(accountId)
        return next
      })
    }
  }

  if (isLoading) {
    return <div className="text-gray-500">Loading accounts...</div>
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-sm font-medium text-red-800">{error}</p>
        </div>
      )}

      <div className="flex gap-4">
        <button
          onClick={handleConnectOAuth}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
        >
          Connect cTrader ID
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className="text-gray-500">No accounts connected</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse border border-gray-300">
            <thead className="bg-gray-50">
              <tr>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Login
                </th>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Type
                </th>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Role
                </th>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Multiplier
                </th>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Enabled
                </th>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Connection Status
                </th>
                <th className="border border-gray-300 px-4 py-2 text-left text-sm font-semibold">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const isPending = pendingRows.has(account.ctid_trader_account_id)
                return (
                  <tr key={account.ctid_trader_account_id} className={`hover:bg-gray-50 ${isPending ? 'opacity-60' : ''}`}>
                    <td className="border border-gray-300 px-4 py-2 text-sm">
                      {account.trader_login}
                    </td>
                    <td className="border border-gray-300 px-4 py-2 text-sm">
                      <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${
                        account.is_live
                          ? 'bg-red-100 text-red-800'
                          : 'bg-blue-100 text-blue-800'
                      }`}>
                        {account.is_live ? 'Live' : 'Demo'}
                      </span>
                    </td>
                    <td className="border border-gray-300 px-4 py-2 text-sm">
                      <select
                        value={account.role}
                        onChange={(e) => handleRoleChange(account.ctid_trader_account_id, e.target.value)}
                        disabled={isPending}
                        className="px-2 py-1 border border-gray-300 rounded text-sm disabled:opacity-50"
                      >
                        <option value="master">Master</option>
                        <option value="slave">Slave</option>
                        <option value="ignored">Ignored</option>
                      </select>
                      {roleErrors[account.ctid_trader_account_id] && (
                        <div className="text-red-600 text-xs mt-1">
                          {roleErrors[account.ctid_trader_account_id]}
                        </div>
                      )}
                    </td>
                    <td className="border border-gray-300 px-4 py-2 text-sm">
                      {account.role === 'slave' ? (
                        <div>
                          <input
                            type="number"
                            step="0.01"
                            min="0.01"
                            value={account.multiplier}
                            onBlur={(e) => {
                              setOriginalMultipliers((prev) => ({
                                ...prev,
                                [account.ctid_trader_account_id]: account.multiplier,
                              }))
                              handleMultiplierChange(account.ctid_trader_account_id, e.target.value)
                            }}
                            onChange={(e) => {
                              // Update local state for input feedback
                              const newAccounts = accounts.map((acc) =>
                                acc.ctid_trader_account_id === account.ctid_trader_account_id
                                  ? { ...acc, multiplier: parseFloat(e.target.value) || 0 }
                                  : acc
                              )
                              setAccounts(newAccounts)
                            }}
                            disabled={isPending}
                            className="px-2 py-1 border border-gray-300 rounded text-sm w-20 disabled:opacity-50"
                          />
                          {multiplierErrors[account.ctid_trader_account_id] && (
                            <div className="text-red-600 text-xs mt-1">
                              {multiplierErrors[account.ctid_trader_account_id]}
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                    <td className="border border-gray-300 px-4 py-2 text-sm">
                      <input
                        type="checkbox"
                        checked={account.enabled}
                        onChange={() => handleEnabledToggle(account.ctid_trader_account_id, account.enabled)}
                        disabled={isPending}
                        className="w-4 h-4 disabled:opacity-50"
                      />
                    </td>
                    <td className="border border-gray-300 px-4 py-2 text-sm">
                      <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${
                        account.connection_status === 'connected'
                          ? 'bg-green-100 text-green-800'
                          : 'bg-yellow-100 text-yellow-800'
                      }`}>
                        {account.connection_status}
                      </span>
                    </td>
                    <td className="border border-gray-300 px-4 py-2 text-sm space-x-2">
                      <button
                        onClick={() => handleDisconnect(account.ctid_trader_account_id)}
                        disabled={isPending}
                        className="px-2 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700 transition-colors disabled:opacity-50"
                      >
                        Disconnect
                      </button>
                      <button
                        onClick={handleConnectOAuth}
                        disabled={isPending}
                        className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-700 transition-colors disabled:opacity-50"
                      >
                        Re-grant access
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
