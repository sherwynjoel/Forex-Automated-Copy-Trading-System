/** Shared desk formatters — every money figure renders the same everywhere. */

export function money(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function signed(value: number | null | undefined): string {
  if (value == null) return '—'
  const formatted = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
  return `${value < 0 ? '-' : '+'}${formatted}`
}

export function formatWhen(ms: number | string | null | undefined): string {
  if (!ms) return '—'
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return String(ms)
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

/** The API surfaces errors as "409: a master already exists" — drop the code. */
export function stripCode(message: string): string {
  return message.replace(/^\d{3}:\s*/, '')
}

export function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? stripCode(err.message) : fallback
}
