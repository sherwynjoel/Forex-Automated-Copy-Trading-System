import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Select from '../../components/Select'
import { formatWhen } from '../../lib/format'
import { isMt5 } from '../../lib/platform'
import type { Account, AccountDetails, SymbolAliases } from '../../lib/types'
import AliasEditor from './AliasEditor'
import { accountRoleLabel, mt5Subtitle } from './AccountRow'
import {
  draftOf, type AccountDraft, type EditField, type SaveFailure, type SaveOutcome,
} from './useAccountsPage'

const FIELD_LABEL: Record<EditField, string> = {
  nickname: 'Nickname',
  cutoff_date: 'Cutoff date',
  enabled: 'Copying enabled',
  role: 'Role',
}

function formatDate(iso: string | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function formatTimestamp(ms: number | null | undefined): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-soft">{label}</dt>
      <dd className={`text-ink text-right ${mono ? 'num' : ''}`}>{value}</dd>
    </div>
  )
}

/** Nickname, role, copying and cutoff, sent only on Save changes. Cancel
 *  puts the form back to what the server has. A field the server refused
 *  keeps what was typed and is named in the form's own alert. */
function EditSection({ account, pending, onSave }: {
  account: Account
  pending: boolean
  onSave: (account: Account, draft: AccountDraft) => Promise<SaveOutcome>
}) {
  const id = useId()
  const saved = draftOf(account)
  const [draft, setDraft] = useState<AccountDraft>(saved)

  const [failures, setFailures] = useState<SaveFailure[]>([])
  // Typed values of fields whose save failed. They survive the reload that
  // follows a partly successful save, so a refused change never snaps back
  // silently to the server's value.
  const keptRef = useRef<Partial<AccountDraft>>({})

  // Follow the SERVER's values (after a save, or when a promotion elsewhere
  // demoted this account), never a mere refetch: a window-focus reload
  // must not wipe what is being typed.
  useEffect(() => {
    setDraft({ ...draftOf(account), ...keptRef.current })
  }, [account.nickname, account.role, account.enabled, account.cutoff_date])

  const dirty =
    draft.nickname !== saved.nickname ||
    draft.role !== saved.role ||
    draft.enabled !== saved.enabled ||
    draft.cutoff_date !== saved.cutoff_date

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!dirty || pending) return
    const submitted = draft
    keptRef.current = {}
    setFailures([])
    const outcome = await onSave(account, submitted)
    const kept: Partial<AccountDraft> = {}
    for (const { field } of outcome.failed) Object.assign(kept, { [field]: submitted[field] })
    keptRef.current = kept
    setFailures(outcome.failed)
    setDraft((prev) => ({
      ...prev,
      ...kept,
      ...(outcome.promoteCancelled ? { role: account.role } : {}),
    }))
  }

  const cancel = () => {
    keptRef.current = {}
    setFailures([])
    setDraft(saved)
  }

  return (
    <section>
      <h3 id={`${id}-heading`} className="desk-label mb-2">Edit</h3>
      <form aria-labelledby={`${id}-heading`} onSubmit={submit} className="space-y-3">
        {failures.length > 0 && (
          <Banner kind="error">
            <p className="font-semibold">Some changes were not saved:</p>
            <ul className="mt-1 list-disc pl-5">
              {failures.map((f) => (
                <li key={f.field}>{FIELD_LABEL[f.field]}: {f.message}</li>
              ))}
            </ul>
          </Banner>
        )}
        <div>
          <label htmlFor={`${id}-nickname`} className="desk-label block mb-1">Nickname</label>
          <Input
            id={`${id}-nickname`}
            type="text"
            placeholder="Add a name"
            autoComplete="off"
            value={draft.nickname}
            onChange={(e) => setDraft((prev) => ({ ...prev, nickname: e.target.value }))}
            disabled={pending}
          />
        </div>
        <div>
          <label htmlFor={`${id}-role`} className="desk-label block mb-1">Role</label>
          <Select
            id={`${id}-role`}
            block
            value={draft.role}
            onChange={(e) => setDraft((prev) => ({ ...prev, role: e.target.value }))}
            disabled={pending}
          >
            <option value="master">Master</option>
            <option value="slave">Follower</option>
            <option value="ignored">Ignored</option>
          </Select>
        </div>
        <label className="flex items-center justify-between gap-4 cursor-pointer has-[:disabled]:cursor-default">
          <span className="desk-label">Copying enabled</span>
          <span className="relative inline-flex items-center">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft((prev) => ({ ...prev, enabled: e.target.checked }))}
              disabled={pending}
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className="h-5 w-9 rounded-full bg-ink-faint transition-colors peer-checked:bg-brand peer-disabled:opacity-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand"
            />
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-card transition-transform peer-checked:translate-x-4"
            />
          </span>
        </label>
        <div>
          <label htmlFor={`${id}-cutoff`} className="desk-label block mb-1">Cutoff date</label>
          <Input
            id={`${id}-cutoff`}
            type="date"
            num
            value={draft.cutoff_date}
            onChange={(e) => setDraft((prev) => ({ ...prev, cutoff_date: e.target.value }))}
            disabled={pending}
          />
          <p className="mt-1 text-xs text-ink-faint">
            Clear the date to remove the cutoff and its reminder.
          </p>
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={cancel}
            disabled={!dirty || pending}
          >
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!dirty || pending}>
            Save changes
          </Button>
        </div>
      </form>
    </section>
  )
}

