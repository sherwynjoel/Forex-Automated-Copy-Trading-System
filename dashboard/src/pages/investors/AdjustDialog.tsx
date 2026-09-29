import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { money } from '../../lib/format'
import { WALLETS, walletLabel } from '../../lib/investor'
import Input from '../../components/Input'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { InvestorRow, WalletEntry, WalletKind } from '../../lib/types'

/** A signed amount with at most two decimals: "-25.00", "100", "+12.5". */
const SIGNED_AMOUNT = /^[-+]?\d+(\.\d{1,2})?$/

/**
 * Posts a signed ledger entry to one of the investor's wallets, confirmed
 * with the ADMIN's own MPIN. Validation failures are thrown so the
 * PinConfirmDialog shows them in its own error line and clears the PIN,
 * the same path a wrong MPIN takes.
 */
export default function AdjustDialog({ orgId, investor, onCancel, onPosted }: {
  orgId: number
  investor: InvestorRow | null
  onCancel: () => void
  onPosted: (entry: WalletEntry) => void | Promise<void>
}) {
  const [wallet, setWallet] = useState<WalletKind>('main')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // A fresh form for every investor; nothing typed for one carries to the next.
  useEffect(() => { setWallet('main'); setAmount(''); setNote('') }, [investor?.user_id])

  const confirm = async (mpin: string) => {
    if (!investor) return
    const raw = amount.trim()
    if (!SIGNED_AMOUNT.test(raw)) {
      throw new Error('Enter a signed amount with at most two decimals, for example -25.00 or 100')
    }
    if (Number(raw) === 0) throw new Error('amount must not be zero')
    if (note.trim() === '') throw new Error('A note is required')
    setBusy(true)
    try {
      // Straight through api() with redirectOn401 off: a wrong MPIN answers
      // 401 and must stay an inline error, never a bounce to /login.
      const entry = await api<WalletEntry>(
        `/api/orgs/${orgId}/investors/${investor.user_id}/adjustments`,
        { method: 'POST', body: JSON.stringify({ wallet, amount: raw, note: note.trim(), mpin }) },
        { redirectOn401: false },
      )
      await onPosted(entry)
    } finally {
      setBusy(false)
    }
  }

  return (
    <PinConfirmDialog
      open={investor != null}
      title={investor ? `Adjust ${investor.display_name}'s wallet` : ''}
      confirmLabel="Post adjustment"
      busy={busy}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <p>
        Posts a signed ledger entry. A negative amount debits the wallet, a positive one
        credits it, and the investor is emailed either way.
      </p>
      <label className="block">
        <span className="desk-label block mb-1">Wallet</span>
        <Select block aria-label="Wallet" value={wallet} onChange={(e) => setWallet(e.target.value as WalletKind)}>
          {WALLETS.map((w) => (
            <option key={w} value={w}>
              {walletLabel(w)}{investor ? ` — ${money(investor.balances[w])}` : ''}
            </option>
          ))}
        </Select>
      </label>
      <label className="block">
        <span className="desk-label block mb-1">Amount (signed)</span>
        <Input aria-label="Amount" num inputMode="decimal" placeholder="-25.00" value={amount}
               onChange={(e) => setAmount(e.target.value)} />
      </label>
      <label className="block">
        <span className="desk-label block mb-1">Note</span>
        <Input aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
    </PinConfirmDialog>
  )
}
