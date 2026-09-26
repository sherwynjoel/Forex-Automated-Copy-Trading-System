import { useEffect, useId, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react'
import Button from './Button'

const LENGTH = 6

/**
 * Six single-digit boxes for the MPIN. Digits are masked; typing advances,
 * Backspace retreats, paste fills, arrows move. `value` is the digits typed
 * so far (0-6 characters); `onComplete` fires once when the sixth lands.
 * The group is labelled and the error is announced and linked to every box.
 */
export default function PinInput({ id, label, value, onChange, onComplete, disabled, error, autoFocus }: {
  id: string
  label: string
  value: string
  onChange: (next: string) => void
  onComplete?: (pin: string) => void
  disabled?: boolean
  error?: string | null
  autoFocus?: boolean
}) {
  const refs = useRef<Array<HTMLInputElement | null>>([])
  const [show, setShow] = useState(false)
  const errorId = useId()
  const labelId = useId()
  const completedFor = useRef<string | null>(null)

  useEffect(() => {
    if (autoFocus) refs.current[0]?.focus()
  }, [autoFocus])

  useEffect(() => {
    if (value.length === LENGTH && completedFor.current !== value) {
      completedFor.current = value
      onComplete?.(value)
    }
    if (value.length < LENGTH) completedFor.current = null
  }, [value, onComplete])

  const focusBox = (i: number) => refs.current[Math.max(0, Math.min(LENGTH - 1, i))]?.focus()

  const onKeyDown = (i: number) => (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) { e.preventDefault(); return }
    if (/^[0-9]$/.test(e.key)) {
      e.preventDefault()
      const next = value.slice(0, i) + e.key
      onChange(next.slice(0, LENGTH))
      focusBox(i + 1)
    } else if (e.key === 'Backspace') {
      e.preventDefault()
      if (value.length > i || i === value.length) {
        const target = value[i] ? i : Math.max(0, i - 1)
        onChange(value.slice(0, target))
        focusBox(target)
      }
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault(); focusBox(i - 1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault(); focusBox(i + 1)
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      // Letters and symbols never land in an MPIN box.
      e.preventDefault()
    }
  }

  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault()
    if (disabled) return
    const digits = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, LENGTH)
    if (!digits) return
    onChange(digits)
    focusBox(digits.length >= LENGTH ? LENGTH - 1 : digits.length)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span id={labelId} className="desk-label">{label}</span>
        <Button
          variant="ghost" tone="neutral" size="sm" type="button"
          disabled={disabled}
          aria-pressed={show}
          onClick={() => setShow((s) => !s)}
        >
          {show ? 'Hide' : 'Show'}
        </Button>
      </div>
      <div role="group" aria-labelledby={labelId} className="flex gap-2">
        {Array.from({ length: LENGTH }, (_, i) => (
          <input
            key={i}
            ref={(el) => { refs.current[i] = el }}
            id={i === 0 ? id : `${id}-${i}`}
            type={show ? 'text' : 'password'}
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete={i === 0 ? 'one-time-code' : 'off'}
            maxLength={1}
            value={value[i] ?? ''}
            onChange={() => { /* handled in onKeyDown / onPaste; controlled value */ }}
            onKeyDown={onKeyDown(i)}
            onPaste={onPaste}
            onFocus={(e) => e.currentTarget.select()}
            disabled={disabled}
            aria-label={`${label} digit ${i + 1} of ${LENGTH}`}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className={[
              'num h-12 w-11 rounded border bg-card text-center text-lg text-ink',
              'disabled:opacity-50',
              error ? 'border-loss' : 'border-field-line',
            ].join(' ')}
          />
        ))}
      </div>
      {error && (
        <p id={errorId} role="alert" className="mt-2 text-sm text-loss-deep">{error}</p>
      )}
    </div>
  )
}
