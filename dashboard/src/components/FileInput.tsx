import { useEffect, useId, useRef, useState, type ChangeEvent } from 'react'
import Button from './Button'

/** What a deposit receipt or payout proof may be. The server sniffs the
 *  bytes and refuses anything else; this list only saves a round trip. */
export const RECEIPT_ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024

export interface FileInputProps {
  id: string
  label: string
  accept: string[]
  maxBytes: number
  value: File | null
  onChange: (file: File | null) => void
  required?: boolean
  hint?: string
  /** The caller's own refusal (e.g. "Upload the receipt first"); shown in
   *  place of the component's type/size message. */
  error?: string | null
  disabled?: boolean
}

/** "512 B", "48 KB", "5 MB", "1.5 MB". */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  const mb = n / (1024 * 1024)
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`
}

/**
 * The desk's one file field: a visually hidden native input behind a
 * secondary "Choose file" button, the chosen file's name and size, a
 * thumbnail for images (an object URL, revoked when the file changes) and
 * a ghost Remove. Type and size are refused here first so the investor sees
 * why before a byte is sent; the server checks again by sniffing.
 */
export default function FileInput({
  id, label, accept, maxBytes, value, onChange, required, hint, error, disabled,
}: FileInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const hintId = useId()
  const errorId = useId()

  useEffect(() => {
    if (!value || !value.type.startsWith('image/') || typeof URL.createObjectURL !== 'function') {
      setPreview(null)
      return
    }
    const url = URL.createObjectURL(value)
    setPreview(url)
    return () => { URL.revokeObjectURL(url) }
  }, [value])

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0] ?? null
    // Cleared so the same file can be chosen again after Remove.
    e.currentTarget.value = ''
    if (!file) return
    if (!accept.includes(file.type)) {
      setRefusal('That file type is not accepted')
      onChange(null)
      return
    }
    if (file.size > maxBytes) {
      setRefusal(`File is larger than ${formatBytes(maxBytes)}`)
      onChange(null)
      return
    }
    setRefusal(null)
    onChange(file)
  }

  const remove = () => {
    setRefusal(null)
    onChange(null)
  }

  const problem = error ?? refusal
  const hintLine = [required ? 'Required.' : null, hint].filter(Boolean).join(' ')
  const describedBy = [hintLine ? hintId : null, problem ? errorId : null].filter(Boolean).join(' ')

  return (
    <div>
      <label htmlFor={id} className="desk-label block mb-1">{label}</label>
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={accept.join(',')}
        className="sr-only"
        onChange={pick}
        disabled={disabled}
        required={required}
        aria-required={required || undefined}
        aria-invalid={problem ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" size="sm" disabled={disabled} onClick={() => inputRef.current?.click()}>
          Choose file
        </Button>
        {value ? (
          <>
            {preview && (
              <img src={preview} alt="" className="h-12 w-12 rounded-inset border border-line object-cover" />
            )}
            <span className="text-sm text-ink">
              {value.name}{' '}
              <span className="text-ink-faint">({formatBytes(value.size)})</span>
            </span>
            <Button variant="ghost" tone="neutral" size="sm" disabled={disabled} onClick={remove}>
              Remove
            </Button>
          </>
        ) : (
          <span className="text-sm text-ink-faint">No file chosen</span>
        )}
      </div>
      {hintLine && <p id={hintId} className="mt-1 text-xs text-ink-soft">{hintLine}</p>}
      {problem && <p id={errorId} role="alert" className="mt-1 text-sm text-loss-deep">{problem}</p>}
    </div>
  )
}
