import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, orgApi } from '../lib/api'
import { can, type Role } from '../lib/roles'
import { useOrg } from '../lib/org'
import Banner from '../components/Banner'
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
  const [linkCopied, setLinkCopied] = useState(false)
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
    const isSelf = userId === me.user.id
    try {
      await orgApi(orgId, `members/${userId}`, { method: 'DELETE' })
      if (isSelf) {
        // A successful self-leave means we're no longer a member of this
        // org — refetching would 403 and surface a false failure. Leave
        // the org instead of refreshing its now-inaccessible member list.
        navigate('/welcome')
      } else {
        await refresh()
      }
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
      setLinkCopied(false); setNewInviteLink(`${window.location.origin}/join/${invite.token}`)
      await refresh()
    } catch {
      setError('Could not create invite')
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="page-title">Members</h1>
      {error && (
        <Banner kind="error">{error}</Banner>
      )}
      <table className="stack-table w-full text-sm">
        <thead>
          <tr className="text-left desk-label border-b border-line">
            <th className="py-2">Name</th><th>Email</th><th>Role</th><th></th>
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id} className="border-b border-line">
              <td data-label="Name" className="py-2 text-ink">{m.display_name}</td>
              <td data-label="Email" className="text-ink-soft">{m.email}</td>
              <td data-label="Role">
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
              className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep"
            >
              Create invite link
            </button>
          </form>
          {newInviteLink && (
            <div className="flex items-center gap-2 bg-brand-wash rounded p-3 text-sm">
              <code className="text-ink break-all">{newInviteLink}</code>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(newInviteLink)
                  setLinkCopied(true)
                }}
                className="text-xs font-medium text-brand-deep hover:underline shrink-0"
              >
                {linkCopied ? 'Copied ✓' : 'Copy'}
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

      <AccountSecurity />

      {can(role, 'manage_members') && (
        <div className="space-y-4 border-t border-line pt-6">
          <h3 className="text-lg font-semibold text-ink">Organization</h3>
          <form
            onSubmit={async (e) => {
              e.preventDefault()
              setError(null)
              try {
                await api(`/api/orgs/${orgId}`, { method: 'PATCH', body: JSON.stringify({ name: orgName }) })
                await refreshMe()
              } catch {
                setError('Could not rename organization')
              }
            }}
            className="flex items-center gap-3"
          >
            <input
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              aria-label="Organization name"
              className="border border-line-strong rounded bg-card px-2 py-1 text-sm"
            />
            <button type="submit" className="px-3 py-1.5 text-xs font-semibold rounded border border-brand text-brand hover:bg-brand-wash hover:text-brand-deep">
              Rename
            </button>
          </form>
          <button
            onClick={() => setDeleteOpen(true)}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors"
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
              } catch {
                setError('Could not delete organization')
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


/**
 * Your own login, not the org's: rotate the password and cut every other
 * session loose. Both live here rather than behind a role check -- every
 * member owns their own credentials.
 */
function AccountSecurity() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const navigate = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setNotice(null)
    setProblem(null)
    setBusy(true)
    try {
      await api('/api/me/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      setCurrent('')
      setNext('')
      setNotice('Password changed. Any other device signed in as you has been signed out.')
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'Could not change the password')
    } finally {
      setBusy(false)
    }
  }

  const signOutEverywhere = async () => {
    setProblem(null)
    setBusy(true)
    try {
      await api('/api/me/logout-all', { method: 'POST' })
      navigate('/login')
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'Could not sign out everywhere')
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4 border-t border-line pt-6">
      <h3 className="text-lg font-semibold text-ink">Your login</h3>
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {problem && <Banner kind="error" onDismiss={() => setProblem(null)}>{problem}</Banner>}

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="current-password" className="desk-label block mb-1">
            Current password
          </label>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            className="rounded border border-line-strong bg-card px-3 py-2 text-sm text-ink"
          />
        </div>
        <div>
          <label htmlFor="new-password" className="desk-label block mb-1">
            New password
          </label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            className="rounded border border-line-strong bg-card px-3 py-2 text-sm text-ink"
          />
        </div>
        <button
          type="submit"
          disabled={busy || !current || !next}
          className="min-h-11 md:min-h-0 px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep transition-colors disabled:opacity-50"
        >
          Change password
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={signOutEverywhere}
          disabled={busy}
          className="min-h-11 md:min-h-0 px-4 py-2 text-sm font-semibold rounded border border-line-strong text-ink hover:bg-line transition-colors disabled:opacity-50"
        >
          Sign out everywhere
        </button>
        <p className="text-sm text-ink-soft">
          Ends every session for your account, on this device and any other.
          Use it if you think a login was stolen.
        </p>
      </div>
    </div>
  )
}
