/** Shared desk formatters — every money figure renders the same everywhere. */

/**
 * Two decimals with thousands separators. `unit` appends the currency or
 * coin after one space ("500.00 USDT"); without it the output is exactly
 * what it always was. A string is what a form field holds ("500") and is
 * read as a number. An unknown or unparseable amount is a bare dash --
 * never "— USDT", which would claim a unit for a number nobody has.
 */
export function money(value: number | string | null | undefined, unit?: string): string {
  if (value == null) return '—'
  const n = typeof value === 'string' ? (value.trim() === '' ? Number.NaN : Number(value)) : value
  if (!Number.isFinite(n)) return '—'
  const formatted = n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return unit ? `${formatted} ${unit}` : formatted
}

export function signed(value: number | null | undefined): string {
  if (value == null) return '—'
  const formatted = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
  return `${value < 0 ? '-' : '+'}${formatted}`
}

/**
 * "04 Mar, 10:05:06" for a moment in the current year; "04 Mar 2025,
 * 10:05:06" otherwise, so a row from last December never reads as next
 * month's.
 */
export function formatWhen(ms: number | string | null | undefined): string {
  if (!ms) return '—'
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return String(ms)
  const options: Intl.DateTimeFormatOptions = {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }
  if (d.getFullYear() !== new Date().getFullYear()) options.year = 'numeric'
  return d.toLocaleString('en-GB', options)
}

/** The API surfaces errors as "409: a master already exists" — drop the code. */
export function stripCode(message: string): string {
  return message.replace(/^\d{3}:\s*/, '')
}

export function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? stripCode(err.message) : fallback
}
