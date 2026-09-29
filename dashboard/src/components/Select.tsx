import { forwardRef, type SelectHTMLAttributes } from 'react'

/**
 * The desk's one select. Painted on the card surface so index.css's
 * `select.bg-card` rule supplies the violet chevron in both themes.
 */
export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  block?: boolean
}

const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { block, className, children, ...rest },
  ref,
) {
  const classes = [
    'rounded-control border border-field-line bg-card px-2 py-1.5 text-sm text-ink',
    'transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
    block ? 'w-full' : '',
    className ?? '',
  ].filter(Boolean).join(' ')
  return (
    <select ref={ref} className={classes} {...rest}>
      {children}
    </select>
  )
})

export default Select
