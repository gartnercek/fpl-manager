# fpl-manager

Advisory Fantasy Premier League manager. It never submits transfers. Once a week (or on
demand) it models expected points, solves a squad under FPL rules, times Triple Captain /
Bench Boost with an optimal-stopping rule, and emails the plan.

## Status

| Milestone | What |
|---|---|
| M1 | FPL API + expected-points model |
| M2 | Squad MIP (15, XI, captain, transfers) |
| M3 | Email report + GitHub Actions daily cron |
| M4 | Triple Captain / Bench Boost stopping rule |
| M5 | Backtest harness (no-ops until GW2 finishes) |

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

```bash
python -m fpl_manager --no-email --force
python -m pytest -q
python -m fpl_manager.backtest --from-gw 2
```

## Find your FPL team ID

It is **not** shown in the official Premier League app. Use a browser (Chrome/Edge/Safari).

1. Open [fantasy.premierleague.com](https://fantasy.premierleague.com) and log in.
2. You must already have entered a squad for **this** season (a new ID is issued every year).
3. Click **Points** (or **Pick Team** → **Gameweek history**).
4. Look at the address bar. It looks like:

   `https://fantasy.premierleague.com/entry/1234567/event/1`

   The number after `/entry/` is your team ID (`1234567` in that example). Ignore `/event/`.

Paste that number into `config.yaml` as `team_id`. You can also set GitHub variable `FPL_TEAM_ID`
(same number). Until it is set, the program still builds a 100.0m GW1 squad; it just cannot
diff against *your* current 15.

## Public site

Marketing pages live in `site/` and are meant to ship at [fpl-manager.com](https://fpl-manager.com). Card checkout is a Cloudflare Pages Function (`/api/checkout`) plus Stripe. The weekly GitHub Action reads who paid and emails each team. See `site/README.md` for DNS, Search Console, and the Stripe checklist.

The public GW letter is [fpl-manager.com/letter.html](https://fpl-manager.com/letter.html). Paste-ready X, Reddit, Scout, and email copy lives in `launch/TONIGHT.md`.

## Where email details go

Do **not** put your Gmail password in `config.yaml` or commit it. GitHub Actions reads secrets.

1. Create a [Gmail app password](https://myaccount.google.com/apppasswords) (Google Account →
   Security → 2-Step Verification must be on → App passwords). Use that 16-character password,
   not your normal Gmail password.
2. Open the repo: [github.com/gartnercek/fpl-manager](https://github.com/gartnercek/fpl-manager)
3. **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add:

   | Name | Value |
   |---|---|
   | `SMTP_USER` | your Gmail address (the account that sends) |
   | `SMTP_PASSWORD` | the 16-character app password |
   | `EMAIL_TO` | the inbox that should receive the weekly plan |

   Optional: `SMTP_HOST` = `smtp.gmail.com`, `SMTP_PORT` = `587` (defaults exist if you skip these).

4. **Settings** → **Secrets and variables** → **Actions** → **Variables** tab →
   `FPL_TEAM_ID` = your team ID from above.

Then: **Actions** → **fpl-weekly** → **Run workflow** → tick **force** → Run. That sends mail
immediately. The daily 09:00 UTC cron only emails when a deadline is within 60 hours.

Paid subscribers are picked up from Stripe when `STRIPE_SECRET_KEY` is set (see `site/README.md`).
`EMAIL_TO` still receives the operator letter for `FPL_TEAM_ID`.

## Known approximations

- Squad MIP maximises **5-week** XI+captain xP (captain and XI re-picked each GW). GW1 xP is displayed separately.
- Each week it compares T=0/1/2 transfers (including -4 hits), a **Wildcard** rebuild, and a **Free Hit** (this GW only, then revert). A 5-week MIP scores the new 15 (so a 100-then-10 punt loses to 60-then-60). WC/FH then have a **stopping rule**: the gap is shrunk until there is this-season data, the first ~1/e weeks of the half are sample-only (unless the trusted gap is huge), then play iff that beats E[best remaining swing]. Unused chips still get cheaper to play in the last 3 GWs of GW19 / GW38.
- One chip per week: if WC or FH is the move, TC/BB are held.
- Set pieces: FPL `penalties_order` / corners / direct FKs. Season xG is stripped of a typical PK rate for stayers so pens are not double-counted; new takers get the full PK module. Misses are -2.
- DEFCON: 2 pts if simulated CBIT/CBIRT hits the FPL threshold (10 DEF / 12 MID-FWD).
- Formation: 1.2 xP/week penalty for a 5th starting defender; 0.8 for starting only one forward. Players with P(play)<0.55 cannot start.
- Minutes: last-6 matches when they exist; otherwise last-season totals mixed with price and ownership. Summer signings are not treated as nailed.
- Form: last-6 xG vs season rate when history exists; otherwise FPL `form` (zero before GW1). GW1 also blends FPL `ep_next`.
- Points are Monte Carlo (minutes, Poisson returns, and clean sheets share the same draws). Bonus is still `0.3 × (G+A)` inside each draw.
- Team strength from FPL's own ratings, not xG models. Sell prices from public transfer history.
- Free-transfer future value is a constant. DGW chip priors are constants until fixtures confirm.
