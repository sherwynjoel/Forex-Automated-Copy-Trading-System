import {
  useCallback, useEffect, useId, useRef, useState,
  type ChangeEvent, type ClipboardEvent, type FocusEvent, type KeyboardEvent,
} from 'react'
import { AnimatePresence, motion, useAnimate, useReducedMotion } from 'motion/react'
import Button from './Button'

const LENGTH = 6
const GROUP = 3
const DIGIT = /^[0-9]$/
const MASK = '●'
const EASE = [0.23, 1, 0.32, 1] as const

/**
 * Six single-digit boxes for the MPIN. Digits are masked as dots until
 * "Show"; each one animates in, the active box carries a blinking caret,
 * and a wrong MPIN shakes the row. `value` is the digits typed so far
 * (0-6 characters, always a prefix): typing at a box truncates what follows,
 * Backspace on an empty box clears the previous one, paste fills from the
 * start, arrows/Home/End move without editing. `onComplete` fires once when
 * the sixth digit lands. The group is labelled and the error is announced
 * and linked to every box. Every animation collapses under reduced motion.
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
  const [focused, setFocused] = useState(-1)
  const errorId = useId()
  const labelId = useId()
  const completedFor = useRef<string | null>(null)
  const reduced = useReducedMotion()
  const [row, animateRow] = useAnimate<HTMLDivElement>()

  const chars = Array.from({ length: LENGTH }, (_, i) => value[i] ?? '')
  // Entry is sequential: the first empty box (or the last one when full) is
  // where typing continues, and focus never lands beyond it. Kept in a ref
  // because a handler moves focus in the same tick it changes the value,
  // before React has re-rendered with the new prop.
  const firstEmpty = useRef(0)
  firstEmpty.current = Math.min(value.length, LENGTH - 1)

  const focusAt = useCallback((i: number) => {
    const el = refs.current[Math.max(0, Math.min(LENGTH - 1, i))]
    if (!el) return
    el.focus()
    el.select()
  }, [])

  useEffect(() => {
    if (autoFocus && !disabled) focusAt(0)
  }, [autoFocus, disabled, focusAt])

  useEffect(() => {
    if (value.length === LENGTH && completedFor.current !== value) {
      completedFor.current = value
      onComplete?.(value)
    }
    if (value.length < LENGTH) completedFor.current = null
  }, [value, onComplete])

  // A parent that clears the value (a wrong MPIN) leaves focus on the last
  // box; bring the caret back to where entry now continues.
  useEffect(() => {
    const active = document.activeElement as HTMLInputElement | null
    const i = active ? refs.current.indexOf(active) : -1
    if (i > firstEmpty.current) focusAt(firstEmpty.current)
  }, [value, focusAt])

  // A new error shakes the row once; the message itself is announced below.
  useEffect(() => {
    if (!error || reduced || !row.current) return
    animateRow(row.current, { x: [0, -5, 4, -3, 0] }, { duration: 0.32, ease: EASE })
  }, [error, reduced, row, animateRow])

  /** Put `digits` at box `i`, dropping whatever followed, and move on. */
  const put = (i: number, digits: string) => {
    const next = (value.slice(0, i) + digits).slice(0, LENGTH)
    onChange(next)
    firstEmpty.current = Math.min(next.length, LENGTH - 1)
    focusAt(firstEmpty.current)
  }

  const onBoxChange = (i: number) => (e: ChangeEvent<HTMLInputElement>) => {
    if (disabled) return
    const raw = e.currentTarget.value
    const previous = chars[i]
    // A box that already holds a digit receives "<old><new>" from the
    // browser; keep only what was added. Autofill may drop several at once.
    const added = raw.length > 1 && previous && raw.startsWith(previous) ? raw.slice(previous.length) : raw
    const digits = added.replace(/\D/g, '')
    if (!digits) {
      if (raw === '' && previous) onChange(value.slice(0, i))
      else e.currentTarget.value = previous
      return
    }
    put(i, digits)
  }

  const onKeyDown = (i: number) => (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) { e.preventDefault(); return }
    switch (e.key) {
      case 'Backspace':
        e.preventDefault()
        if (chars[i]) { onChange(value.slice(0, i)); firstEmpty.current = i }
        else if (i > 0) { onChange(value.slice(0, i - 1)); firstEmpty.current = i - 1; focusAt(i - 1) }
        return
      case 'Delete':
        e.preventDefault(); onChange(value.slice(0, i)); firstEmpty.current = i; return
      case 'ArrowLeft':
        e.preventDefault(); focusAt(i - 1); return
      case 'ArrowRight':
        e.preventDefault(); focusAt(i + 1); return
      case 'Home':
        e.preventDefault(); focusAt(0); return
      case 'End':
        e.preventDefault(); focusAt(LENGTH - 1); return
    }
    // Letters and symbols never land in an MPIN box.
    if (e.key.length === 1 && !DIGIT.test(e.key) && !e.ctrlKey && !e.metaKey) e.preventDefault()
  }

  const onPaste = (i: number) => (e: ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault()
    if (disabled) return
    const digits = e.clipboardData.getData('text').replace(/\D/g, '')
    if (!digits) return
    put(digits.length >= LENGTH ? 0 : i, digits)
  }

  const onFocus = (i: number) => (e: FocusEvent<HTMLInputElement>) => {
    e.currentTarget.select()
    if (firstEmpty.current < i) { focusAt(firstEmpty.current); return }
    setFocused(i)
  }

  const onBlur = (e: FocusEvent<HTMLInputElement>) => {
    const to = e.relatedTarget as HTMLInputElement | null
    if (to && refs.current.includes(to)) return
    setFocused(-1)
  }

  const glyphEnter = reduced ? { duration: 0 } : { duration: 0.22, ease: EASE }

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
      <motion.div ref={row} role="group" aria-labelledby={labelId} className="flex gap-1.5 sm:gap-2">
        {chars.map((char, i) => {
          const active = focused === i
          const glyph = char ? (show ? char : MASK) : ''
          return (
            <div key={i} className={`relative h-11 w-9 sm:h-12 sm:w-11 ${i > 0 && i % GROUP === 0 ? 'ml-2 sm:ml-3' : ''}`}>
              <input
                ref={(el) => { refs.current[i] = el }}
                id={i === 0 ? id : `${id}-${i}`}
                type={show ? 'text' : 'password'}
                inputMode="numeric"
                autoComplete={i === 0 ? 'one-time-code' : 'off'}
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck={false}
                value={char}
                onChange={onBoxChange(i)}
                onKeyDown={onKeyDown(i)}
                onPaste={onPaste(i)}
                onFocus={onFocus(i)}
                onBlur={onBlur}
                disabled={disabled}
                aria-label={`${label} digit ${i + 1} of ${LENGTH}`}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? errorId : undefined}
                className={[
                  // The input owns focus and the value; the glyph beside it is
                  // what the eye sees, so its own text and caret stay invisible.
                  'num h-11 w-9 sm:h-12 sm:w-11 rounded border bg-card text-center text-lg',
                  'text-transparent caret-transparent selection:bg-transparent',
                  'transition-[border-color] duration-150 motion-reduce:transition-none disabled:opacity-50',
                  // The global focus ring marks the active box; the border only
                  // carries the error state, like every other control.
                  error ? 'border-loss' : 'border-field-line',
                ].join(' ')}
              />
              <span aria-hidden className="pointer-events-none absolute inset-0 grid place-items-center">
                <AnimatePresence initial={false}>
                  {glyph ? (
                    <motion.span
                      key={glyph}
                      initial={reduced ? false : { opacity: 0, scale: 0.97, y: 10, filter: 'blur(6px)' }}
                      animate={{ opacity: 1, scale: 1, y: 0, filter: 'blur(0px)' }}
                      exit={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.98, y: -6, filter: 'blur(3px)' }}
                      transition={glyphEnter}
                      className="num col-start-1 row-start-1 text-lg text-ink"
                    >
                      {glyph}
                    </motion.span>
                  ) : null}
                </AnimatePresence>
                {active && !char && !disabled ? (
                  <motion.span
                    className="col-start-1 row-start-1 block h-[17px] w-[1.5px] rounded-[1px] bg-ink"
                    initial={{ opacity: 1 }}
                    animate={reduced ? { opacity: 1 } : { opacity: [1, 1, 0, 0] }}
                    transition={reduced
                      ? { duration: 0 }
                      : { duration: 1.06, times: [0, 0.5, 0.5, 1], repeat: Infinity, ease: 'linear' }}
                  />
                ) : null}
              </span>
            </div>
          )
        })}
      </motion.div>
      {error && (
        <p id={errorId} role="alert" className="mt-2 text-sm text-loss-deep">{error}</p>
      )}
    </div>
  )
}
