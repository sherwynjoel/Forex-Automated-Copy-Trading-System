import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import Button from '../components/Button'
import Card from '../components/Card'
import Logo from '../components/Logo'
import StatusDot from '../components/StatusDot'
import HeroPreview from './landing/HeroPreview'

/** Company facts the desk operator fills in. Empty strings are simply
 *  not rendered, so the page stays truthful if one is cleared. */
export const LANDING_FACTS = {
  legalName: 'MirrorFleet',
  // Filled by the owner: blank until the mailbox and address are confirmed.
  address: '',
  supportEmail: '',
}

/** The tab title. Set here rather than through usePageTitle, whose
 *  "Title · MirrorFleet" format would print the brand twice. */
export const PAGE_TITLE = 'MirrorFleet — copy trading for cTrader and MT5'

/* ---------------------------------------------------------------------------
   Example data for the three product panels. Every figure below is made up
   for illustration and the page says so under the panels.
--------------------------------------------------------------------------- */

type FleetStatus = 'ok' | 'paused' | 'degraded'

const MASTER = { name: 'Main book', platform: 'cTrader', equity: '25,410.20' }

const FOLLOWERS: {
  name: string
  platform: 'cTrader' | 'MT5'
  multiplier: string
  equity: string
  status: FleetStatus
  label: string
}[] = [
  { name: 'Growth A', platform: 'cTrader', multiplier: '1.00', equity: '10,212.55', status: 'ok', label: 'Copying' },
  { name: 'Growth B', platform: 'MT5', multiplier: '0.50', equity: '5,104.90', status: 'ok', label: 'Copying' },
  { name: 'Family', platform: 'cTrader', multiplier: '0.25', equity: '2,540.00', status: 'paused', label: 'Paused' },
  { name: 'Swing', platform: 'MT5', multiplier: '2.00', equity: '20,388.75', status: 'degraded', label: 'Offline' },
]

const COPY_LOG: { at: string; who: string; what: string; tone: 'text-ink' | 'text-profit' | 'text-ink-soft' | 'text-loss' }[] = [
  { at: '09:41:07.212', who: 'Main book', what: 'bought 1.00 lot XAUUSD', tone: 'text-ink' },
  { at: '09:41:07.341', who: 'Growth A', what: 'copied 1.00 lot, filled', tone: 'text-profit' },
  { at: '09:41:07.356', who: 'Growth B', what: 'copied 0.50 lot, filled', tone: 'text-profit' },
  { at: '09:41:07.357', who: 'Family', what: 'not copied, paused', tone: 'text-ink-soft' },
  { at: '09:41:07.357', who: 'Swing', what: 'not copied, terminal offline', tone: 'text-loss' },
]

const RISK_RULES = [
  { symbol: 'XAUUSD', stop: '300', target: '600' },
  { symbol: 'EURUSD', stop: '150', target: '300' },
  { symbol: 'US500', stop: '400', target: '800' },
]

const LIMITS = [
  { name: 'Max lots per alert', value: '2.00' },
  { name: 'Alerts per minute', value: '10' },
  { name: 'Max open positions', value: '5' },
]

const INVESTOR_STEPS = [
  { title: 'Deposit', text: "Send crypto to the desk's wallet and file a deposit notice. An admin confirms it against the chain." },
  { title: 'Watch', text: 'Your own trading account is linked to your login: live equity, open positions, closed trades and a performance snapshot.' },
  { title: 'Withdraw', text: 'Request an amount and a destination. An admin approves, pays from the desk wallet and records the transaction.' },
]

const FAQ = [
  {
    q: 'Which platforms are supported?',
    a: 'cTrader accounts connect through the cTrader Open API via cTrader ID OAuth, and no broker password is ever entered. MetaTrader 5 accounts connect through a small expert advisor that runs in the terminal. Either can be a master or a follower.',
  },
  {
    q: 'Does MirrorFleet hold my funds?',
    a: "No. Trading accounts stay at your broker, and the desk never holds account passwords or wallet keys. Investor deposits go to the desk operator's wallet, and withdrawals are paid by the operator.",
  },
  {
    q: 'How are follower trades sized?',
    a: 'Each follower has a lot multiplier relative to the master, with per-symbol aliases for brokers that name instruments differently. Risk rules can add or tighten stops after a fill.',
  },
  {
    q: 'What happens if a terminal goes offline?',
    a: 'The desk marks the account as disconnected, alerts the operator, and shows the last known equity until the terminal reports again. Nothing is copied to an account that is offline.',
  },
  {
    q: 'Can I try it without placing trades?',
    a: 'Yes. With dry run on, the desk watches the master and logs what it would have sent to each follower, without placing a single order.',
  },
  {
    q: 'How do I get access?',
    a: 'Create an account, or ask the desk operator for an invite link if sign-up is invite-only. Investors are invited by the desk that manages their money.',
  },
]

