import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, orgApi } from '../lib/api'
import { can, type Role } from '../lib/roles'
import { useOrg } from '../lib/org'
import type { Invite, Member } from '../lib/types'
import ConfirmDialog from '../components/ConfirmDialog'

const ASSIGNABLE: Role[] = ['viewer', 'trader', 'admin', 'owner']
const INVITABLE: Role[] = ['viewer', 'trader', 'admin']

export default function Members() {
  const { orgId, role, me, org, refreshMe } = useOrg()
  const navigate = useNavigate()
  const [members, setMembers] = useState<Member[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [inviteRole, setInviteRole] = useState<Role>('viewer')
  const [newInviteLink, setNewInviteLink] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [orgName, setOrgName] = useState(org.name)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setMembers(await orgApi<Member[]>(orgId, 'members'))
      if (can(role, 'control')) {
        setInvites(await orgApi<Invite[]>(orgId, 'invites'))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load members')
    }
  }, [orgId, role])

  useEffect(() => { refresh() }, [refresh])

  const changeRole = async (userId: number, newRole: string) => {
    setError(null)
    try {
      await orgApi(orgId, `members/${userId}`, {
        method: 'PATCH', body: JSON.stringify({ role: newRole }),
      })
      await refresh()
    } catch (err) {
      setError(err instanceof Error && err.message.includes('409')
        ? 'An organization must keep at least one owner.'
        : 'Could not change role')
    }
  }

  const removeMember = async (userId: number) => {
    setError(null)
    try {
      await orgApi(orgId, `members/${userId}`, { method: 'DELETE' })
      await refresh()
    } catch (err) {
      setError(err instanceof Error && err.message.includes('409')
        ? 'An organization must keep at least one owner.'
        : 'Could not remove member')
    }
  }

  const createInvite = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      const invite = await orgApi<{ token: string }>(orgId, 'invites', {
        method: 'POST', body: JSON.stringify({ role: inviteRole }),
      })
      setNewInviteLink(`${window.location.origin}/join/${invite.token}`)
      await refresh()
    } catch {
      setError('Could not create invite')
    }
  }

  return (
    <div className="space-y-8">
      <h2 className="text-xl font-semibold text-ink">Members</h2>
      {error && (
        <div className="rounded-md bg-loss-wash p-3 text-sm text-loss-deep">{error}</div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left desk-label border-b border-line">
            <th className="py-2">Name</th><th>Email</th><th>Role</th><th></th>
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id} className="border-b border-line">
              <td className="py-2 text-ink">{m.display_name}</td>
              <td className="text-ink-soft">{m.email}</td>
              <td>
                {can(role, 'manage_members') ? (
                  <select
                    aria-label={`Role for ${m.email}`}
                    value={m.role}
                    onChange={(e) => changeRole(m.user_id, e.target.value)}
                    className="border border-line-strong rounded bg-card px-2 py-1"
                  >
                    {ASSIGNABLE.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                ) : (
                  <span className="text-ink">{m.role}</span>
                )}
              </td>
              <td className="text-right">
                {(can(role, 'manage_members') || m.user_id === me.user.id) && (
                  <button
                    onClick={() => removeMember(m.user_id)}
                    className="text-xs text-loss hover:underline"
                  >
                    {m.user_id === me.user.id ? 'Leave' : 'Remove'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {can(role, 'control') && (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold text-ink">Invites</h3>
          <form onSubmit={createInvite} className="flex items-center gap-3">
            <select
              aria-label="Invite role"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as Role)}
              className="border border-line-strong rounded bg-card px-2 py-1 text-sm"
            >
              {INVITABLE.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <button
              type="submit"
              className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-white hover:bg-brand-deep"
            >
              Create invite link
            </button>
          </form>
          {newInviteLink && (
            <div className="flex items-center gap-2 bg-brand-wash rounded p-3 text-sm">
              <code className="text-ink break-all">{newInviteLink}</code>
              <button
                onClick={() => navigator.clipboard.writeText(newInviteLink)}
                className="text-xs font-medium text-brand hover:underline shrink-0"
              >
                Copy
              </button>
              <span className="desk-label shrink-0">shown once — copy it now</span>
            </div>
          )}
          <ul className="text-sm text-ink-soft space-y-1">
            {invites.map((inv) => (
              <li key={inv.id} className="flex items-center gap-3">
                <span>{inv.role}</span>
                <span>{inv.consumed ? 'used' : `expires ${new Date(inv.expires_at).toLocaleDateString()}`}</span>
                {!inv.consumed && (
                  <button
                    onClick={async () => {
                      await orgApi(orgId, `invites/${inv.id}`, { method: 'DELETE' })
                      await refresh()
                    }}
                    className="text-xs text-loss hover:underline"
                  >
                    Revoke
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {can(role, 'manage_members') && (
        <div className="space-y-4 border-t border-line pt-6">
          <h3 className="text-lg font-semibold text-ink">Organization</h3>
          <form
            onSubmit={async (e) => {
              e.preventDefault()
              await api(`/api/orgs/${orgId}`, { method: 'PATCH', body: JSON.stringify({ name: orgName }) })
              await refreshMe()
            }}
            className="flex items-center gap-3"
          >
            <input
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              aria-label="Organization name"
              className="border border-line-strong rounded bg-card px-2 py-1 text-sm"
            />
            <button type="submit" className="px-3 py-1.5 text-xs font-semibold rounded border border-brand text-brand hover:bg-brand-wash">
              Rename
            </button>
          </form>
          <button
            onClick={() => setDeleteOpen(true)}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-white transition-colors"
          >
            Delete organization
          </button>
          <ConfirmDialog
            open={deleteOpen}
            title="Delete this organization"
            confirmLabel="Delete organization"
            danger
            typeToConfirm="DELETE"
            busy={deleteBusy}
            onConfirm={async () => {
              setDeleteBusy(true)
              try {
                await api(`/api/orgs/${orgId}`, { method: 'DELETE' })
                navigate('/welcome')
              } finally {
                setDeleteBusy(false)
                setDeleteOpen(false)
              }
            }}
            onCancel={() => setDeleteOpen(false)}
          >
            <p>
              This removes the organization, its members, connected cTrader
              grants, and its copy history. Open positions at the broker are
              NOT closed — flatten first if you mean to exit the market. It
              cannot be undone.
            </p>
          </ConfirmDialog>
        </div>
      )}
    </div>
  )
}
