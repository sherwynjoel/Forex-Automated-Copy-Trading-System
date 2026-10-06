import { useEffect, useRef, useState } from 'react'
import { api, orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { errorText } from '../lib/format'
import { TOPICS, TOPIC_EMAIL_LABELS } from '../lib/engagement'
import { isThemePref } from '../lib/themeSync'
import { useTheme } from '../hooks/useTheme'
import Banner from '../components/Banner'
import Card from '../components/Card'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import type { NotificationPrefs, NotificationTopic, ThemePref, UserSettings } from '../lib/types'

/** Light, Dim, System. The server also accepts 'dark', which paints the
 *  same night palette and therefore shows as Dim. */
const THEME_OPTIONS: [ThemePref, string][] = [['light', 'Light'], ['dim', 'Dim'], ['system', 'System']]

/**
 * The signed-in user's own settings: Appearance (per account, every org)
 * and the email switches (per org). Desk: /org/:id/settings from the rail;
 * investors: invest/settings from the nav.
 */
export default function Settings() {
  const { orgId } = useOrg()
  const { choose } = useTheme()
  const [theme, setThemePref] = useState<ThemePref | null>(null)
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Bumped per org: a late answer for the previous org never lands.
  const seq = useRef(0)
  // Bumped per theme pick: a stale PUT answer (success or failure) from an
  // earlier pick never overwrites what a later pick already showed.
  const themeSeq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    setPrefs(null)
    Promise.all([
      api<UserSettings>('/api/me/settings'),
      orgApi<NotificationPrefs>(orgId, 'notification-prefs'),
    ]).then(
      ([s, p]) => {
        if (mine !== seq.current) return
        setThemePref(isThemePref(s?.theme) ? s.theme : 'system')
        setPrefs(p)
      },
      (err) => {
        if (mine !== seq.current) return
        setError(errorText(err, 'Could not load your settings'))
      },
    )
  }, [orgId])

  const pickTheme = async (pref: ThemePref) => {
    const mine = ++themeSeq.current
    setThemePref(pref)
    choose(pref)
    setError(null); setNotice(null)
    try {
      await api<UserSettings>('/api/me/settings', { method: 'PUT', body: JSON.stringify({ theme: pref }) })
      if (mine === themeSeq.current) setNotice('Appearance saved')
    } catch (err) {
      if (mine === themeSeq.current) setError(errorText(err, 'Could not save the appearance'))
    }
  }

  const flip = async (topic: NotificationTopic) => {
    if (!prefs) return
    const before = prefs
    const next = { ...prefs, [topic]: !prefs[topic] }
    const mine = seq.current
    setPrefs(next); setBusy(true); setError(null); setNotice(null)
    try {
      const saved = await orgApi<NotificationPrefs>(orgId, 'notification-prefs', {
        method: 'PUT', body: JSON.stringify(next) })
      if (mine === seq.current) { setPrefs(saved); setNotice('Email preferences saved') }
    } catch (err) {
      if (mine === seq.current) { setPrefs(before); setError(errorText(err, 'Could not save your email preferences')) }
    } finally {
      setBusy(false)
    }
  }

  const shown = theme === 'dark' ? 'dim' : theme
  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        title="Settings"
        subtitle="How the portal looks for you wherever you sign in, and which notifications also reach your inbox."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {theme == null || prefs == null ? (
        !error && <Loading lines={4} label="Loading settings" />
      ) : (
        <>
          <Card title="Appearance">
            <fieldset>
              <legend className="desk-label mb-2">Theme</legend>
              <div className="flex flex-wrap gap-4 text-sm">
                {THEME_OPTIONS.map(([value, label]) => (
                  <label key={value} className="flex items-center gap-2 text-ink">
                    <input type="radio" name="theme" value={value} className="accent-brand"
                           checked={shown === value} onChange={() => { void pickTheme(value) }} />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          </Card>
          <Card title="Email notifications">
            <p className="mb-3 text-sm text-ink-soft">
              Everything still appears under Notifications; these switches only decide what is also emailed.
            </p>
            <div className="space-y-3">
              {TOPICS.map((t) => (
                <label key={t} className="flex items-center justify-between gap-4 cursor-pointer has-[:disabled]:cursor-default">
                  <span className="text-sm text-ink">{TOPIC_EMAIL_LABELS[t]}</span>
                  <span className="relative inline-flex items-center">
                    <input type="checkbox" role="switch" checked={prefs[t]} disabled={busy}
                           onChange={() => { void flip(t) }} className="peer sr-only" />
                    <span aria-hidden="true"
                          className="h-5 w-9 rounded-full bg-ink-faint transition-colors peer-checked:bg-brand peer-disabled:opacity-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand" />
                    <span aria-hidden="true"
                          className="pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-card transition-transform peer-checked:translate-x-4" />
                  </span>
                </label>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
