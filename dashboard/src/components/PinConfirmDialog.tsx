import { useEffect, useState, type ReactNode } from 'react'
import type { ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import ConfirmDialog from './ConfirmDialog'
import PinInput from './PinInput'

export interface PinConfirmDialogProps {
  open: boolean
  title: string
  /** The summary of what is about to happen ("Fee 2.50 USD, you receive 247.50 USD."). */
  children: ReactNode
  confirmLabel: string
  busy?: boolean
  /** Runs the request with the MPIN. Reject to keep the dialog open with the
   *  refusal shown; resolve and the CALLER closes the dialog. */
  onConfirm: (mpin: string) => Promise<void>
  onCancel: () => void
}

const MPIN_LENGTH = 6

/**
 * The words for a refused MPIN step-up, the same ones AccountSecurity uses
 * for a refused MPIN change: 401 with attempts_left, 423 with locked_until,
 * 409 "MPIN not set", otherwise the server's detail without its code -- a
 * route's own 409 (a transfer's "no account linked yet") reads as itself.
 */
export function mpinErrorText(err: unknown, fallback: string): string {
  const res = (err as ApiError | undefined)?.response
  const left = res?.body?.attempts_left
  const until = res?.body?.locked_until
  if (res?.status === 401 && typeof left === 'number') {
    return `Wrong MPIN, ${left} ${left === 1 ? 'try' : 'tries'} left`
  }
  if (res?.status === 423 && typeof until === 'string') {
    const minutes = Math.max(1, Math.ceil((Date.parse(until) - Date.now()) / 60000))
    return `MPIN locked. Try again in about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
  }
  if (res?.status === 409 && res.body?.detail === 'MPIN not set') return 'Set your MPIN first'
  return errorText(err, fallback)
}

/**
 * ConfirmDialog with the MPIN as the confirmation: the summary the caller
 * passes, six masked boxes, and a confirm button that only unlocks on the
 * sixth digit. A refused MPIN is announced under the boxes and the boxes
 * are cleared, so a second guess starts clean. The dialog never closes
 * itself: the caller flips `open` on success (and refreshes its list).
 */
export default function PinConfirmDialog({
  open, title, children, confirmLabel, busy, onConfirm, onCancel,
}: PinConfirmDialogProps) {
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  // A closed dialog forgets the PIN and the last refusal.
  useEffect(() => {
    if (!open) {
      setPin('')
      setError(null)
      setWorking(false)
    }
  }, [open])

  const confirm = async () => {
    if (pin.length !== MPIN_LENGTH || working) return
    setError(null)
    setWorking(true)
    try {
      await onConfirm(pin)
    } catch (err) {
      setError(mpinErrorText(err, 'Could not confirm'))
      setPin('')
    } finally {
      setWorking(false)
    }
  }

  const inFlight = Boolean(busy) || working

  return (
    <ConfirmDialog
      open={open}
      title={title}
      confirmLabel={confirmLabel}
      busy={inFlight}
      disabled={pin.length !== MPIN_LENGTH}
      onConfirm={() => { void confirm() }}
      onCancel={onCancel}
    >
      {children}
      <PinInput id="confirm-mpin" label="Your MPIN" value={pin} onChange={setPin} disabled={inFlight} error={error} />
    </ConfirmDialog>
  )
}
