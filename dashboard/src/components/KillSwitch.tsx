import { useState } from 'react'
import { api } from '../lib/api'
import type { Settings } from '../lib/types'

interface KillSwitchProps {
  settings: Settings
  onUpdate: (settings: Settings) => void
}

export default function KillSwitch({ settings, onUpdate }: KillSwitchProps) {
  const [isLoading, setIsLoading] = useState(false)

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
      await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ dry_run: newState }),
      })
      onUpdate({ ...settings, dry_run: newState })
    } catch (err) {
      console.error('Failed to update dry-run mode:', err)
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
      await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ copying_enabled: newState }),
      })
      onUpdate({ ...settings, copying_enabled: newState })
    } catch (err) {
      console.error('Failed to update copying status:', err)
    } finally {
      setIsLoading(false)
    }
  }

  const buttonText = settings.copying_enabled ? 'STOP COPYING' : 'RESUME COPYING'
  const buttonColor = settings.copying_enabled ? 'bg-loss hover:bg-loss-deep' : 'bg-profit hover:bg-brand-deep'

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {settings.dry_run && (
        <div className="px-3 py-1 bg-warn-wash text-warn text-sm font-semibold rounded-full">
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
            : 'bg-card hover:bg-paper text-ink border-line-strong'
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
