/**
 * The MirrorFleet mark — a fleet of sails in formation, mirrored on the
 * waterline (Concept A). Pure SVG so it recolors and scales anywhere.
 */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className="shrink-0"
    >
      <polygon points="14,38 38,26 38,50" fill="#6c5fc7" />
      <polygon points="42,30 62,20 62,40" fill="#8b7fd7" />
      <polygon points="66,23 82,15 82,31" fill="#a99ff0" />
      <line x1="10" y1="54" x2="90" y2="54" stroke="#cfc9dc" strokeWidth="2" />
      <g opacity="0.35">
        <polygon points="14,70 38,82 38,58" fill="#6c5fc7" />
        <polygon points="42,78 62,88 62,68" fill="#8b7fd7" />
        <polygon points="66,85 82,93 82,77" fill="#a99ff0" />
      </g>
    </svg>
  )
}

/** Mark + wordmark lockup. */
export default function Logo({ size = 28, textClass = 'text-xl' }: {
  size?: number
  textClass?: string
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <LogoMark size={size} />
      <span className={`font-display ${textClass} leading-none`}>
        <span className="font-semibold text-ink">Mirror</span>
        <span className="font-bold text-brand">Fleet</span>
      </span>
    </span>
  )
}
