import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { money } from '../../lib/format'
import Input from '../../components/Input'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import { checkSignedAmount } from './AdjustDialog'
import type { Bonus, InvestorRow } from '../../lib/types'

/**
 * Pays a bonus into the investor's Credit wallet (negative: takes one back,
 * never below zero), confirmed with the ADMIN's own MPIN. Validation
 * failures are thrown so PinConfirmDialog shows them and clears the PIN,
 * the same path as AdjustDialog.
 */
export default function GrantBonusDialog({ orgId, investor, onCancel, onGranted }: {
  orgId: number
  investor: InvestorRow | null
  onCancel: () => void
  onGranted: (bonus: Bonus) => void | Promise<void>
}) {
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // A fresh form for every investor; nothing typed for one carries to the next.
  useEffect(() => { setAmount(''); setNote('') }, [investor?.user_id])

  const confirm = async (mpin: string) => {
    if (!investor) return
    // The same checks and messages as AdjustDialog (one copy, exported there).
    const raw = checkSignedAmount(amount, note)
    setBusy(true)
    try {
      // redirectOn401 off: a wrong MPIN stays an inline error.
      const bonus = await api<Bonus>(
        `/api/orgs/${orgId}/investors/${investor.user_id}/bonuses`,
        { method: 'POST', body: JSON.stringify({ amount: raw, note: note.trim(), mpin }) },
        { redirectOn401: false },
      )
      await onGranted(bonus)
    } finally {
      setBusy(false)
    }
  }

  return (
    <PinConfirmDialog
      open={investor != null}
      title={investor ? `Grant ${investor.display_name} a bonus` : ''}
      confirmLabel="Pay bonus"
      busy={busy}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <p>
        Pays into the Credit wallet{investor ? ` (now ${money(investor.balances.credit)})` : ''}. A
        negative amount takes a bonus back, never below zero. The investor is notified either way.
      </p>
      <label className="block">
        <span className="desk-label block mb-1">Amount (USD, signed)</span>
        <Input aria-label="Bonus amount" num inputMode="decimal" placeholder="25.00" value={amount}
               onChange={(e) => setAmount(e.target.value)} />
      </label>
      <label className="block">
        <span className="desk-label block mb-1">Note</span>
        <Input aria-label="Bonus note" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
    </PinConfirmDialog>
  )
}
