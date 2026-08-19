/**
 * Token-colored status marker — replaces emoji glyphs so status reads in the
 * desk's own palette. Meaning is always paired with adjacent text; the dot is
 * aria-hidden.
 */
export default function StatusDot({ tone, paused = false }: {
  tone: 'ok' | 'degraded' | 'paused' | 'warn'
  paused?: boolean
}) {
  const fill =
    tone === 'ok' ? 'var(--color-profit)'
    : tone === 'degraded' ? 'var(--color-loss)'
    : tone === 'warn' ? 'var(--color-warn)'
    : 'var(--color-ink-faint)'
  return (
    <svg aria-hidden="true" viewBox="0 0 12 12" className="inline-block h-3 w-3 shrink-0">
      {tone === 'paused' || paused ? (
        <>
          <circle cx="6" cy="6" r="5.25" fill="none" stroke={fill} strokeWidth="1.5" />
          <rect x="3.9" y="3.5" width="1.5" height="5" fill={fill} />
          <rect x="6.6" y="3.5" width="1.5" height="5" fill={fill} />
        </>
      ) : (
        <circle cx="6" cy="6" r="5" fill={fill} />
      )}
    </svg>
  )
}
