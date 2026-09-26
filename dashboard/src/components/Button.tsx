import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost'
export type ButtonTone = 'brand' | 'profit' | 'loss' | 'warn' | 'neutral' | 'inverse'
export type ButtonSize = 'sm' | 'md' | 'lg'

/**
 * The desk's one button. `variant` is the shape (filled, outlined, text),
 * `tone` the meaning (brand for ordinary actions, profit for Buy/resume,
 * loss for Sell/stop/destroy, warn for dry-run, neutral for quiet chrome
 * such as Dismiss, sidebar rows and icon buttons, inverse for a control that
 * sits ON a solid accent fill, such as Dismiss inside a red alert banner). Every button keeps the
 * 44px touch floor on phones and drops it from md up, where density wins.
 *
 * Class strings are spelled out per variant × tone so Tailwind can see them.
 */
const BASE =
  'inline-flex items-center justify-center gap-2 rounded font-semibold transition-colors ' +
  'min-h-11 md:min-h-0 disabled:opacity-50 disabled:cursor-not-allowed'

const SIZE: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  // Hero and empty-state calls to action only.
  lg: 'px-6 py-3 text-base',
}

const PRIMARY: Record<ButtonTone, string> = {
  brand: 'bg-brand text-on-accent hover:bg-brand-deep',
  profit: 'bg-profit text-on-accent hover:bg-profit-deep',
  loss: 'bg-loss text-on-accent hover:bg-loss-deep',
  warn: 'bg-warn text-on-accent hover:bg-warn-deep',
  // A filled neutral is ink on paper: the rare "dark" button.
  neutral: 'bg-ink text-paper hover:bg-ink-soft',
  inverse: 'bg-on-accent text-ink hover:bg-paper',
}

const SECONDARY: Record<ButtonTone, string> = {
  // Plain outlined control; brand tone is the "quiet primary" used beside a filled one.
  brand: 'border border-brand bg-card text-brand hover:bg-brand-wash hover:text-brand-deep',
  profit: 'border border-profit bg-card text-profit-deep hover:bg-profit-wash',
  loss: 'border border-loss bg-card text-loss hover:bg-loss hover:text-on-accent',
  warn: 'border border-warn bg-card text-warn-deep hover:bg-warn-wash',
  neutral: 'border border-field-line bg-card text-ink hover:bg-line',
  inverse: 'border border-on-accent text-on-accent hover:bg-on-accent/15',
}
const SECONDARY_NEUTRAL = SECONDARY.neutral

const GHOST: Record<ButtonTone, string> = {
  brand: 'text-brand hover:text-brand-deep hover:underline',
  profit: 'text-profit hover:text-profit-deep hover:underline',
  loss: 'text-loss hover:text-loss-deep hover:underline',
  warn: 'text-warn-deep hover:underline',
  neutral: 'text-ink-soft hover:text-ink hover:bg-line',
  inverse: 'text-on-accent hover:underline',
}

function recipe(variant: ButtonVariant, tone: ButtonTone | undefined, size: ButtonSize, block?: boolean, extra?: string) {
  let look: string
  if (variant === 'primary') look = PRIMARY[tone ?? 'brand']
  else if (variant === 'secondary') look = tone ? SECONDARY[tone] : SECONDARY_NEUTRAL
  else look = GHOST[tone ?? 'brand']
  return [BASE, SIZE[size], look, block ? 'w-full' : '', extra ?? ''].filter(Boolean).join(' ')
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  /** Omitted on `secondary` gives the neutral outline; on others it defaults to brand. */
  tone?: ButtonTone
  size?: ButtonSize
  block?: boolean
  /** Work in flight: disabled and announced; the caller supplies the label. */
  busy?: boolean
  /** Render as a router link with the same recipe. */
  to?: string
  children: ReactNode
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', tone, size = 'md', block, busy, to, className, type = 'button', disabled, children, ...rest },
  ref,
) {
  const classes = recipe(variant, tone, size, block, className)
  if (to) {
    return (
      <Link to={to} className={classes}>
        {children}
      </Link>
    )
  }
  return (
    <button
      ref={ref}
      type={type}
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {children}
    </button>
  )
})

export default Button
