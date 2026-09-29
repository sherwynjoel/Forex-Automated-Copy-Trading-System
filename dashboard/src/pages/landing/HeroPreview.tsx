import StatusDot from '../../components/StatusDot'

/**
 * Hero art used when no screenshot of the running Overview could be
 * captured: a static mock of the Overview built from the desk's own
 * `.inset` surface and example figures. Exposed to assistive technology as
 * one labelled image so its figures are never read out as if they were
 * live. Sits inside the hero's own glass `<figure>`, so its tiles are
 * `.inset` (not nested glass cards) to avoid blur stacked on blur.
 */
const LABEL =
  'Example of the MirrorFleet Overview: headline figures, then every account ' +
  'in the fleet with its equity and copy status. Example data.'

const TILES: {
  label: string
  value: string
  sub: string
  tone: 'text-brand' | 'text-ink' | 'text-profit'
  big?: boolean
}[] = [
  { label: 'Portfolio value', value: '63,656.40', sub: '+0.84% vs yesterday', tone: 'text-brand', big: true },
  { label: 'Accounts in the fleet', value: '5', sub: '1 master · 4 followers', tone: 'text-ink' },
  { label: 'Open P&L', value: '+412.35', sub: '6 open trades', tone: 'text-profit' },
  { label: 'Total P&L', value: '+528.10', sub: '5 accounts since yesterday', tone: 'text-profit' },
]

const ACCOUNTS: {
  name: string
  role: 'Master' | 'Follower'
  platform: 'cTrader' | 'MT5'
  equity: string
  status: 'ok' | 'paused' | 'degraded'
  label: string
}[] = [
  { name: 'Main book', role: 'Master', platform: 'cTrader', equity: '25,410.20', status: 'ok', label: 'Connected' },
  { name: 'Growth A', role: 'Follower', platform: 'cTrader', equity: '10,212.55', status: 'ok', label: 'Copying' },
  { name: 'Growth B', role: 'Follower', platform: 'MT5', equity: '5,104.90', status: 'ok', label: 'Copying' },
  { name: 'Family', role: 'Follower', platform: 'cTrader', equity: '2,540.00', status: 'paused', label: 'Paused' },
  { name: 'Swing', role: 'Follower', platform: 'MT5', equity: '20,388.75', status: 'degraded', label: 'Offline' },
]

export default function HeroPreview() {
  return (
    <div role="img" aria-label={LABEL} className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {TILES.map((t) => (
          <div key={t.label} className="inset p-3">
            <div className="text-xs font-medium text-ink-soft">{t.label}</div>
            <div className={`num ${t.big ? 'text-xl sm:text-2xl' : 'text-2xl'} font-semibold tracking-tight mt-1 ${t.tone}`}>
              {t.value}
            </div>
            <div className="text-xs text-ink-soft mt-0.5">{t.sub}</div>
          </div>
        ))}
      </div>
      <div className="inset overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-line">
              <th className="desk-label px-2 sm:px-3 py-2 font-semibold">Account</th>
              <th className="desk-label px-2 sm:px-3 py-2 font-semibold hidden sm:table-cell">Role</th>
              <th className="desk-label px-2 sm:px-3 py-2 font-semibold hidden sm:table-cell">Platform</th>
              <th className="desk-label px-2 sm:px-3 py-2 font-semibold text-right">Equity</th>
              <th className="desk-label px-2 sm:px-3 py-2 font-semibold">Status</th>
            </tr>
          </thead>
          <tbody>
            {ACCOUNTS.map((a) => (
              <tr key={a.name} className="border-b border-line last:border-0">
                <td className="px-2 sm:px-3 py-2 text-ink">{a.name}</td>
                <td className="px-2 sm:px-3 py-2 text-ink-soft hidden sm:table-cell">{a.role}</td>
                <td className="px-2 sm:px-3 py-2 text-ink-soft hidden sm:table-cell">{a.platform}</td>
                <td className="num px-2 sm:px-3 py-2 text-right text-ink">{a.equity}</td>
                <td className="px-2 sm:px-3 py-2">
                  <span className="inline-flex items-center gap-1.5 text-ink-soft">
                    <StatusDot tone={a.status} />
                    {a.label}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
