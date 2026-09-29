import type { Account, Settings } from '../../lib/types'
import Card from '../../components/Card'
import Button from '../../components/Button'
import StatusDot from '../../components/StatusDot'

export interface SetupStep {
  key: 'master' | 'followers' | 'dry-run' | 'live'
  title: string
  detail: string
  done: boolean
  to?: string
  linkLabel?: string
}

/** Every "done" is read from state the page already loads: the account list
 *  and the org's copier settings. Nothing is remembered client-side. */
export function setupSteps(orgId: number, accounts: Account[], settings: Settings | null): SetupStep[] {
  const hasMaster = accounts.some((a) => a.role === 'master')
  const hasFollower = accounts.some((a) => a.role === 'slave')
  const dryRunOn = settings?.dry_run === true
  const live = hasMaster && hasFollower &&
    settings != null && settings.copying_enabled && !settings.dry_run
  const accountsPath = `/org/${orgId}/accounts`
  return [
    {
      key: 'master',
      title: 'Connect the master',
      detail: 'The account whose trades every follower copies. Connect it with cTrader ID or add an MT5 terminal.',
      done: hasMaster,
      to: accountsPath,
      linkLabel: 'Open Accounts',
    },
    {
      key: 'followers',
      title: 'Add followers',
      detail: 'Each follower copies the master at its own multiplier.',
      done: hasFollower,
      to: accountsPath,
      linkLabel: 'Open Accounts',
    },
    {
      key: 'dry-run',
      title: 'Run a dry run',
      detail: 'Turn dry-run on from the desk strip and watch copies being simulated before real orders go out.',
      done: dryRunOn || live,
      to: `/org/${orgId}/automation`,
      linkLabel: 'Open Automation',
    },
    {
      key: 'live',
      title: 'Go live',
      detail: 'When the dry run looks right, turn dry-run off from the desk strip. Copying then sends real orders.',
      done: live,
    },
  ]
}

export default function SetupChecklist({ orgId, accounts, settings }: {
  orgId: number
  accounts: Account[]
  settings: Settings | null
}) {
  const steps = setupSteps(orgId, accounts, settings)
  return (
    <div data-testid="setup-checklist">
      <Card as="section" title="Set up copying">
        <p className="text-sm text-ink-soft">Four steps from an empty desk to live copying.</p>
        <ol className="mt-4 space-y-3">
          {steps.map((step) => (
            <li key={step.key} className="inset flex flex-wrap items-start gap-3 p-4">
              <span className="mt-1"><StatusDot tone={step.done ? 'ok' : 'warn'} /></span>
              <div className="min-w-0 flex-1 basis-56">
                <p className="font-semibold text-ink">{step.title}</p>
                <p className="mt-0.5 text-sm text-ink-soft">{step.detail}</p>
              </div>
              <span className="text-xs font-semibold text-ink-soft">{step.done ? 'Done' : 'To do'}</span>
              {step.to && step.linkLabel && (
                <Button to={step.to} variant="secondary" size="sm">{step.linkLabel}</Button>
              )}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  )
}
