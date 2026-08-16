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

Put your public FPL team ID in `config.yaml` (`team_id`) or set `FPL_TEAM_ID`. GW1 does
not need it: the solver builds a 100.0m initial squad.

```bash
python -m fpl_manager --no-email --force
python -m pytest -q
python -m fpl_manager.backtest --from-gw 2
```

## GitHub Actions email

The workflow `.github/workflows/weekly.yml` runs daily at 09:00 UTC. It only emails when
the next deadline is within 60 hours, and sends a second mail inside 24 hours only if the
recommendation changed.

Create a **private** GitHub repo, then add:

- Variable `FPL_TEAM_ID` (optional until you want transfer diffs)
- Secrets: `SMTP_USER`, `SMTP_PASSWORD` (Gmail app password), `EMAIL_TO`
- Optional secrets: `SMTP_HOST` (default `smtp.gmail.com`), `SMTP_PORT` (default `587`)

Manual send from Actions: Run workflow (force is a CLI flag locally: `--email --force`).

## Known approximations

- Sell prices approximated from public transfer history (no auth)
- Component independence in variance; bonus-points model is crude (`0.3 × E[goals+assists]`)
- Team strength from FPL's own ratings, not xG models (upgrade path: understat/FBref later)
- Free-transfer future value as a constant in v1
- DGW priors are hand-set constants until fixtures confirm
- Before GW1, minutes/xG come from last-season totals on `bootstrap-static` (match history is empty)
- MIP uses linear variance penalty `E[points] - λ_var · Var[points]` (`lambda_var` in config)
