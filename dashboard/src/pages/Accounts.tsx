import Button from '../components/Button'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import { isMt5 } from '../lib/platform'
import AccountDialogs from './accounts/AccountDialogs'
import AccountDrawer from './accounts/AccountDrawer'
import AccountRow from './accounts/AccountRow'
import KeyRevealDialog from './accounts/KeyRevealDialog'
import { needsRegrant, useAccountsPage } from './accounts/useAccountsPage'

export default function Accounts() {
  const page = useAccountsPage()
  const { accounts, canControl, drawer } = page
  const drawerId = drawer.account?.ctid_trader_account_id
  // Re-grant is one header action: shown while any cTrader account exists,
  // enabled only when one of their grants has lapsed.
  const hasCtrader = accounts.some((a) => !isMt5(a))
  const regrantNeeded = accounts.some(needsRegrant)

  const actions = canControl ? (
    <div className="flex flex-col gap-2 md:flex-row md:items-center">
      {hasCtrader && (
        <Button
          variant="secondary"
          block
          className="md:w-auto"
          onClick={page.connectOAuth}
          disabled={!regrantNeeded}
        >
          Re-grant access
        </Button>
      )}
      <Button variant="secondary" block className="md:w-auto" onClick={page.askAddMt5}>
        Add MT5 account
      </Button>
      <Button block className="md:w-auto" onClick={page.connectOAuth}>
        Connect cTrader ID
      </Button>
    </div>
  ) : undefined

  return (
    <div className="space-y-8 max-w-6xl">
      <PageHeader
        title="Accounts"
        subtitle={
          'One cTrader ID grant covers every account under it; an MT5 account connects ' +
          'through the MirrorFleet EA in its own terminal. Roles, nicknames and cutoff ' +
          'dates apply per account and are edited from Details.'
        }
        actions={actions}
      />

      {/* Permanently mounted so screen readers reliably announce new notices. */}
      <div
        role="status"
        className={page.notice
          ? 'rounded-inset border border-line bg-brand-wash px-4 py-3 text-sm text-ink flex justify-between items-center gap-3'
          : 'sr-only'}
      >
        {page.notice && (
          <>
            <span>{page.notice}</span>
            <Button variant="ghost" tone="neutral" size="sm" onClick={page.dismissNotice}>
              Dismiss
            </Button>
          </>
        )}
      </div>
      {page.error && (
        <div role="alert" className="rounded-inset border border-loss/30 bg-loss-wash px-4 py-3 text-sm text-loss-deep">
          {page.error}
        </div>
      )}

      {page.isLoading ? (
        <Loading lines={4} label="Loading accounts" />
      ) : accounts.length === 0 ? (
        <Card>
          <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-ink-soft">
              No accounts yet: connect a cTrader ID to discover its trading accounts, or
              add an MT5 account and install the EA in its terminal.
            </p>
            {canControl && (
              <Button className="shrink-0" onClick={page.connectOAuth}>Connect cTrader ID</Button>
            )}
          </div>
        </Card>
      ) : (
        <Card inset>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2.5 font-semibold">Account</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Nickname</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Role</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Env</th>
                  <th className="desk-label px-3 py-2.5 font-semibold text-right">Equity</th>
                  <th className="desk-label px-3 py-2.5 font-semibold">Health</th>
                  <th className="desk-label px-5 py-2.5 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => {
                  const id = account.ctid_trader_account_id
                  return (
                    <AccountRow
                      key={id}
                      account={account}
                      equity={page.equity[String(id)]?.equity}
                      canControl={canControl}
                      pending={page.pending.has(id)}
                      flatten={page.flatten[id]}
                      onDetails={page.openDetails}
                      onFlatten={page.askFlatten}
                      onRegrant={page.connectOAuth}
                      onRotate={page.askRotate}
                      onDisconnect={page.askDisconnect}
                      onRemove={page.askRemove}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <AccountDialogs page={page} />
      <KeyRevealDialog
        reveal={page.keyReveal}
        onClose={page.closeKeyReveal}
        onCopyError={page.reportError}
      />
      <AccountDrawer
        account={drawer.account}
        details={drawer.details}
        detailsError={drawer.detailsError}
        aliases={drawer.aliases}
        aliasesError={drawer.aliasesError}
        canControl={canControl}
        canTrade={page.canTrade}
        pending={drawerId != null && page.pending.has(drawerId)}
        roleError={drawerId != null ? page.roleErrors[drawerId] : undefined}
        onClose={page.closeDetails}
        onSave={page.saveEdits}
        onRotate={page.askRotate}
        onSaveAlias={page.saveAlias}
      />
    </div>
  )
}
