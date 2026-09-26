import { forwardRef, type InputHTMLAttributes } from 'react'

/**
 * The desk's one text field. Card surface, strong hairline, ink text and a
 * token placeholder so the hint never drops below AA. `num` switches the
 * digits to the tabular numeric style every figure in the product uses;
 * `invalid` paints the loss border and tells assistive tech.
 */
export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  num?: boolean
  invalid?: boolean
}

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { num, invalid, className, ...rest },
  ref,
) {
  const classes = [
    'w-full rounded border bg-card px-3 py-2 text-sm text-ink placeholder:text-ink-faint',
    'disabled:opacity-50 disabled:cursor-not-allowed',
    invalid ? 'border-loss' : 'border-field-line',
    num ? 'num' : '',
    className ?? '',
  ].filter(Boolean).join(' ')
  return <input ref={ref} className={classes} aria-invalid={invalid || undefined} {...rest} />
})

export default Input
