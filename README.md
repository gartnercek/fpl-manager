# fpl-manager

Advisory Fantasy Premier League manager. It never submits anything. Once a day the GitHub
Action checks the next deadline; inside 60 hours it projects expected points for every player,
plans transfers and chips over the next weeks, and emails the plan.

## What it does each gameweek

1. **Data.** Per-match history for every player (`element-summary`), fixtures, prices, injury
   flags, your public squad, bank, transfer history and chips. Free transfers are rebuilt from
   your history (1 per GW, up to 5 banked, a WC/FH week keeps the bank but adds none, a hit
   resets to 1).
2. **Expected points** (`projection.py`), only from data known before the deadline:
   - team attack/defence ratings fitted on this season's match xG (recency weighted), with
     last season's ratings as a prior (FPL's own `strength_*` fields are empty in 2026/27);
   - minutes: recency-weighted start/cameo rates, times FPL's chance-of-playing;
   - per-90 xG, xA, bonus, saves, cards and DEFCON hit rate: this season + last season + a
     position/price prior;
   - FPL scoring incl. DEFCON, clean sheets/goals conceded/saves from Poisson rates, Polymarket
     team totals blended into this GW's goal rates when the book is liquid;
   - a final per-position calibration (fitted on 2025/26) that stops the optimiser overpaying
     for premiums whose rates are over-extrapolated.
   It deliberately does **not** use FPL `form` / `ep_next` / points-per-game: those are lagging
   point totals and were what made the old version chase last week's haul.
3. **Transfers** (`planner.py`): one MIP over the next 6 GWs (transfers in the first 2, XI and
   captain re-picked every week) with budget/sell prices, 3-per-club, formations, FT banking,
   hits (a -4 must also clear a 2-point margin) and the value of rolling an FT.
4. **Chips**: every chip is available once per half (GW1-19 and GW20-38; first-half chips
   expire after GW19). Each GW it solves the plan with each available chip played now and
   compares the gain with the expected best gain from keeping it for a later GW in the same
   half (Triple Captain / Bench Boost from our own projections of those weeks, Wildcard / Free
   Hit from a prior; second-half priors are higher because doubles and blanks are announced
   late). If more chips are left than GWs in the half, one is played now.
5. **Letter** (`report.py`): action summary, transfers with xP gain, XI, bench order,
   captain/vice, chip verdicts with the numbers, the provisional plan for the next weeks, risk
   notes and the top players by position.

## Backtest

`python -m fpl_manager.backtest` replays this season from GW1 under the FPL rules
(`simulator.py`: prices, sell-on fee, FT banking, hits, auto-subs, vice-captain, all chips per
half) and scores every decision with the points players actually got. Each decision only sees
data from before that deadline. Historical injury flags are not public, so the backtest uses
heavy net transfers out as a news proxy; the live letter uses the real flags.

```bash
python -m fpl_manager.backtest                    # 2026/27, GW1 to last finished GW
python -m fpl_manager.backtest --season-dir data/vaastav/2025-26 --prev-dir data/vaastav/2024-25
```

Past seasons come from the [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
archive (download `gws/merged_gw.csv`, `fixtures.csv`, `teams.csv`, `players_raw.csv` into the folder).

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

```bash
python -m fpl_manager --no-email --force
python -m pytest -q
```

## Find your FPL team ID

It is **not** shown in the official Premier League app. Use a browser.

1. Open [fantasy.premierleague.com](https://fantasy.premierleague.com) and log in.
2. Click **Points**. The address bar looks like `https://fantasy.premierleague.com/entry/1234567/event/1`.
3. The number after `/entry/` is your team ID. Put it in `config.yaml` as `team_id`, or set the
   GitHub variable `FPL_TEAM_ID`.

## Where email details go

Do **not** put your Gmail password in `config.yaml` or commit it. GitHub Actions reads secrets.

1. Create a [Gmail app password](https://myaccount.google.com/apppasswords) (2-Step Verification
   must be on). Use that 16-character password, not your normal one.
2. Repo **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**:

   | Name | Value |
   |---|---|
   | `SMTP_USER` | the Gmail address that sends |
   | `SMTP_PASSWORD` | the 16-character app password |
   | `EMAIL_TO` | the inbox that should receive the weekly plan |

   Optional: `SMTP_HOST`, `SMTP_PORT`, `EMAIL_REPLY_TO`, `EMAIL_OPERATOR` (failure notices),
   `SUBSCRIBERS_JSON` (extra teams: `[{"team_id": 123, "email": "a@b.co"}]`), `BILLING_BLOCKLIST`.
3. **Variables** tab -> `FPL_TEAM_ID` = your team ID.

Then **Actions** -> **fpl-weekly** -> **Run workflow** -> tick **force**. The daily 09:00 UTC run
only emails when a deadline is within 60 hours, and re-sends inside 24 hours only if the advice
changed (new injury flag, price change).

## Known approximations

- Sell prices are rebuilt from public transfer history (FPL does not publish them without login).
- Transfers are planned for the next two GWs; later weeks of the 6-GW horizon hold that squad.
- Wildcard / Free Hit hold values are priors, not projections of future rebuilds.
- Bonus is a per-90 rate scaled by fixture, not a BPS simulation.
- Polymarket odds cannot be backtested (no history); they only touch this GW's fixtures.
