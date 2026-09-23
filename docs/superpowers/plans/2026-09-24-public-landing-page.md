# Public Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Visitors to the domain root see a public MirrorFleet website with Sign in and Create account buttons; signed-in users still land in their workspace.

**Architecture:** One new page component in the existing React dashboard, rendered by `RootRedirect` when `/api/me` fails; no backend change. Copy and company placeholders live as constants at the top of the page file.

**Tech Stack:** React 18, TypeScript, react-router-dom, Tailwind v4 tokens already defined in `src/index.css`, vitest + testing-library.

**Spec:** `docs/superpowers/specs/2026-09-24-public-landing-page-design.md`

## Global Constraints

- Honest copy only: no invented statistics, testimonials, awards, client counts or regulatory status; nothing that imitates Vantage or IC Markets. The exact copy is in Task 1 and is used verbatim.
- Every "Sign in" link goes to `/login`; every "Create account" link goes to `/register`.
- Use the app's existing tokens (`bg-paper`, `bg-card`, `text-ink`, `text-ink-soft`, `bg-brand`, `text-brand`, `bg-brand-wash`, `border-line`, `text-on-accent`) so light and dark themes work; no new CSS, no external images.
- `RootRedirect` must call `api('/api/me', undefined, { redirectOn401: false })`; without that option the API helper hard-redirects to `/login` on 401 and the landing page never renders.
- All paths below are relative to `dashboard/`. Tests: `npx vitest run <file>` from `dashboard/`; type check `npx tsc --noEmit -p tsconfig.app.json`.

---

### Task 1: The Landing page

**Files:**
- Create: `src/pages/Landing.tsx`
- Test: `src/pages/Landing.test.tsx`

**Interfaces:**
- Consumes: `Logo` (default) and `LogoMark` (named) from `src/components/Logo.tsx`; `Link` from react-router-dom.
- Produces: default export `Landing()`; named export `LANDING_FACTS`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/Landing.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import Landing from './Landing'

test('the front page offers sign in and account creation and names its sections', () => {
  render(<MemoryRouter><Landing /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/trade once/i)

  const signIns = screen.getAllByRole('link', { name: 'Sign in' })
  expect(signIns.length).toBeGreaterThan(0)
  for (const a of signIns) expect(a).toHaveAttribute('href', '/login')

  const creates = screen.getAllByRole('link', { name: 'Create account' })
  expect(creates.length).toBeGreaterThan(1)
  for (const a of creates) expect(a).toHaveAttribute('href', '/register')

  for (const name of ['Platform', 'How it works', 'For investors', 'FAQ']) {
    expect(screen.getByRole('heading', { name })).toBeInTheDocument()
  }
  expect(screen.getByText(/high level of risk/i)).toBeInTheDocument()
})

test('the page makes no claims it cannot back', () => {
  render(<MemoryRouter><Landing /></MemoryRouter>)
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/regulated|licensed|award|[0-9,]+\+? (clients|traders|users)/i)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/Landing.test.tsx`
Expected: FAIL — module `./Landing` not found.

- [ ] **Step 3: Write the page**

```tsx
// src/pages/Landing.tsx
import { Link } from 'react-router-dom'
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
  const pad = large ? 'px-6 py-3 text-base' : 'px-4 py-2 text-sm'
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Link to="/register"
            className={`${pad} font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep transition-colors`}>
        Create account
      </Link>
      <Link to="/login"
            className={`${pad} font-semibold rounded border border-line-strong text-ink hover:bg-brand-wash transition-colors`}>
        Sign in
      </Link>
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
      <header className="sticky top-0 z-10 bg-card/95 backdrop-blur border-b border-line">
        <div className="max-w-6xl mx-auto px-4 md:px-6 h-16 flex items-center justify-between gap-6">
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
```

- [ ] **Step 4: Run to verify it passes, plus the type check**

Run: `npx vitest run src/pages/Landing.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Landing.tsx src/pages/Landing.test.tsx
git commit -m "feat(dashboard): public landing page"
```

---

### Task 2: Serve the landing page at the root for signed-out visitors

**Files:**
- Modify: `src/App.tsx` (`RootRedirect`)
- Test: `src/App.test.tsx` (append)

**Interfaces:**
- Consumes: `Landing` from `./pages/Landing`; `api` from `./lib/api` (third argument `{ redirectOn401: false }`).
- Produces: `/` renders `<Landing />` when `/api/me` fails; unchanged navigation when it succeeds.

- [ ] **Step 1: Write the failing test** (append to `src/App.test.tsx`; it already has `render`, `screen`, `vi`, `beforeEach` that resets the URL to `/`, and `afterEach` that unstubs globals)

```tsx
test('/ shows the public front page when nobody is signed in', async () => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/me') {
      return Promise.resolve(new Response(JSON.stringify({ detail: 'Unauthorized' }),
        { status: 401, headers: { 'content-type': 'application/json' } }))
    }
    throw new Error(`Unexpected fetch: ${url}`)
  }))

  render(<App />)

  expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(/trade once/i)
  expect(window.location.pathname).toBe('/')
  expect(screen.getAllByRole('link', { name: 'Sign in' })[0]).toHaveAttribute('href', '/login')
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/App.test.tsx`
Expected: the new test FAILS (no level-1 heading; today `/` navigates to `/login`). The three existing tests still pass.

- [ ] **Step 3: Change `RootRedirect`**

In `src/App.tsx`, add the import after `import Logs from './pages/Logs'` (and after the investor imports if present):

```tsx
import Landing from './pages/Landing'
```

Replace the body of `RootRedirect` so that a failed `/api/me` renders the landing page instead of navigating to `/login`:

```tsx
/** `/` → the last-used org, else the first org, else /welcome; signed-out
 *  visitors get the public front page instead of the login screen. */
function RootRedirect() {
  const [target, setTarget] = useState<string | null>(null)

  useEffect(() => {
    const resolve = async () => {
      try {
        const me = await api<Me>('/api/me', undefined, { redirectOn401: false })
        const last = Number(localStorage.getItem(LAST_ORG_KEY))
        const org = me.orgs.find((o) => o.id === last) ?? me.orgs[0]
        setTarget(org ? `/org/${org.id}` : '/welcome')
      } catch {
        setTarget('landing')
      }
    }
    resolve()
  }, [])

  if (!target) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }
  if (target === 'landing') return <Landing />
  return <Navigate to={target} replace />
}
```

- [ ] **Step 4: Run the App tests, the Login/Register tests and the type check**

Run: `npx vitest run src/App.test.tsx src/pages/Login.test.tsx src/pages/Register.test.tsx src/pages/Landing.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: all PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/App.tsx src/App.test.tsx
git commit -m "feat(dashboard): signed-out visitors land on the public front page"
```
