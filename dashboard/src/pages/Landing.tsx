import { Link } from 'react-router-dom'
import Button from '../components/Button'
import Logo, { LogoMark } from '../components/Logo'

/** Company facts the desk operator fills in. Empty strings are simply
 *  not rendered, so the page is truthful before they are known. */
export const LANDING_FACTS = {
  legalName: 'MirrorFleet',
  address: '',
  supportEmail: '',
}

const FEATURES = [
  {
    title: 'Copy engine',
    text: 'One master account, any number of followers. Every fill on the master is mirrored to each follower with its own lot scaling, as it happens.',
  },
  {
    title: 'cTrader and MetaTrader 5',
    text: 'Connect cTrader accounts through the broker API and MetaTrader 5 terminals through the MirrorFleet expert advisor, side by side on one desk.',
  },
  {
    title: 'Risk engine',
    text: 'Stop-loss and target rules per symbol, a cap on open positions, and a kill switch that flattens an account in one click.',
  },
  {
    title: 'TradingView automation',
    text: 'Point a TradingView alert at your desk and it becomes a market, stop or limit order, with cancel and close actions, protected by a secret.',
  },
  {
    title: 'Investor portal',
    text: 'Investors deposit to the desk, watch the equity, positions and history of their own account, and request withdrawals that an admin approves and pays.',
  },
  {
    title: 'Alerts and logs',
    text: 'Email and Telegram alerts for copy failures, offline terminals and investor requests, and a log of every action with who did it.',
  },
]

const STEPS = [
  { n: 1, title: 'Connect a master account', text: 'Link the account you trade on, cTrader or MetaTrader 5.' },
  { n: 2, title: 'Add followers and set the rules', text: 'Connect follower accounts, choose lot scaling, and set stop, target and exposure limits.' },
  { n: 3, title: 'Trade once', text: 'Place the trade on the master. The fleet mirrors it, and the desk shows every fill as it lands.' },
]

const INVESTOR_STEPS = [
  { title: 'Deposit', text: "Send crypto to the desk's wallet and file a deposit notice. An admin confirms it against the chain." },
  { title: 'Watch', text: 'Your own trading account is linked to your login: live equity, open positions, closed trades and a performance snapshot.' },
  { title: 'Withdraw', text: 'Request an amount and a destination. An admin approves, pays from the desk wallet and records the transaction.' },
]

const FAQ = [
  {
    q: 'Which platforms are supported?',
    a: "cTrader accounts connect through the broker's Open API. MetaTrader 5 accounts connect through a small expert advisor that runs in the terminal. Either can be a master or a follower.",
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
    q: 'How do I get access?',
    a: 'Create an account, or ask the desk operator for an invite link if sign-up is invite-only. Investors are invited by the desk that manages their money.',
  },
]

const RISK_NOTICE =
  'Trading leveraged products such as forex, metals and CFDs carries a high level of risk and may not be suitable for everyone. Past results do not predict future results. Nothing on this page is investment advice.'

function Ctas({ large = false }: { large?: boolean }) {
  const size = large ? 'md' : 'sm'
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button to="/register" size={size}>Create account</Button>
      <Button to="/login" variant="secondary" tone="brand" size={size}>Sign in</Button>
    </div>
  )
}

function HeroArt() {
  return (
    <div className="rounded-2xl bg-brand-wash border border-line p-8 md:p-12 flex items-center justify-center"
         aria-hidden="true">
      <LogoMark size={220} />
    </div>
  )
}

