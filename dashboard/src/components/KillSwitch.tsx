import { useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import type { Settings } from '../lib/types'
import Button from './Button'
import Badge from './Badge'
import ConfirmDialog from './ConfirmDialog'

interface KillSwitchProps {
  settings: Settings
  onUpdate: (settings: Settings) => void
}

type PendingAction = 'dry-run-off' | 'stop' | 'resume' | null

export default function KillSwitch({ settings, onUpdate }: KillSwitchProps) {
  const { orgId, role } = useOrg()
  const [busy, setBusy] = useState(false)
  // A failed toggle must never be silent: mid-incident, "the button did
  // nothing" has to read as "the copier did NOT stop".
  const [error, setError] = useState<string | null>(null)
  // Names the confirmation in flight, so the dialog it opens can carry
  // copy that states the resulting state rather than a generic "sure?".
  const [pending, setPending] = useState<PendingAction>(null)

  const putDryRun = async (newState: boolean) => {
    try {
      setBusy(true)
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
      setBusy(false)
    }
  }

  // N1: dry-run had a badge but no control anywhere in the dashboard, so
  // README §4's Stage 1 ("turn dry-run mode on") was not executable from the
  // UI at all. Same shape as the kill switch below: PUT /api/settings, then
  // hand the new settings back to the parent.
  const handleToggleDryRun = () => {
    // Confirm only in the direction that starts putting REAL orders on the
    // wire. Turning dry-run ON is always safe, so it must not be gated
    // behind a dialog an operator reaching for the safety switch has to
    // stop and read.
    if (settings.dry_run) {
      setPending('dry-run-off')
      return
    }
    putDryRun(true)
  }

  const putCopying = async (newState: boolean) => {
    try {
      setBusy(true)
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
      setBusy(false)
    }
  }

  const handleToggleCopying = () => {
    setPending(settings.copying_enabled ? 'stop' : 'resume')
  }

  const confirmPending = async () => {
    if (pending === 'dry-run-off') await putDryRun(false)
    else if (pending === 'stop') await putCopying(false)
    else if (pending === 'resume') await putCopying(true)
    setPending(null)
  }

  const buttonText = settings.copying_enabled ? 'STOP COPYING' : 'RESUME COPYING'

  // Kill switches are a control-level action; viewers never see them.
  if (!can(role, 'control')) return null

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {error && (
        <p role="alert" className="w-full text-sm font-medium text-loss-deep bg-loss-wash border border-loss/30 rounded px-3 py-2">
          {error}
        </p>
      )}
      {settings.dry_run && (
        <Badge tone="warn" pill>DRY RUN</Badge>
      )}
      <Button
        data-testid="dry-run-toggle"
        onClick={handleToggleDryRun}
        disabled={busy}
        variant={settings.dry_run ? 'primary' : 'secondary'}
        tone={settings.dry_run ? 'warn' : undefined}
      >
        {settings.dry_run ? 'Turn dry-run off' : 'Turn dry-run on'}
      </Button>
      <Button
        onClick={handleToggleCopying}
        disabled={busy}
        tone={settings.copying_enabled ? 'loss' : 'profit'}
      >
        {buttonText}
      </Button>

      <ConfirmDialog
        open={pending === 'dry-run-off'}
        title="Turn dry-run off?"
        confirmLabel="Turn dry-run off"
        danger
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        <p>Copied trades go to the broker for real from now on.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={pending === 'stop'}
        title="Stop copying?"
        confirmLabel="Stop copying"
        danger
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        <p>Every follower stops receiving new copies until you resume. Open positions stay open at the broker.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={pending === 'resume'}
        title="Resume copying?"
        confirmLabel="Resume copying"
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        <p>Followers receive every new master fill again from now on.</p>
      </ConfirmDialog>
    </div>
  )
}
