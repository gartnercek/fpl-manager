"""Replay a season with the weekly advisor and score it on real points.

    python -m fpl_manager.backtest                      # this season, GW1..last finished
    python -m fpl_manager.backtest --season-dir data/vaastav/2025-26 --prev-dir data/vaastav/2024-25

Every decision only sees data from before that GW's deadline: per-match history up to the
previous GW, prices at the deadline, and (backtest only) a news proxy from net transfers.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from fpl_manager.planner import HALF_END, LAST_GW, Plan, PlannerConfig, WeekPlan, plan_gameweek
from fpl_manager.projection import ProjectionConfig, availability_proxy, project
from fpl_manager.season import SeasonData
from fpl_manager.simulator import Team, run_season


def make_decider(
    season: SeasonData,
    pcfg: ProjectionConfig | None = None,
    plcfg: PlannerConfig | None = None,
    *,
    projection_horizon: int = 8,
    log=None,
):
    pcfg = pcfg or ProjectionConfig()
    plcfg = plcfg or PlannerConfig()
    players_base = season.players.set_index("element")

    def decide(gw: int, team: Team, prices: dict[int, int]) -> WeekPlan:
        t0 = time.time()
        av = availability_proxy(season, gw) if season.status is None else None
        half_end = HALF_END if gw <= HALF_END else LAST_GW
        proj = project(season, gw, max(projection_horizon, plcfg.horizon, half_end - gw + 1), pcfg, av)
        xp_full = proj.xp_matrix()
        xp = xp_full[[w for w in xp_full.columns if w < gw + plcfg.horizon]]
        players = proj.table[["pos", "team"]].copy()
        players["price"] = [prices.get(int(p), int(pr)) for p, pr in zip(proj.table.index, proj.table.price)]
        state = team.state(prices)
        plan = plan_gameweek(xp, players, state, gw, plcfg, p_start=proj.table.p_start, xp_full=xp_full)
        if log:
            wp = plan.first
            names = proj.table.name
            log(
                f"   GW{gw} plan: chip={wp.chip} in={[names[p] for p in wp.transfers_in]} "
                f"out={[names[p] for p in wp.transfers_out]} C={names[wp.captain]} xp={wp.xp:.1f} "
                f"({time.time() - t0:.0f}s) | " + " ".join(n for n in plan.notes if n.startswith(("PLAY", "HOLD")))
            )
        return plan.first

    return decide


def run(season: SeasonData, gws: list[int], ft_topups: dict[int, int] | None = None, **kw) -> dict[str, Any]:
    pos = dict(zip(season.players.element.astype(int), season.players.pos.astype(int)))
    log = kw.pop("log", print)
    decide = make_decider(season, log=log, **kw)
    results, team = run_season(season.pm, pos, season.price_at, decide, gws, log=log, ft_topups=ft_topups)
    total = sum(r.points for r in results)
    return {"total": total, "weeks": [asdict(r) for r in results]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl_manager.backtest")
    parser.add_argument("--season-dir", help="vaastav season folder (default: live FPL API, this season)")
    parser.add_argument("--prev-dir", help="vaastav folder of the previous season (team priors)")
    parser.add_argument("--from-gw", type=int, default=1)
    parser.add_argument("--to-gw", type=int, default=0)
    parser.add_argument("--horizon", type=int, default=0)
    parser.add_argument("--out", help="write JSON results here")
    args = parser.parse_args(argv)

    if args.season_dir:
        from fpl_manager.season import from_vaastav

        season = from_vaastav(args.season_dir, args.prev_dir)
        season.status = None
    else:
        from fpl_manager.pipeline import load_season

        season = load_season()
        season.status = None  # replay with the backtest news proxy, not today's flags
    last = args.to_gw or season.last_finished_gw
    gws = list(range(args.from_gw, last + 1))
    if not gws:
        print("No finished gameweeks to replay yet.")
        return 0
    plcfg = PlannerConfig()
    if args.horizon:
        plcfg = replace(plcfg, horizon=args.horizon)
    out = run(season, gws, plcfg=plcfg)
    print(f"TOTAL GW{gws[0]}-{gws[-1]}: {out['total']}")
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
