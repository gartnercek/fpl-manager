# FPL Manager — Implementation Specification

> Build this as a **standalone repository** (working name `fpl-manager`).
> Follow the milestones in order. Do not build the optional items until the core loop works.

---

## 1. Product summary

A Python program that, once per gameweek (scheduled via GitHub Actions), pulls Fantasy
Premier League data, computes expected points with uncertainty for every player, solves a
constrained optimization for the user's squad, and **emails the user a recommendation**:

- 0–2 transfers (with explicit expected-value justification, including whether a −4 hit pays)
- Starting XI, bench order, captain, vice-captain
- Chip advice: whether **this** gameweek is the right time for Triple Captain or Bench Boost
  (and, secondarily, Wildcard / Free Hit), with the math shown

The program **never submits anything to FPL**. It is advisory only. The user applies changes
manually on the website. No FPL login/authentication is required (read-only public endpoints
suffice).

### Non-goals (v1)
- No auto-submit / autopilot (no authenticated write calls)
- No mini-league rival modeling
- No web UI, no database server, no ML training pipeline
- No price-change prediction

---

## 2. User context and objective

- Single user, one FPL team, identified by a public **team ID** (config value).
- Objective: **maximize expected points with a variance penalty** (mean–variance utility),
  i.e. `maximize E[points] − λ · SD[points]` with a configurable risk-aversion `λ`
  (default λ = 0.15; must be a config knob, and the email should show results for λ=0 too
  so the user sees what pure-EV would have picked).
- The user is mathematically literate: emails should show numbers (xP, variance, EV of a hit,
  probabilities), not just names.

---

## 3. Data sources (all public JSON, no scraping, no auth)

Base URL: `https://fantasy.premierleague.com/api/`

| Endpoint | Use |
|---|---|
| `bootstrap-static/` | Master data: players (`elements`), teams, positions, prices, form, status flags (`a`/`d`/`i`/`s`/`u`), `chance_of_playing_next_round`, current/next gameweek, chip metadata |
| `fixtures/` | Full season fixtures with `team_h_difficulty` / `team_a_difficulty`, kickoff times; detect **blank and double gameweeks** (0 or 2+ fixtures for a team in one event) |
| `element-summary/{player_id}/` | Per-player match history this season (minutes, xG, xA per match) and past seasons |
| `entry/{team_id}/` | User's public team metadata, bank value, chips used |
| `entry/{team_id}/event/{gw}/picks/` | User's current 15, captain, bench order (as of last completed deadline) |
| `entry/{team_id}/history/` | Chips already used this season |
| `event/{gw}/live/` | Live/final points per player per gameweek (used for backtesting, not the weekly run) |

Implementation notes:
- Single `requests.Session` with retries (3 attempts, exponential backoff) and a UA header.
- Cache raw JSON responses to `data/cache/{date}/` during a run so a rerun is deterministic.
- All prices are in tenths (e.g. `now_cost = 75` means £7.5m). **Selling prices** for the
  user's squad are not available without auth; approximate with the standard FPL sell rule
  (purchase price + floor(profit/2)) using purchase prices from the transfers endpoint
  `entry/{team_id}/transfers/`; document this approximation in the email footer when bank
  math is tight (within £0.3m of infeasibility).

---

## 4. Player model (expected points as a distribution)

For every player, produce `(mu, sigma2)` = mean and variance of next-gameweek points, plus
`mu_horizon[k]` for the next `k = 1..6` gameweeks (needed for transfer planning and chips).

### 4.1 Minutes model (highest-leverage component — do this carefully)
Model `P(start)`, `P(cameo)`, `E[minutes | start]` per player:
- Base rates from last 6 matches' minutes (exponentially weighted, half-life 3 matches).
- Hard overrides from status flags: `status in {i, s, u}` → P(play) = 0;
  `status = d` → multiply by `chance_of_playing_next_round / 100`.
- Expose the resulting `P(≥60 min)` and `P(1–59 min)` — appearance points depend on the 60' cutoff.

### 4.2 Attacking returns
- Per-90 rates for goals (`expected_goals_per_90`) and assists (`expected_assists_per_90`)
  from `element-summary` season data, shrunk toward position priors
  (empirical-Bayes: weight = n_90s / (n_90s + 5)).
- Fixture adjustment: scale by opponent strength using team attack/defence ratings derived
  from `bootstrap-static` team `strength_attack_home/away`, `strength_defence_home/away`
  (normalize to league mean = 1.0). Home/away split respected.
- Goals scored in a match: Poisson with the adjusted rate × expected minutes / 90.
  Points per goal by position (GK/DEF 6, MID 5, FWD 4); assists 3 for all.

### 4.3 Clean sheets and defensive points
- Team clean-sheet probability: Poisson on opponent's adjusted scoring rate → `P(CS) = exp(−λ_opp)`.
- Player CS points require `P(≥60 min)` × `P(CS)` (4 pts GK/DEF, 1 pt MID).
- GK saves: Poisson on expected shots faced → E[save points] = E[floor(saves/3)] (approximate
  via the Poisson pmf up to 15 saves).