const RISK_NOTICE =
  'Trading leveraged products such as forex, metals and CFDs carries a high level of risk and may not be suitable for everyone. Past results do not predict future results. Nothing on this page is investment advice.'

/** The footer's address/email block. Extracted so its empty-value
 *  behaviour is unit-testable by rendering a variant with different facts,
 *  without needing a second data module or a module mock. */
export function FooterContact({ facts = LANDING_FACTS }: { facts?: typeof LANDING_FACTS }) {
  if (!facts.address && !facts.supportEmail) return null
  return (
    <address className="not-italic">
      {facts.address}
      {facts.address && facts.supportEmail && ' · '}
      {facts.supportEmail && (
        <a href={`mailto:${facts.supportEmail}`} className="text-brand-deep hover:underline">
          {facts.supportEmail}
        </a>
      )}
    </address>
  )
}

function Ctas({ large = false, compact = false }: { large?: boolean; compact?: boolean }) {
  const size = large ? 'lg' : 'sm'
  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* On a phone the header keeps only Sign in; the hero carries Create account. */}
      <div className={compact ? 'hidden sm:block' : undefined}>
        <Button to="/register" size={size}>Create account</Button>
      </div>
      <Button to="/login" variant="secondary" tone="brand" size={size}>Sign in</Button>
    </div>
  )
}

