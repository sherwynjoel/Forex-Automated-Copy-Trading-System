/**
 * Skeleton lines on an inset surface. Announced once as a busy status; the
 * visible bars carry no text so nothing reads "Loading..." aloud twice.
 */
export default function Loading({ lines = 3, label = 'Loading', className }: {
  lines?: number
  label?: string
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" aria-label={label}
         className={['inset p-4 space-y-3', className ?? ''].filter(Boolean).join(' ')}>
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