- Goals conceded penalty for GK/DEF: −1 per 2 conceded, computed from the same Poisson.

### 4.4 Aggregation
- `mu` = sum of component means (appearance, goals, assists, CS, saves, conceded, minus
  expected yellow-card rate × 1). Bonus points: approximate as a smooth function of
  expected involvement (a simple regression `bonus ≈ f(xG+xA, CS)` fit on this season's data
  is acceptable; even `0.3 × (goals+assists mean)` is fine for v1 — flag it as crude).
- `sigma2` = sum of component variances treating components as independent (documented
  simplification). Captaincy doubles both `mu` and (×4) `sigma2` for that player.
- **Double gameweeks:** sum the two fixtures' distributions. **Blanks:** `mu = 0`.
- Cache the full per-player table to `data/predictions/gw{n}.parquet` for the email appendix
  and backtesting.

---

## 5. Squad optimization (MIP)

Use **PuLP with CBC** (pure-Python install, works in GitHub Actions).

### 5.1 Decision variables
For each player `p`: `own_p`, `start_p`, `captain_p`, `vice_p`, bench positions; plus
`in_p`, `out_p` transfer indicators relative to the current squad.

### 5.2 Constraints (FPL rules — encode exactly)
- Squad of 15: 2 GK, 5 DEF, 5 MID, 3 FWD
- Max 3 players per club
- Budget: `Σ buy prices of incoming ≤ bank + Σ sell prices of outgoing`
- Starting XI: 11 starters, formation with ≥1 GK (exactly 1), ≥3 DEF, ≥2 MID (FPL allows 2–5), ≥1 FWD
- Exactly 1 captain and 1 vice, both starters, captain ≠ vice
- Transfers: `Σ in_p = Σ out_p = T`, where `T ∈ {0, 1, 2}` is enumerated (solve the MIP
  three times); free transfers available `F` come from config/state; cost = `max(0, T − F) × 4`

### 5.3 Objective
`maximize Σ_p start_p·mu_p + captain_p·mu_p − hit_cost − λ · sqrt(Σ start_p·sigma2_p + 3·captain_p·sigma2_p)`

The square root makes this non-linear; acceptable approaches (pick one, document it):
1. **Preferred:** maximize `Σ mu − λ_var · Σ sigma2` (linear in variance, `λ_var ≈ 0.02`), or
2. Solve pure-EV MIP, then re-rank the top 20 solutions (via solution-pool / no-good cuts)
   by the true mean–SD utility.

Bench value: add `0.1 × Σ bench_p·mu_p` to the objective so the bench is not garbage
(this also feeds Bench Boost planning).

### 5.4 Transfer horizon (banking logic)
A transfer is only recommended if it beats **rolling** the free transfer:
compare `best plan with T transfers now` vs `T−1 now + expected value of the extra FT next week`
(estimate the FT's future value as the historical mean gain of the best single transfer,
computed from the 6-gameweek horizon `mu_horizon`; a constant ≈ 1.5 pts is an acceptable v1
fallback, clearly labeled). Recommend a hit (−4) only if the modeled gain over the next
**3 gameweeks** exceeds 4 by ≥ 1.0 pt margin.

---

## 6. Chip timing (Triple Captain & Bench Boost — the "sophisticated" part)

Both chips are **optimal-stopping problems**: use the chip now iff the current opportunity
beats the expected best remaining opportunity. Implement both with the same framework.

### 6.1 Opportunity value
- **Triple Captain (TC):** `V_now = mu_captain` (the extra points are `+1 × mu_captain`
  on top of normal captaincy). Compute for the best captain candidate this GW.
- **Bench Boost (BB):** `V_now = Σ bench mu` for the best feasible squad this GW.

### 6.2 Future opportunity distribution
For each remaining gameweek `g`, estimate `V_g` using:
- Fixture difficulty for premium captain candidates (TC) / whole-squad fixtures (BB)
- **Double gameweeks are the dominant factor.** Before doubles are announced (they firm up
  around GW 25–34 due to cup clashes), apply a prior: `P(at least one DGW for a top team) ≈ 0.9`
  historically, with typical DGW captain `V ≈ 1.6×` a normal-week `V`. Encode these as
  config constants with comments, not magic numbers.

### 6.3 Stopping rule
Play the chip in the current gameweek iff:

`V_now ≥ E[max over remaining g of V_g] − epsilon`

where the expected maximum is estimated by Monte Carlo (sample fixture-adjusted `V_g` with
noise, take the max, average over 2,000 draws), and `epsilon = 0.5` pts avoids knife-edge
recommendations. The email must show `V_now`, the estimated `E[max future V]`, and the
verdict ("HOLD: expected best future TC week ≈ GW34 double, worth ~4.1 extra pts vs ~2.6 now").

- Never recommend TC and BB the same week.
- If a confirmed **double gameweek** exists for the user's likely captain, TC in a DGW
  nearly always dominates single-GW use — the stopping rule handles this naturally, but add
  an assertion/test for it.