/** Below control the same four settings, read-only. */
function SettingsSummary({ account }: { account: Account }) {
  return (
    <section>
      <h3 className="desk-label mb-2">Settings</h3>
      <dl className="space-y-1.5 text-sm">
        <DetailRow label="Nickname" value={account.nickname || '—'} />
        <DetailRow label="Role" value={accountRoleLabel(account.role)} />
        <DetailRow label="Copying enabled" value={account.enabled ? 'Yes' : 'No'} />
        <DetailRow label="Cutoff" value={account.cutoff_date || '—'} mono />
      </dl>
    </section>
  )
}

export default function AccountDrawer({
  account, details, detailsError, aliases, aliasesError, canControl, canTrade, pending,
  onClose, onSave, onRotate, onSaveAlias,
}: {
  account: Account | null
  details: AccountDetails | null
  detailsError: string | null
  aliases: SymbolAliases | null
  aliasesError: string | null
  canControl: boolean
  canTrade: boolean
  pending: boolean
  onClose: () => void
  onSave: (account: Account, draft: AccountDraft) => Promise<SaveOutcome>
  onRotate: (account: Account) => void
  onSaveAlias: (accountId: number, canonical: string, brokerName: string) => Promise<void>
}) {
  return (
    <Drawer
      open={account != null}
      title={account ? (account.nickname || `Account ${account.trader_login}`) : ''}
      onClose={onClose}
      busy={pending}
    >
      {account && (
        <>
          <p className="num text-sm text-ink-soft -mt-2 mb-4">
            {isMt5(account)
              ? mt5Subtitle(account.mt5)
              : `${account.trader_login} · cTID ${account.ctid_trader_account_id}`}
          </p>

          <div className="space-y-6">
            {canControl ? (
              <EditSection
                key={account.ctid_trader_account_id}
                account={account}
                pending={pending}
                onSave={onSave}
              />
            ) : (
              <SettingsSummary account={account} />
            )}

            {detailsError ? (
              <p className="text-sm text-loss-deep">{detailsError}</p>
            ) : !details ? (
              <Loading lines={4} label="Fetching from the broker" />
            ) : (
              <>
                <section>
                  <h3 className="desk-label mb-2">Broker profile</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Broker" value={details.broker_name ?? '—'} />
                    <DetailRow
                      label="Balance"
                      value={details.balance != null
                        ? `${details.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${details.deposit_currency ?? ''}`
                        : '—'}
                      mono
                    />
                    <DetailRow label="Currency" value={details.deposit_currency ?? '—'} />
                    <DetailRow
                      label="Leverage"
                      value={details.leverage != null ? `1:${details.leverage}` : '—'}
                      mono
                    />
                    <DetailRow
                      label="Max leverage"
                      value={details.max_leverage != null ? `1:${details.max_leverage}` : '—'}
                      mono
                    />
                    <DetailRow label="Account type" value={details.account_type ?? '—'} />
                    <DetailRow label="Access" value={details.access_rights ?? '—'} />
                    <DetailRow
                      label="Swap-free"
                      value={details.swap_free == null ? '—' : details.swap_free ? 'Yes' : 'No'}
                    />
                    <DetailRow label="Registered" value={formatTimestamp(details.registration_timestamp)} />
                  </dl>
                  <p className="mt-3 text-xs text-ink-faint">
                    {isMt5(account)
                      ? 'The terminal reports only what MT5 exposes to an Expert Advisor — set a nickname for anything more.'
                      : 'The account holder\'s name and email are not exposed by the cTrader Open API — set a nickname instead.'}
                  </p>
                </section>

                <section>
                  <h3 className="desk-label mb-2">Copy settings</h3>
                  <dl className="space-y-1.5 text-sm">
                    <DetailRow label="Role" value={accountRoleLabel(details.role ?? account.role)} />
                    <DetailRow label="Enabled" value={(details.enabled ?? account.enabled) ? 'Yes' : 'No'} />
                    <DetailRow label="Status" value={details.status ?? account.status} />
                  </dl>
                </section>

                {isMt5(account) ? (
                  <section>
                    <h3 className="desk-label mb-2">Terminal</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Broker" value={account.mt5?.broker ?? '—'} />
                      <DetailRow label="Server" value={account.mt5?.server ?? '—'} />
                      <DetailRow label="Currency" value={account.mt5?.currency ?? '—'} />
                      <DetailRow
                        label="Hedging"
                        value={account.mt5?.hedging == null
                          ? '—'
                          : account.mt5?.hedging ? 'Yes' : 'No — netting accounts are not supported'}
                      />
                      <DetailRow label="Trade mode" value={account.mt5?.trade_mode ?? '—'} />
                      <DetailRow label="EA version" value={account.mt5?.ea_version ?? '—'} mono />
                      <DetailRow label="Last seen" value={formatWhen(account.mt5?.last_seen_at)} mono />
                    </dl>
                    {/* Rotate key sits beside the terminal it cuts off; the
                        row menu opens the same dialog. */}
                    {canControl && (
                      <Button variant="secondary" size="sm" className="mt-3" onClick={() => onRotate(account)}>
                        Rotate key
                      </Button>
                    )}
                  </section>
                ) : (
                  <section>
                    <h3 className="desk-label mb-2">OAuth grant</h3>
                    <dl className="space-y-1.5 text-sm">
                      <DetailRow label="Granted" value={formatDate(details.connection?.granted_at)} />
                      <DetailRow label="Token expires" value={formatDate(details.connection?.expires_at)} />
                      <DetailRow label="Grant status" value={details.connection?.status ?? '—'} />
                      <DetailRow label="Scope" value={details.connection?.scope ?? '—'} />
                    </dl>
                  </section>
                )}

                {isMt5(account) && (
                  <AliasEditor
                    key={account.ctid_trader_account_id}
                    aliases={aliases}
                    error={aliasesError}
                    canView={canTrade}
                    canEdit={canControl}
                    onSave={(canonical, brokerName) =>
                      onSaveAlias(account.ctid_trader_account_id, canonical, brokerName)}
                  />
                )}

                <section>
                  <h3 className="desk-label mb-2">Open positions ({details.open_positions.length})</h3>
                  {details.open_positions.length === 0 ? (
                    <p className="text-sm text-ink-faint">None.</p>
                  ) : (
                    <ul className="space-y-1 text-sm">
                      {details.open_positions.map((pos) => (
                        <li key={pos.position_id} className="flex justify-between">
                          <span className="num">{pos.symbol ?? pos.symbol_id}</span>
                          <span className={pos.side === 'BUY' ? 'text-profit' : 'text-loss'}>
                            {pos.side} <span className="num">{pos.volume_lots ?? pos.volume}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </>
            )}
          </div>
        </>
      )}
    </Drawer>
  )
}
