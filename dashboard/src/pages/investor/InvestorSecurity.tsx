import { useOrg } from '../../lib/org'
import AccountSecurity from '../../components/AccountSecurity'
import PageHeader from '../../components/PageHeader'
import SignInHistory from '../../components/SignInHistory'

/** Password, MPIN, sessions, and where this account signed in from. */
export default function InvestorSecurity() {
  // Guards against rendering outside OrgProvider, like every other investor
  // page; the page itself has nothing org-scoped to read.
  useOrg()
  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Security" subtitle="Your password, your MPIN, your sessions and where you signed in from." />
      <AccountSecurity />
      <SignInHistory path="/api/me/sign-ins?limit=50" />
    </div>
  )
}
