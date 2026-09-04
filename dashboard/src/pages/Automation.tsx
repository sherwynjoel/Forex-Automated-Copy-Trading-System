import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { can } from '../lib/roles'
import { useOrg } from '../lib/org'
import Banner from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import type { WebhookReceipt, WebhookSettings, WebhookSecret } from '../lib/types'

const POLL_MS = 5000

/**
 * TradingView alerts placing the master's orders.
 *
 * The one-time secret is the sensitive object on this page. It lives in
 * React state only while the reveal dialog is open and is cleared the
 * moment it closes -- never in a toast, never in an error string, never
 * re-fetched. After that the server holds only its hash and cannot show it
 * again; the operator generates a new one instead.
 */
export default function Automation() {
  const { orgId, role } = useOrg()
  const [settings, setSettings] = useState<WebhookSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reveal, setReveal] = useState<WebhookSecret | null>(null)
  const [copied, setCopied] = useState<'url' | 'template' | null>(null)
  const [confirmRotate, setConfirmRotate] = useState(false)
  const [draft, setDraft] = useState({ max_lots: '', max_per_minute: '', max_open_positions: '' })

  const refresh = useCallback(async () => {
    try {
      const s = await orgApi<WebhookSettings>(orgId, 'webhook')
      setSettings(s)
      setDraft((d) => ({
        max_lots: d.max_lots === '' ? String(s.max_lots) : d.max_lots,
        max_per_minute: d.max_per_minute === '' ? String(s.max_per_minute) : d.max_per_minute,
        max_open_positions: d.max_open_positions === '' ? String(s.max_open_positions) : d.max_open_positions,
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load automation settings')
    }
  }, [orgId])

  // Alerts arrive whether or not anyone is looking; the recent list must
  // catch up on its own, the way Positions and Trade do.
  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const control = can(role, 'control')

  const put = async (body: Record<string, unknown>, done: string) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi(orgId, 'webhook', { method: 'PUT', body: JSON.stringify(body) })
      setNotice(done)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The change failed')
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async () => {
    if (!settings) return
    const next = !settings.enabled
    if (next && !window.confirm(
      'Turn automation ON? From now on a valid TradingView alert will place a real order on the master, and every follower will copy it.')) {
      return
    }
    await put({ enabled: next }, next ? 'Automation is ON.' : 'Automation is OFF.')
  }

  const rotate = async () => {
    setConfirmRotate(false)
    setBusy(true); setError(null); setNotice(null)
    try {
      const s = await orgApi<WebhookSecret>(orgId, 'webhook/secret', { method: 'POST', body: '{}' })
      setReveal(s)
      await refresh()
    } catch (err) {
      // The error text must never carry the secret -- it cannot, because
      // the request failed before one existed on this side.
      setError(err instanceof Error ? err.message : 'Could not generate a secret')
    } finally {
      setBusy(false)
    }
  }

  const saveLimits = async () => {
    const body: Record<string, number> = {}
    const lots = Number(draft.max_lots), perMin = Number(draft.max_per_minute), open = Number(draft.max_open_positions)
    if (!(lots > 0)) { setError('Max lots must be greater than 0'); return }
    if (!(perMin >= 1)) { setError('Alerts per minute must be at least 1'); return }
    if (!(open >= 1)) { setError('Max open positions must be at least 1'); return }
    body.max_lots = lots; body.max_per_minute = perMin; body.max_open_positions = open
    await put(body, 'Limits saved.')
  }

  const copy = async (text: string, what: 'url' | 'template') => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
      window.setTimeout(() => setCopied(null), 2000)
    } catch {
      setError('Could not copy — select the text and copy it by hand')
    }
  }

  const closeReveal = () => {
    // Clear the secret from memory the moment the dialog closes.
    setReveal(null)
    setCopied(null)
  }

  if (!settings) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">Automation</h1>
        {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
        {!error && <p className="text-ink-soft">Loading…</p>}
      </div>
    )
  }

  const ready = settings.has_secret && settings.master_account_id != null && settings.url != null
  const blockers: string[] = []
  if (!settings.has_secret) blockers.push('generate a secret')
  if (settings.master_account_id == null) blockers.push('set a master account')
  if (settings.url == null) blockers.push(settings.url_hint ?? 'set PUBLIC_ORIGIN')
  if (settings.dry_run) blockers.push('turn dry-run off')
  if (!settings.copying_enabled) blockers.push('resume copying')

  return (
    <div className="space-y-8">
      <div>
        <h1 className="page-title">Automation</h1>
        <p className="text-sm text-ink-soft mt-1 max-w-2xl">
          A TradingView alert places the order on the master. Every follower copies it,
          exactly as if a person had traded. Nothing downstream changes.
        </p>
      </div>

      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {/* ---------- status ---------- */}
      <section className="rounded-lg border border-line bg-card p-5 space-y-4">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className={`inline-block w-2.5 h-2.5 rounded-full ${settings.enabled ? 'bg-profit pulse-dot' : 'bg-line-strong'}`}
            />
            <span className="font-semibold text-ink">
              {settings.enabled ? 'Automation is ON' : 'Automation is OFF'}
            </span>
            {settings.enabled && !settings.copying_enabled && (
              <span className="desk-label text-loss-deep bg-loss-wash px-2 py-0.5 rounded">
                copying is stopped — alerts are refused
              </span>
            )}
            {settings.enabled && settings.dry_run && (
              <span className="desk-label text-warn-deep bg-warn-wash px-2 py-0.5 rounded">
                dry run — alerts are refused
              </span>
            )}
          </div>
          {control && (
            <button
              onClick={toggleEnabled}
              disabled={busy || (!settings.enabled && !ready)}
              className={`px-4 py-2 text-sm font-semibold rounded text-on-accent disabled:opacity-50 ${
                settings.enabled ? 'bg-loss hover:bg-loss-deep' : 'bg-profit hover:bg-profit-deep'}`}
            >
              {settings.enabled ? 'TURN OFF' : 'TURN ON'}
            </button>
          )}
        </div>
        {!settings.enabled && blockers.length > 0 && (
          <p className="text-sm text-ink-soft">
            Before it can be turned on: {blockers.join(', ')}.
          </p>
        )}
      </section>

      {/* ---------- the two things TradingView needs ---------- */}
      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-line bg-card p-5 space-y-3">
          <h2 className="desk-label">Webhook URL</h2>
          {settings.url ? (
            <>
              <code className="block text-xs break-all bg-paper border border-line rounded px-3 py-2 text-ink">
                {settings.url}
              </code>
              <button onClick={() => copy(settings.url!, 'url')}
                      className="text-sm font-semibold text-brand hover:underline">
                {copied === 'url' ? 'Copied' : 'Copy URL'}
              </button>
              <p className="text-xs text-ink-faint">
                The URL is an address, not a password. It is safe in TradingView's dialog.
              </p>
            </>
          ) : (
            <p className="text-sm text-ink-soft">{settings.url_hint ?? 'Generate a secret first.'}</p>
          )}
        </div>

        <div className="rounded-lg border border-line bg-card p-5 space-y-3">
          <h2 className="desk-label">Secret</h2>
          {settings.has_secret ? (
            <p className="text-sm text-ink-soft">
              A secret exists
              {settings.secret_created_at && (
                <> — created {new Date(settings.secret_created_at).toLocaleString()}</>
              )}. It cannot be shown again. Generate a new one if you have lost it;
              the old one stops working immediately.
            </p>
          ) : (
            <p className="text-sm text-ink-soft">No secret yet. TradingView cannot sign requests, so this is the only thing that proves an alert is yours.</p>
          )}
          {control && (
            <button onClick={() => settings.has_secret ? setConfirmRotate(true) : rotate()}
                    disabled={busy}
                    className="px-3 py-1.5 text-sm font-semibold rounded border border-brand text-brand hover:bg-brand hover:text-on-accent disabled:opacity-50">
              {settings.has_secret ? 'Generate new secret' : 'Generate secret'}
            </button>
          )}
        </div>
      </section>

      {/* ---------- limits ---------- */}
      <section className="rounded-lg border border-line bg-card p-5 space-y-4">
        <div>
          <h2 className="desk-label">Limits</h2>
          <p className="text-sm text-ink-soft mt-1 max-w-2xl">
            These bound what a template typo or a leaked secret can do. Every alert is checked
            against them before anything reaches the broker.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block">
            <span className="desk-label block mb-1">Max lots per alert</span>
            <input type="number" step="0.01" min="0.01" max="50" value={draft.max_lots}
                   disabled={!control}
                   onChange={(e) => setDraft({ ...draft, max_lots: e.target.value })}
                   className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
          </label>
          <label className="block">
            <span className="desk-label block mb-1">Alerts per minute</span>
            <input type="number" step="1" min="1" max="60" value={draft.max_per_minute}
                   disabled={!control}
                   onChange={(e) => setDraft({ ...draft, max_per_minute: e.target.value })}
                   className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
          </label>
          <label className="block">
            <span className="desk-label block mb-1">Max open positions</span>
            <input type="number" step="1" min="1" max="50" value={draft.max_open_positions}
                   disabled={!control}
                   onChange={(e) => setDraft({ ...draft, max_open_positions: e.target.value })}
                   className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
          </label>
        </div>
        {control && (
          <button onClick={saveLimits} disabled={busy}
                  className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
            Save limits
          </button>
        )}
      </section>

      {/* ---------- setup ---------- */}
      <section className="rounded-lg border border-line bg-card p-5 space-y-3">
        <h2 className="desk-label">Setting up the alert in TradingView</h2>
        <ol className="list-decimal pl-5 space-y-2 text-sm text-ink-soft max-w-2xl">
          <li>Create an alert. Under <strong className="text-ink">Notifications</strong>, tick <strong className="text-ink">Webhook URL</strong> and paste the URL above.</li>
          <li>Untick email and push notifications for this alert — the message carries your secret.</li>
          <li>In the <strong className="text-ink">Message</strong> box, paste the template you were shown when the secret was generated. Change <code>"action"</code> to <code>buy</code>, <code>sell</code> or <code>close</code> and set <code>"lots"</code>.</li>
          <li>Leave <code>{'{{ticker}}'}</code> and <code>{'{{timenow}}'}</code> as they are — TradingView fills them in. Without <code>id</code>, a second identical signal is treated as a repeat and ignored.</li>
          <li><strong className="text-ink">Using a strategy?</strong> Placeholders only render in the alert dialog's Message box, not inside <code>strategy.entry(alert_message=…)</code>. Put the full template in the dialog and keep <code>alert_message</code> to the bare action word.</li>
        </ol>
        <p className="text-sm text-ink-soft max-w-2xl">
          <strong className="text-ink">What is refused:</strong> a buy while the master holds a sell on the same symbol
          (send <code>close</code> first), anything above the limits, anything while copying is stopped
          or dry-run is on, and any alert not from TradingView's own servers.
        </p>
      </section>

      {/* ---------- recent ---------- */}
      <section className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="desk-label">Recent alerts</h2>
          <span className="text-xs text-ink-faint">refreshes every {POLL_MS / 1000}s</span>
        </div>
        {settings.recent.length === 0 ? (
          <p className="text-sm text-ink-soft">No alerts received yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-line bg-card">
            <table className="w-full text-sm stack-table">
              <thead>
                <tr className="text-left">
                  <th className="desk-label px-4 py-2.5 font-semibold">Time</th>
                  <th className="desk-label px-4 py-2.5 font-semibold">Outcome</th>
                  <th className="desk-label px-4 py-2.5 font-semibold">Alert</th>
                  <th className="desk-label px-4 py-2.5 font-semibold">Detail</th>
                  <th className="desk-label px-4 py-2.5 font-semibold text-right">Took</th>
                </tr>
              </thead>
              <tbody>
                {collapse(settings.recent).map((r) => (
                  <tr key={r.id} className="border-t border-line align-top">
                    <td data-label="Time" className="num px-4 py-2.5 whitespace-nowrap text-ink-soft">
                      {new Date(r.received_at).toLocaleTimeString()}
                    </td>
                    <td data-label="Outcome" className="px-4 py-2.5">
                      <OutcomePill outcome={r.outcome} />
                      {r.repeats > 1 && (
                        <span className="ml-2 text-xs text-ink-faint">×{r.repeats}</span>
                      )}
                    </td>
                    <td data-label="Alert" className="px-4 py-2.5 whitespace-nowrap">
                      {r.action ? (
                        <><span className="font-semibold text-ink uppercase">{r.action}</span>{' '}
                          <span className="num">{r.symbol}</span>
                          {r.lots != null && <span className="num text-ink-soft"> {r.lots}</span>}</>
                      ) : <span className="text-ink-faint">—</span>}
                    </td>
                    <td data-label="Detail" className="px-4 py-2.5 text-ink-soft">
                      {r.reason ?? ''}
                      {r.source_ip && r.outcome === 'rejected' && (
                        <span className="text-xs text-ink-faint"> · from {r.source_ip}</span>
                      )}
                    </td>
                    <td data-label="Took" className="num px-4 py-2.5 text-right text-ink-soft whitespace-nowrap">
                      {r.latency_ms != null ? `${r.latency_ms}ms` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ---------- one-time reveal ---------- */}
      {reveal && (
        <div role="dialog" aria-modal="true" aria-labelledby="reveal-title"
             className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-2xl rounded-lg border border-line bg-card p-6 space-y-4">
            <h2 id="reveal-title" className="text-lg font-bold text-ink">Your new secret — shown once</h2>
            <p className="text-sm text-ink-soft">
              Copy the template below into your TradingView alert's Message box now. When you close
              this, the secret is gone from this screen and cannot be shown again.
            </p>
            <pre className="text-xs bg-paper border border-line rounded px-3 py-3 overflow-x-auto text-ink whitespace-pre">
{reveal.template}
            </pre>
            <div className="flex gap-3 flex-wrap">
              <button onClick={() => copy(reveal.template, 'template')}
                      className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep">
                {copied === 'template' ? 'Copied' : 'Copy template'}
              </button>
              {reveal.url && (
                <button onClick={() => copy(reveal.url!, 'url')}
                        className="px-4 py-2 text-sm font-semibold rounded border border-line-strong text-ink hover:bg-paper">
                  {copied === 'url' ? 'Copied' : 'Copy URL'}
                </button>
              )}
              <button onClick={closeReveal}
                      className="ml-auto px-4 py-2 text-sm font-semibold rounded border border-line-strong text-ink hover:bg-paper">
                I have copied it — close
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmRotate}
        title="Generate a new secret?"
        confirmLabel="Generate new secret"
        danger
        busy={busy}
        onConfirm={rotate}
        onCancel={() => setConfirmRotate(false)}
      >
        The current secret stops working the moment a new one is made. Every
        TradingView alert still using the old template will be refused until you
        update it.
      </ConfirmDialog>
    </div>
  )
}

function OutcomePill({ outcome }: { outcome: string }) {
  const style =
    outcome === 'accepted' ? 'bg-profit-wash text-profit-deep'
    : outcome === 'duplicate' ? 'bg-paper text-ink-soft'
    : outcome === 'nothing_to_close' ? 'bg-warn-wash text-warn-deep'
    : outcome === 'unknown' ? 'bg-loss-wash text-loss-deep'
    : outcome === 'failed' ? 'bg-warn-wash text-warn-deep'
    : 'bg-loss-wash text-loss-deep'
  const label = outcome === 'nothing_to_close' ? 'nothing to close'
    : outcome === 'unknown' ? 'UNCONFIRMED' : outcome
  return <span className={`desk-label px-2 py-0.5 rounded ${style}`}>{label}</span>
}

/**
 * Consecutive rejections from the same source with the same reason become
 * one row with a count. Otherwise someone holding only the URL could push
 * every real alert off the bottom of this list with a loop of junk.
 */
function collapse(rows: WebhookReceipt[]): (WebhookReceipt & { repeats: number })[] {
  const out: (WebhookReceipt & { repeats: number })[] = []
  for (const r of rows) {
    const last = out[out.length - 1]
    if (last && last.outcome === 'rejected' && r.outcome === 'rejected'
        && last.reason === r.reason && last.source_ip === r.source_ip) {
      last.repeats += 1
      continue
    }
    out.push({ ...r, repeats: 1 })
  }
  return out
}
