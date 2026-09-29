import Button from './Button'
import { money, signed } from '../lib/format'
import { useHiddenBalances } from '../lib/hideBalances'

export interface MoneyProps {
  value: number | string | null | undefined
  unit?: string
  /** "+1,000.00" / "-250.00" for ledger amounts; the plain form otherwise. */
  signed?: boolean
  className?: string
}

/** A form string ("250"), a number, or nothing. Empty and unparseable read as nothing. */
function asNumber(value: MoneyProps['value']): number | null {
  if (value == null) return null
  const n = typeof value === 'string' ? (value.trim() === '' ? Number.NaN : Number(value)) : value
  return Number.isFinite(n) ? n : null
}

// A caller's className often carries a tone that was computed from the real
// (unmasked) value -- e.g. `text-loss` on a negative ledger amount. Stripped
// before the hidden state adds its own neutral color, so "••••" can never be
// colored by a tone that itself gives away the sign.
const TONE_CLASS = /\btext-(profit|loss|warn|brand)(-deep)?\b/g

/**
 * The one way a money figure reaches the screen. Honours the hide-balances
 * switch: when hidden it renders four dots named "Hidden amount" and the
 * real value is nowhere in the DOM, so a screen reader hears "hidden" and
 * a shoulder-surfer sees nothing -- not even through a leftover profit/loss
 * colour, inherited from a wrapper or passed in via `className`.
 */
export default function Money({ value, unit, signed: withSign, className }: MoneyProps) {
  const [hidden] = useHiddenBalances()
  if (hidden) {
    const neutral = [(className ?? '').replace(TONE_CLASS, '').replace(/\s+/g, ' ').trim(), 'text-ink-soft']
      .filter(Boolean).join(' ')
    return <span role="img" aria-label="Hidden amount" className={neutral}>••••</span>
  }
  let text: string
  if (withSign) {
    const s = signed(asNumber(value))
    text = s === '—' || !unit ? s : `${s} ${unit}`
  } else {
    text = money(value, unit)
  }
  return <span className={['num', className ?? ''].filter(Boolean).join(' ')}>{text}</span>
}

/** The switch itself: a quiet ghost button that says what it will do next. */
export function HideBalancesToggle() {
  const [hidden, setHidden] = useHiddenBalances()
  return (
    <Button variant="ghost" tone="neutral" size="sm" aria-pressed={hidden} onClick={() => setHidden(!hidden)}>
      {hidden ? 'Show balances' : 'Hide balances'}
    </Button>
  )
}
