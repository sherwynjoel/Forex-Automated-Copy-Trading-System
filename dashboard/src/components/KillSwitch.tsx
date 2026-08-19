import { useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import type { Settings } from '../lib/types'

interface KillSwitchProps {
  settings: Settings
  onUpdate: (settings: Settings) => void
}

export default function KillSwitch({ settings, onUpdate }: KillSwitchProps) {
  const { orgId, role } = useOrg()
  const [isLoading, setIsLoading] = useState(false)
  // A failed toggle must never be silent: mid-incident, "the button did
  // nothing" has to read as "the copier did NOT stop".
  const [error, setError] = useState<string | null>(null)

  // N1: dry-run had a badge but no control anywhere in the dashboard, so
  // README §4's Stage 1 ("turn dry-run mode on") was not executable from the
  // UI at all. Same shape as the kill switch below: PUT /api/settings, then
  // hand the new settings back to the parent.
  const handleToggleDryRun = async () => {
    const newState = !settings.dry_run
    // Confirm only in the direction that starts putting REAL orders on the
    // wire. Turning dry-run ON is always safe, so it must not be gated
    // behind a dialog an operator reaching for the safety switch has to
    // stop and read.
    if (
      !newState &&
      !window.confirm(
        'Turn dry-run OFF? Copied trades will be sent to the broker for real from now on.'
      )
    ) {
      return
    }

    try {
      setIsLoading(true)
      setError(null)
      await orgApi(orgId, 'settings', {
        method: 'PUT',
        body: JSON.stringify({ dry_run: newState }),
      })
      onUpdate({ ...settings, dry_run: newState })
    } catch (err) {
      setError(
        `Dry-run is still ${settings.dry_run ? 'ON' : 'OFF'} — the change failed: ` +
        `${err instanceof Error ? err.message : 'the copier did not respond'}`)
    } finally {
      setIsLoading(false)
    }
  }

  const handleToggleCopying = async () => {
    const newState = !settings.copying_enabled
    const message = newState
      ? 'Are you sure you want to resume copying?'
      : 'Are you sure you want to stop copying? This will pause all active trades.'

    if (!window.confirm(message)) {
      return
    }

    try {
      setIsLoading(true)
      setError(null)
      await orgApi(orgId, 'settings', {
        method: 'PUT',
        body: JSON.stringify({ copying_enabled: newState }),
      })
      onUpdate({ ...settings, copying_enabled: newState })
    } catch (err) {
      setError(
        `Copying is still ${settings.copying_enabled ? 'RUNNING' : 'stopped'} — the change failed: ` +
        `${err instanceof Error ? err.message : 'the copier did not respond'}`)
    } finally {
      setIsLoading(false)
    }
  }

  const buttonText = settings.copying_enabled ? 'STOP COPYING' : 'RESUME COPYING'
  const buttonColor = settings.copying_enabled ? 'bg-loss hover:bg-loss-deep' : 'bg-profit hover:bg-profit-deep'

  // Kill switches are a control-level action; viewers/traders never see them.
  if (!can(role, 'control')) return null

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {error && (
        <p role="alert" className="w-full text-sm font-medium text-loss-deep bg-loss-wash border border-loss/30 rounded px-3 py-2">
          {error}
        </p>
      )}
      {settings.dry_run && (
        <div className="px-3 py-1 bg-warn-wash text-warn-deep text-sm font-semibold rounded-full">
          DRY RUN
        </div>
      )}
      <button
        data-testid="dry-run-toggle"
        onClick={handleToggleDryRun}
        disabled={isLoading}
        className={`px-4 py-2 text-sm font-semibold rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
          settings.dry_run
            ? 'bg-warn text-white border-warn hover:opacity-90'
            : 'bg-card hover:bg-brand-wash text-ink border-line-strong'
        }`}
      >
        {settings.dry_run ? 'Turn dry-run off' : 'Turn dry-run on'}
      </button>
      <button
        onClick={handleToggleCopying}
        disabled={isLoading}
        className={`px-5 py-2 text-sm text-white font-bold rounded ${buttonColor} disabled:opacity-50 disabled:cursor-not-allowed transition-colors`}
      >
        {buttonText}
      </button>
    </div>
  )
}