function FleetPanel() {
  return (
    <Card as="article" className="md:col-span-2 md:row-span-2 md:self-start">
      <div className="space-y-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-ink">Every follower on one screen</h3>
          <p className="text-sm text-ink-soft max-w-xl">
            Each follower copies the master with its own lot multiplier. cTrader accounts connect
            through the cTrader Open API via cTrader ID OAuth, so no broker password is ever entered;
            MetaTrader 5 terminals connect through the MirrorFleet expert advisor, and the desk shows
            each one&apos;s equity and whether it is copying.
          </p>
        </div>
        <div className="inset p-3 space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2 px-1">
            <p className="text-sm text-ink">
              <span className="font-semibold">{MASTER.name}</span>
              <span className="text-ink-soft"> · master · {MASTER.platform}</span>
            </p>
            <p className="text-sm text-ink-soft">
              Equity <span className="num text-ink">{MASTER.equity}</span> USD
            </p>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2">
            {FOLLOWERS.map((f) => (
              <li key={f.name} className="rounded-control border border-line p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-ink">{f.name}</span>
                  <span className="text-xs text-ink-soft">{f.platform} · ×{f.multiplier}</span>
                </div>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="num text-xl text-ink">{f.equity}</span>
                  <span className="inline-flex items-center gap-1.5 text-xs text-ink-soft">
                    <StatusDot tone={f.status} />
                    {f.label}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  )
}

function CopyLogPanel() {
  return (
    <Card as="article">
      <div className="space-y-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-ink">Every fill, timestamped</h3>
          <p className="text-sm text-ink-soft">
            The master&apos;s fill and each follower&apos;s copy land in one log. Rejected copies and
            degraded accounts also raise an email alert.
          </p>
        </div>
        <ol className="inset divide-y divide-line text-sm">
          {COPY_LOG.map((line) => (
            <li key={`${line.at}-${line.who}`} className="flex gap-3 px-3 py-2">
              <time dateTime={line.at} className="num text-ink-faint shrink-0">{line.at}</time>
              <span className={line.tone}>
                <span className="font-semibold">{line.who}</span> {line.what}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </Card>
  )
}

function RiskPanel() {
  return (
    <Card as="article">
      <div className="space-y-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-ink">Limits before the broker</h3>
          <p className="text-sm text-ink-soft">
            Every TradingView alert is checked against your limits before it reaches the broker, and a
            symbol rule fills in the stop and target when the alert sends none. One click flattens an
            account.
          </p>
        </div>
        <div className="inset p-3 space-y-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label py-1.5 font-semibold">Symbol</th>
                <th className="desk-label py-1.5 font-semibold text-right">Stop (pts)</th>
                <th className="desk-label py-1.5 font-semibold text-right">Target (pts)</th>
              </tr>
            </thead>
            <tbody>
              {RISK_RULES.map((r) => (
                <tr key={r.symbol} className="border-b border-line last:border-0">
                  <td className="py-1.5 text-ink">{r.symbol}</td>
                  <td className="num py-1.5 text-right text-ink">{r.stop}</td>
                  <td className="num py-1.5 text-right text-ink">{r.target}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl className="space-y-1 text-sm">
            {LIMITS.map((l) => (
              <div key={l.name} className="flex items-baseline justify-between gap-3">
                <dt className="text-ink-soft">{l.name}</dt>
                <dd className="num text-ink">{l.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </Card>
  )
}

export default function Landing() {
  const year = new Date().getFullYear()

  useEffect(() => {
    const previous = document.title
    document.title = PAGE_TITLE
    return () => {
      document.title = previous
    }
  }, [])

  return (
    <div className="min-h-screen text-ink">
      <header className="sticky top-0 z-10 px-4 md:px-6 pt-3">
        <div className="glass rounded-card shadow-card max-w-6xl mx-auto px-4 min-h-14 py-2 flex items-center justify-between gap-6">
          <Logo size={30} textClass="text-xl" />
          <nav aria-label="Sections" className="hidden md:flex items-center gap-6 text-sm text-ink-soft">
            <a href="#platform" className="hover:text-ink">Platform</a>
            <a href="#how" className="hover:text-ink">How it works</a>
            <a href="#investors" className="hover:text-ink">Investors</a>
            <a href="#faq" className="hover:text-ink">FAQ</a>
          </nav>
          <Ctas compact />
        </div>
      </header>

      <main>
        <section className="max-w-6xl mx-auto px-4 md:px-6 pt-12 md:pt-20 pb-16 space-y-10 md:space-y-14">
          <div className="max-w-3xl space-y-6">
            <h1 className="hero-title font-bold text-ink">
              Trade once. Mirror it across every account.
            </h1>
            <p className="text-lg text-ink-soft max-w-2xl">
              MirrorFleet copies your master account to every cTrader and MetaTrader 5 follower,
              checks each order against your risk rules, takes orders from TradingView, and gives your
              investors a portal of their own.
            </p>
            <Ctas large />
          </div>
          <figure className="glass rounded-card shadow-card p-2 md:p-3">
            <HeroPreview />
            <figcaption className="mt-2 text-sm text-ink-faint">Example data, not trading results.</figcaption>
          </figure>
        </section>

        <section id="platform" className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-8">
          <div className="max-w-2xl space-y-2">
            <h2 className="text-3xl font-bold text-ink">One desk for every account</h2>
            <p className="text-ink-soft">
              The same screens you would use every day: the fleet, the copy log and the rules that
              guard it.
            </p>
          </div>
          <div className="grid gap-5 md:grid-cols-3">
            <FleetPanel />
            <CopyLogPanel />
            <RiskPanel />
          </div>
          <p className="text-sm text-ink-faint">The panels above show example data, not trading results.</p>
        </section>

        <section id="how" className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-5">
          <h2 className="text-3xl font-bold text-ink">How it works</h2>
          <p className="text-lg text-ink-soft max-w-3xl">
            <strong className="font-semibold text-ink">Connect the account you trade on</strong>, cTrader
            or MetaTrader 5, as the master. <strong className="font-semibold text-ink">Add your
            followers</strong>, give each a lot multiplier, and set the stop, target and exposure
            limits. Then <strong className="font-semibold text-ink">trade once</strong> on the master:
            the fleet mirrors it, and the desk shows every fill as it lands. Start with dry run on and
            the desk logs what it would have sent without placing a single order.
          </p>
        </section>

        <div id="investors" className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20">
          <Card as="section" title="For investors">
            <div className="space-y-6">
              <p className="text-ink-soft max-w-2xl">
                Your money is traded on an account that is yours to watch, from deposit to withdrawal.
              </p>
              <dl className="grid gap-x-8 gap-y-3 md:grid-cols-[8rem_1fr]">
                {INVESTOR_STEPS.map((s) => (
                  <div key={s.title} className="contents">
                    <dt className="font-semibold text-ink">{s.title}</dt>
                    <dd className="text-sm text-ink-soft">{s.text}</dd>
                  </div>
                ))}
              </dl>
              <Ctas />
            </div>
          </Card>
        </div>

        <section id="faq" className="max-w-3xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-6">
          <h2 className="text-3xl font-bold text-ink">Questions</h2>
          <div className="border-t border-line">
            {FAQ.map((item) => (
              <details key={item.q} className="group border-b border-line">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-4 font-semibold text-ink [&::-webkit-details-marker]:hidden">
                  {item.q}
                  <svg aria-hidden="true" viewBox="0 0 16 16"
                       className="h-4 w-4 shrink-0 text-brand transition-transform group-open:rotate-180">
                    <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </summary>
                <p className="pb-4 text-sm text-ink-soft">{item.a}</p>
              </details>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="max-w-6xl mx-auto px-4 md:px-6 py-10 space-y-6 text-sm text-ink-soft">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Logo size={26} textClass="text-lg" />
            <div className="flex items-center gap-4">
              <Link to="/login" className="hover:text-ink">Sign in</Link>
              <Link to="/register" className="hover:text-ink">Create account</Link>
            </div>
          </div>
          <p>{RISK_NOTICE}</p>
          <div className="space-y-1">
            <p>© {year} {LANDING_FACTS.legalName}</p>
            <FooterContact />
          </div>
        </div>
      </footer>
    </div>
  )
}
