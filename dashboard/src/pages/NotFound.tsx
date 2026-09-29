import { Link } from 'react-router-dom'
import Card from '../components/Card'
import { usePageTitle } from '../hooks/usePageTitle'

/** The catch-all route: says what happened and offers the one way back. */
export default function NotFound() {
  usePageTitle('Page not found')
  return (
    <div className="mx-auto max-w-md pt-12">
      <Card>
        <h1 id="page-title" tabIndex={-1} className="page-title outline-none">That page is not here</h1>
        <p className="mt-2 text-sm text-ink-soft">
          The link may be old, or the address has a typo. Nothing was changed.
        </p>
        <Link to="/" className="mt-5 inline-block text-sm font-semibold text-brand hover:text-brand-deep hover:underline">
          Back to the desk
        </Link>
      </Card>
    </div>
  )
}