- Wildcard / Free Hit: v1 only *detects* the trigger conditions and mentions them in the email
  ("your squad's 4-week xP is ≥ 12 pts below the unconstrained optimal squad → consider
  Wildcard"); full WC squad construction is a later milestone.

### 6.4 State
Track chips already used via `entry/{team_id}/history/`. Never recommend a used chip.

---

## 7. Email report

Delivery: SMTP via Gmail app password (config/secrets: `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`). Plain-text body plus an HTML version with tables.

Required sections, in order:
1. **Header:** GW number, deadline datetime (user's timezone, Europe/Ljubljana), hours remaining
2. **Action summary** (≤5 lines): transfers, captain, chip verdicts — what to actually click
3. **Transfer analysis:** for each recommended (and each *almost*-recommended) transfer:
   `xP gained this GW`, `xP gained over 3 GW`, hit math if applicable, price/bank feasibility
4. **XI + bench order + captain/vice** with each player's `mu ± sd`, opponent, home/away
5. **Chip section:** the TC/BB stopping-rule numbers as specified in §6.3
6. **Risk notes:** any starter with `P(play) < 0.75`, flagged players, deadline quirks
7. **Appendix:** top-15 players by `mu` this GW at each position (so the user can sanity-check)

If the run fails, send a failure email with the traceback summary rather than staying silent.

---

## 8. Scheduling (GitHub Actions)

Deadlines move (Friday/Saturday, occasional midweek), so **do not hardcode cron to deadlines**:

- Workflow runs **daily at 09:00 UTC** (`cron: "0 9 * * *"`) plus `workflow_dispatch` for
  manual runs.
- The program fetches the next deadline from `bootstrap-static`. Logic:
  - If deadline is **within 60 hours**: run full pipeline, send email. Mark the GW as
    "emailed" in `state/state.json` (committed back to the repo by the workflow, or kept in
    Actions cache — committing is simpler and auditable; do that).
  - If already emailed for this GW and deadline is **within 24 hours**: re-run and send a
    **delta email only if** recommendations changed (new injury flag, price change breaking
    feasibility). Otherwise exit silently.
  - Else: exit silently.
- Secrets via GitHub Actions secrets. `FPL_TEAM_ID` can be a plain env/config value.

---

## 9. Repository layout

```
fpl-manager/
├── pyproject.toml            # deps: requests, pulp, numpy, pandas, jinja2 (email HTML)
├── README.md                 # setup: team ID, Gmail app password, GH secrets, local run
├── .github/workflows/weekly.yml
├── config.yaml               # team_id, lambda_risk, horizon, chip constants, email settings
├── state/state.json          # last emailed GW, free-transfer count, chips-used mirror
├── src/fpl_manager/
│   ├── __main__.py           # `python -m fpl_manager` → full pipeline
│   ├── api.py                # FPL endpoints, session, caching
│   ├── models/minutes.py     # §4.1
│   ├── models/returns.py     # §4.2–4.3
│   ├── models/aggregate.py   # §4.4 → per-player (mu, sigma2, horizon)
│   ├── optimize.py           # §5 MIP
│   ├── chips.py              # §6 stopping rules + Monte Carlo
│   ├── report.py             # §7 email rendering + SMTP
│   └── state.py              # §8 state read/write
├── tests/                    # see §10
└── data/                     # gitignored cache & predictions
```

## 10. Testing & validation (required, not optional)

1. **Rule tests:** MIP constraint unit tests (club limit, formation bounds, budget, captain logic)
   with tiny synthetic player pools where the optimum is known by hand.
2. **Model sanity tests:** premium striker vs bench fodder ordering; injured player mu = 0;
   DGW mu ≈ 2× SGW mu for the same opponent strength.
3. **Chip tests:** confirmed-DGW-captain scenario must trigger TC; loaded bench in a DGW must
   trigger BB; both must HOLD in an ordinary early-season week.
4. **Backtest harness (`python -m fpl_manager.backtest --season current --from-gw 2`):**
   replay past gameweeks of the current season using `event/{gw}/live/` actuals; report
   the strategy's cumulative points vs (a) the user's actual team, (b) doing nothing.
   This is the acceptance test for the whole model: the strategy should beat "do nothing".
5. **Dry-run mode:** `--no-email` prints the report to stdout; use in CI on every PR.

## 11. Milestones (implement in this order)

1. **M1 — Data + model:** `api.py` + player model; CLI prints top-20 xP table for next GW.
2. **M2 — Optimizer:** full MIP; CLI prints recommended XI/captain/transfers for the user's real team.
3. **M3 — Email + Actions:** report rendering, SMTP, daily workflow, state file. *(System is useful from here.)*
4. **M4 — Chips:** stopping-rule engine + Monte Carlo + email section.
5. **M5 — Backtest:** harness + README results table.
6. **M6 (optional, later):** Wildcard squad construction, Free Hit, authenticated sell prices,
   delta-email injury watch on Fridays.

## 12. Known approximations (keep this list honest in README)

- Sell prices approximated from public transfer history (no auth)
- Component independence in variance; bonus-points model is crude
- Team strength from FPL's own ratings, not xG models (upgrade path: understat/FBref later)
- Free-transfer future value as a constant in v1
- DGW priors are hand-set constants until fixtures confirm
