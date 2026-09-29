import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import { accountName } from '../../lib/platform'
import type { AccountsPage } from './useAccountsPage'

/** The page's confirm dialogs. At most one is open (`page.dialog`); promote
 *  and rotate can stack above the Details drawer. */
export default function AccountDialogs({ page }: { page: AccountsPage }) {
  const { dialog, busy } = page
  const target = dialog && dialog.kind !== 'add-mt5' ? dialog.account : null
  const mt5Nickname = dialog?.kind === 'add-mt5' ? dialog.nickname : ''

  return (
    <>
      <ConfirmDialog
        open={dialog?.kind === 'disconnect'}
        title={`Disconnect account ${target?.trader_login ?? ''}`}
        confirmLabel="Disconnect grant"
        danger
        busy={busy}
        onConfirm={page.confirmDisconnect}
        onCancel={page.cancelDialog}
      >
        <p>
          This removes the cTrader ID grant behind this account — and with it{' '}
          <strong>every account under that same grant</strong>. Open positions
          are not touched; the copier just stops seeing these accounts.
        </p>
        <p>You can reconnect any time with Connect cTrader ID.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog?.kind === 'remove'}
        title={`Remove account ${target?.nickname || target?.mt5?.login || ''}`}
        confirmLabel="Remove permanently"
        danger
        busy={busy}
        onConfirm={page.confirmRemove}
        onCancel={page.cancelDialog}
      >
        <p>
          This <strong>permanently deletes</strong> this MT5 account from
          MirrorFleet — its history disappears from every page, and the
          terminal&apos;s key stops working on its next poll. Open positions
          on the broker are not touched.
        </p>
        <p>Adding the login again later creates a fresh account.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog?.kind === 'promote'}
        title={`Make account ${target?.trader_login ?? ''} the master`}
        confirmLabel="Make it the master"
        onConfirm={() => page.resolvePromote(true)}
        onCancel={page.cancelDialog}
      >
        <p>
          Every other account in this workspace becomes a follower — including
          the current master and any Ignored accounts — and every enabled
          follower then copies account {target?.trader_login}'s trades.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog?.kind === 'flatten'}
        title={`Flatten account ${target?.trader_login ?? ''}`}
        confirmLabel="Close everything here"
        danger
        onConfirm={page.confirmFlatten}
        onCancel={page.cancelDialog}
      >
        <p>
          Every open position in this account is closed at market and every
          working order cancelled. Other accounts are untouched
          {target?.is_live ? ' — and this is a live account' : ''}.
        </p>
      </ConfirmDialog>

      {/* The nickname is the only identifier an MT5 row has until its
          terminal connects, so it is required. */}
      <ConfirmDialog
        open={dialog?.kind === 'add-mt5'}
        title="Add an MT5 account"
        confirmLabel="Create account"
        busy={busy}
        disabled={mt5Nickname.trim() === ''}
        onConfirm={page.confirmAddMt5}
        onCancel={page.cancelDialog}
      >
        <p>
          The account starts as a disabled follower. You get a one-time key to
          paste into the MirrorFleet EA running in the account's own MT5
          terminal; the login, broker and symbols arrive when the EA first
          connects.
        </p>
        <div>
          <label className="desk-label block mb-1" htmlFor="mt5-nickname">Nickname</label>
          <Input
            id="mt5-nickname"
            type="text"
            value={mt5Nickname}
            onChange={(e) => page.setMt5Nickname(e.target.value)}
            autoComplete="off"
            placeholder="e.g. VPS desk"
          />
        </div>
      </ConfirmDialog>

      {/* The old key dies the moment the new one exists. */}
      <ConfirmDialog
        open={dialog?.kind === 'rotate'}
        title={`Rotate the key for ${target ? accountName(target) : ''}`}
        confirmLabel="Rotate key"
        danger
        busy={busy}
        onConfirm={page.confirmRotate}
        onCancel={page.cancelDialog}
      >
        <p>
          The current key stops working the moment the new one exists, so the
          running EA disconnects until you paste the new key into its{' '}
          <span className="num">InpKey</span> input and restart it. Positions,
          symbol mapping and history are untouched.
        </p>
      </ConfirmDialog>
    </>
  )
}