export default function Landing() {
  const year = new Date().getFullYear()
  return (
    <div className="min-h-screen bg-paper text-ink">
      <header className="sticky top-0 z-10 glass border-b">
        <div className="max-w-6xl mx-auto px-4 md:px-6 min-h-16 py-2 flex items-center justify-between gap-6">
          <Logo size={30} textClass="text-xl" />
          <nav aria-label="Sections" className="hidden md:flex items-center gap-6 text-sm text-ink-soft">
            <a href="#platform" className="hover:text-ink">Platform</a>
            <a href="#how" className="hover:text-ink">How it works</a>
            <a href="#investors" className="hover:text-ink">Investors</a>
            <a href="#faq" className="hover:text-ink">FAQ</a>
          </nav>
          <Ctas />
        </div>
      </header>

      <main>
        <section className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-24 grid gap-10 md:grid-cols-2 items-center">
          <div className="space-y-6">
            <p className="desk-label text-brand">Copy trading desk</p>
            <h1 className="font-display text-4xl md:text-5xl font-bold leading-tight">
              Trade once. Mirror it across every account.
            </h1>
            <p className="text-lg text-ink-soft">
              MirrorFleet copies your master account to a fleet of cTrader and MetaTrader 5
              follower accounts, guards each one with risk rules, takes orders from TradingView,
              and gives your investors a portal of their own.
            </p>
            <Ctas large />
          </div>
          <HeroArt />
        </section>

        <section id="platform" className="border-t border-line bg-card">
          <div className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-8">
            <div className="max-w-2xl">
              <h2 className="font-display text-3xl font-bold">Platform</h2>
              <p className="mt-2 text-ink-soft">Everything a copy desk needs, in one place.</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((f) => (
                <article key={f.title} className="rounded-lg border border-line bg-paper p-5 space-y-2">
                  <h3 className="font-semibold">{f.title}</h3>
                  <p className="text-sm text-ink-soft">{f.text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="how" className="border-t border-line">
          <div className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-8">
            <h2 className="font-display text-3xl font-bold">How it works</h2>
            <ol className="grid gap-4 md:grid-cols-3">
              {STEPS.map((s) => (
                <li key={s.n} className="rounded-lg border border-line bg-card p-5 space-y-2">
                  <div className="w-8 h-8 rounded-full bg-brand text-on-accent flex items-center justify-center font-semibold">
                    {s.n}
                  </div>
                  <h3 className="font-semibold">{s.title}</h3>
                  <p className="text-sm text-ink-soft">{s.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="investors" className="border-t border-line bg-card">
          <div className="max-w-6xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-8">
            <div className="max-w-2xl">
              <h2 className="font-display text-3xl font-bold">For investors</h2>
              <p className="mt-2 text-ink-soft">
                Your money is traded on an account that is yours to watch, from deposit to withdrawal.
              </p>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              {INVESTOR_STEPS.map((s) => (
                <article key={s.title} className="rounded-lg border border-line bg-paper p-5 space-y-2">
                  <h3 className="font-semibold">{s.title}</h3>
                  <p className="text-sm text-ink-soft">{s.text}</p>
                </article>
              ))}
            </div>
            <Ctas />
          </div>
        </section>

        <section id="faq" className="border-t border-line">
          <div className="max-w-3xl mx-auto px-4 md:px-6 py-16 md:py-20 space-y-8">
            <h2 className="font-display text-3xl font-bold">FAQ</h2>
            <dl className="space-y-6">
              {FAQ.map((item) => (
                <div key={item.q}>
                  <dt className="font-semibold">{item.q}</dt>
                  <dd className="mt-1 text-sm text-ink-soft">{item.a}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </main>

      <footer className="border-t border-line bg-card">
        <div className="max-w-6xl mx-auto px-4 md:px-6 py-10 space-y-6 text-sm text-ink-soft">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Logo size={26} textClass="text-lg" />
            <div className="flex items-center gap-4">
              <Link to="/login" className="hover:text-ink">Sign in</Link>
              <Link to="/register" className="hover:text-ink">Create account</Link>
            </div>
          </div>
          <p>{RISK_NOTICE}</p>
          <p>
            © {year} {LANDING_FACTS.legalName}
            {LANDING_FACTS.address && ` · ${LANDING_FACTS.address}`}
            {LANDING_FACTS.supportEmail && (
              <> · <a href={`mailto:${LANDING_FACTS.supportEmail}`} className="hover:text-ink">{LANDING_FACTS.supportEmail}</a></>
            )}
          </p>
        </div>
      </footer>
    </div>
  )
}
