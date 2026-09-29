import { useId, useState } from 'react'

/**
 * Error text that used to be cut short and live only in a `title` tooltip,
 * which touch and keyboard users cannot reach. The full text is always in
 * the DOM. Collapsed, CSS truncates it to one line; "Show details" expands
 * it in place. `title` stays for pointer users.
 *
 * The toggle inherits the surrounding colour (`text-current`) so it is
 * always the pair its container was proven with, e.g. loss-deep on
 * loss-wash inside a follower tile.
 */
export default function ExpandableText({ text, limit, className, testId }: {
  text: string
  /** Texts at or under this many characters render whole, with no toggle. */
  limit: number
  className?: string
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  const id = useId()

  if (text.length <= limit) {
    return (
      <span data-testid={testId} title={text} className={`break-words ${className ?? ''}`}>
        {text}
      </span>
    )
  }

  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <span
        id={id}
        data-testid={testId}
        title={text}
        className={`${open ? 'whitespace-normal break-words' : 'block max-w-full truncate'} ${className ?? ''}`}
      >
        {text}
      </span>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-semibold text-current underline underline-offset-2 min-h-11 md:min-h-0"
      >
        {open ? 'Hide details' : 'Show details'}
      </button>
    </span>
  )
}
