/**
 * Skeleton lines on an inset surface. Announced as a busy status: the label
 * is the region's name and its one visually hidden line of text (a live
 * region is read from its content, not its aria-label); the bars carry no
 * text.
 */
export default function Loading({ lines = 3, label = 'Loading', className }: {
  lines?: number
  label?: string
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" aria-label={label}
         className={['inset p-4 space-y-3', className ?? ''].filter(Boolean).join(' ')}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          data-skeleton
          aria-hidden="true"
          className="h-3 rounded-full bg-line animate-pulse"
          style={{ width: `${88 - (i % 3) * 14}%` }}
        />
      ))}
    </div>
  )
}
