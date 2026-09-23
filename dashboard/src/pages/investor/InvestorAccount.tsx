import { useOrg } from '../../lib/org'
import AccountSecurity from '../../components/AccountSecurity'

export default function InvestorAccount() {
  const { me, org } = useOrg()
  return (
    <div className="space-y-6 max-w-3xl">
      <header>
        <h1 className="page-title">Account</h1>
      </header>
      <section className="rounded-lg border border-line bg-card p-5 grid gap-3 md:grid-cols-2 text-sm">
        <div><div className="desk-label">Name</div><div className="text-ink">{me.user.display_name}</div></div>
        <div><div className="desk-label">Email</div><div className="text-ink">{me.user.email}</div></div>
        <div><div className="desk-label">Workspace</div><div className="text-ink">{org.name}</div></div>
        <div><div className="desk-label">Role</div><div className="text-ink">Investor</div></div>
      </section>
      <AccountSecurity />
    </div>
  )
}
