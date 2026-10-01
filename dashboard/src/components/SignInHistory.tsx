import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { errorText, formatWhen } from '../lib/format'
import { OUTCOME_LABELS, deviceLabel } from '../lib/identity'
import Badge from './Badge'
import Banner from './Banner'
import Card from './Card'
import Loading from './Loading'
import type { SignIn } from '../lib/types'

/**
 * Where this account signed in from, newest first. Desk members read
 * /api/me/sign-ins, investors their portal route; the caller passes the
 * path. Read once on mount: it is a record, not a live feed.
 */
export default function SignInHistory({ path }: { path: string }) {
  const [rows, setRows] = useState<SignIn[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<SignIn[]>(path).then(
      (r) => { if (live) setRows(r) },
      (err) => {
        if (!live) return
        setError(errorText(err, 'Could not load your sign-ins'))
        setRows([])
      },
    )
    return () => { live = false }
  }, [path])

  return (
    <Card title="Sign-in history" inset>
      {error && <div className="p-4"><Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner></div>}
      {rows == null ? (
        <div className="p-4"><Loading lines={3} label="Loading sign-ins" /></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-4 py-2 font-semibold">Time</th>
                <th className="desk-label px-4 py-2 font-semibold">IP</th>
                <th className="desk-label px-4 py-2 font-semibold">Device</th>
                <th className="desk-label px-4 py-2 font-semibold">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={4} className="text-center py-8 text-ink-faint">No sign-ins recorded yet</td></tr>
              )}
              {rows.map((s) => (
                <tr key={s.id} className="border-b border-line last:border-0">
                  <td data-label="Time" className="num px-4 py-2.5">{formatWhen(s.created_at)}</td>
                  <td data-label="IP" className="num px-4 py-2.5">{s.ip}</td>
                  <td data-label="Device" className="px-4 py-2.5">{deviceLabel(s.user_agent)}</td>
                  <td data-label="Outcome" className="px-4 py-2.5">
                    <Badge tone={s.outcome === 'failed' ? 'loss' : 'neutral'}>{OUTCOME_LABELS[s.outcome]}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
