# Public landing page — design

**Requested:** 2026-09-24 (night). The user wants visitors to the domain to see a
business website for MirrorFleet, in the spirit of broker sites such as
vantagemarkets.com and ic.com, with "Sign in" and "Create account" buttons that
lead into the app, instead of landing straight on the login screen. Designed
without the user available; every choice below is an assumption they can
change.

## 1. Scope

- One public, unauthenticated page at `/` rendered by the existing React
  dashboard. Signed-in visitors keep today's behaviour: `/` sends them to their
  last workspace.
- No new backend. "Sign in" links to `/login`; "Create account" links to
  `/register` (which already explains, when `REGISTRATION_ENABLED` is false,
  that an invite is needed).
- Out of scope: a CMS, a blog, multiple languages, contact forms, cookie
  banners, analytics.

## 2. Content rules

- Honest copy only: no invented statistics, testimonials, awards, client
  counts, or regulatory status. Nothing that imitates Vantage or IC Markets.
- Company facts the user must supply later are placeholders in one place
  (`LANDING_FACTS` at the top of the page file): legal name, address, support
  email. They render as "MirrorFleet" and a generic support line until filled.
- A plain risk notice in the footer: trading leveraged products carries a
  high level of risk and may not suit everyone; past results do not predict
  future results. No licence claims.

## 3. Page structure (top to bottom)

1. **Header** — `Logo`, section links (Platform, How it works, Investors,
   FAQ), "Sign in" (link, secondary) and "Create account" (button, brand).
   Sticky, card background, hairline bottom border.
2. **Hero** — headline "Trade once. Mirror it across every account."; one
   paragraph describing MirrorFleet as a copy-trading desk for cTrader and
   MetaTrader 5 with a risk engine and TradingView automation; primary button
   "Create account", secondary "Sign in"; a static illustration built from
   the existing `LogoMark` (three sails, mirrored) rather than a screenshot.
3. **Platform** — six feature cards, each one sentence: Copy engine
   (master → followers, lot scaling); Platforms (cTrader, MetaTrader 5);
   Risk engine (stop/target rules, max open, kill switch); TradingView
   automation (webhooks, market/stop/limit, cancel); Investor portal
   (deposit notices, equity, withdrawals); Alerts (email, Telegram).
4. **How it works** — three numbered steps: Connect a master account; Add
   follower accounts and set risk rules; Trade once, the fleet mirrors it.
5. **For investors** — three short columns: Deposit (crypto to the desk's
   wallet, admin confirms), Watch (live equity, positions, history, from the
   investor's own account), Withdraw (request, admin approves and pays);
   button "Create account".
6. **FAQ** — five questions with one-paragraph answers: Which platforms?
   Do you hold my funds? (No: accounts stay at the broker; the desk never
   holds keys.) How does the copy engine size trades? What happens if my
   terminal goes offline? How do I get access? (Sign up, or ask the desk
   for an invite.)
7. **Footer** — Logo, the placeholders from `LANDING_FACTS`, links Sign in /
   Create account, the risk notice, "© {year} MirrorFleet".

## 4. Visual design

- Uses the app's tokens (`bg-paper`, `bg-card`, `text-ink`, `bg-brand`,
  `text-brand`, wash colours) so light and dark themes both work without new
  CSS; Rubik is already loaded.
- Layout: centred column `max-w-6xl`, sections separated by `py-16/24`,
  cards `rounded-lg border border-line bg-card`. Feature grid 1 → 2 → 3
  columns at `sm`/`lg`. Header collapses section links below `md`, keeping
  both CTAs.
- No images from the internet; the hero art is inline SVG derived from
  `LogoMark`.

## 5. Routing change

`RootRedirect` in `src/App.tsx`: on a successful `/api/me` it navigates as
today; on failure it renders `<Landing />` instead of `<Navigate to="/login">`.
The loading placeholder stays while `/api/me` is pending so signed-in users do
not flash the marketing page.

## 6. Files

- Create `src/pages/Landing.tsx` (default export `Landing`), with
  `LANDING_FACTS` and the copy as constants at the top; test
  `src/pages/Landing.test.tsx`.
- Modify `src/App.tsx` (`RootRedirect`); extend `src/App.test.tsx` if it
  exists, else add the root behaviour to `Landing.test.tsx` via a small
  render of `RootRedirect` exported for tests.

## 7. Tests

- The page renders the hero headline, and both "Sign in" links point to
  `/login` and every "Create account" link points to `/register`.
- Section headings exist (Platform, How it works, For investors, FAQ).
- The footer risk notice is present.
- Root: with `/api/me` failing, `/` shows the landing page; with `/api/me`
  succeeding, `/` navigates to `/org/<id>` (existing behaviour).
- Type check and the existing Login/Register tests unchanged.

## 8. Follow-ups for the user

- Fill `LANDING_FACTS` (legal name, address, support email).
- Decide whether to enable public sign-up (`REGISTRATION_ENABLED=true` on
  the server) or keep invite-only, in which case "Create account" leads to
  the invite explanation.
- Legal review of the copy and the risk notice for their jurisdiction.
