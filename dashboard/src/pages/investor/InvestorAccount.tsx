import { useOrg } from '../../lib/org'
import AccountSecurity from '../../components/AccountSecurity'
import Card from '../../components/Card'
import PageHeader from '../../components/PageHeader'

export default function InvestorAccount() {
  const { me, org } = useOrg()
  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader title="Account" />
      <Card title="Profile">
        <dl className="grid gap-3 md:grid-cols-2 text-sm">
          <div><dt className="desk-label">Name</dt><dd className="text-ink">{me.user.display_name}</dd></div>
          <div><dt className="desk-label">Email</dt><dd className="text-ink">{me.user.email}</dd></div>
          <div><dt className="desk-label">Workspace</dt><dd className="text-ink">{org.name}</dd></div>
          <div><dt className="desk-label">Role</dt><dd className="text-ink">Investor</dd></div>
        </dl>
      </Card>
      <AccountSecurity />
    </div>
  )
}
