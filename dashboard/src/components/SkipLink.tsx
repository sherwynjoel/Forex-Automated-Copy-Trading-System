/** First in the tab order; visible only when focused; jumps past the rail. */
export default function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 glass rounded-control border px-4 py-2 text-sm font-semibold text-brand shadow-float"
    >
      Skip to content
    </a>
  )
}
