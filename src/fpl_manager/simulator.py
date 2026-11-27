"""Replay a season under FPL rules and score decisions with the points players actually scored.

Rules implemented: 100.0m start, sell price = purchase + floor(profit / 2), 1 FT per GW banked
up to 5 (kept but not added in a WC/FH week), -4 per extra transfer, automatic substitutions
in bench order with formation checks, vice-captain cover, Triple Captain, Bench Boost,
Free Hit (squad reverts next GW), Wildcard, each chip once per half (GW1-19, GW20-38).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from fpl_manager.planner import CHIPS, SquadState, WeekPlan, half_of


def sell_price(purchase: int, now: int) -> int:
    if now <= purchase:
        return now
    return purchase + (now - purchase) // 2


@dataclass
class Team:
    squad: list[int]
    purchase: dict[int, int]
    bank: int
    ft: int
    chips_left: dict[tuple[str, int], bool]

    @classmethod
    def fresh(cls) -> Team:
        return cls([], {}, 1000, 1, {(c, h): True for c in CHIPS for h in (1, 2)})

    def state(self, prices: dict[int, int]) -> SquadState:
        sell = {p: sell_price(self.purchase[p], prices.get(p, self.purchase[p])) for p in self.squad}
        return SquadState(list(self.squad), sell, self.bank, self.ft, dict(self.chips_left))


@dataclass
class GWResult:
    gw: int
    points: int
    chip: str | None
    transfers: int
    hits: int
    captain: int
    captain_points: int
    bench_points: int
    xp: float
    ins: list[int] = field(default_factory=list)
    outs: list[int] = field(default_factory=list)
    starters: list[int] = field(default_factory=list)
    squad: list[int] = field(default_factory=list)


def gw_actuals(pm: pd.DataFrame, gw: int) -> tuple[dict[int, int], dict[int, int]]:
    rows = pm[pm.gw == gw]
    pts = rows.groupby("element").points.sum()
    mins = rows.groupby("element").minutes.sum()
    return {int(k): int(v) for k, v in pts.items()}, {int(k): int(v) for k, v in mins.items()}


def _valid(xi: list[int], pos: dict[int, int]) -> bool:
    cnt = {k: 0 for k in (1, 2, 3, 4)}
    for p in xi:
        cnt[pos[p]] += 1
    return cnt[1] == 1 and cnt[2] >= 3 and cnt[4] >= 1 and cnt[3] >= 2 and len(xi) == 11


def auto_subs(starters: list[int], bench: list[int], mins: dict[int, int], pos: dict[int, int]) -> list[int]:
    xi = list(starters)
    used: set[int] = set()
    for p in list(starters):
        if mins.get(p, 0) > 0:
            continue
        for b in bench:
            if b in used or mins.get(b, 0) <= 0:
                continue
            if (pos[b] == 1) != (pos[p] == 1):
                continue
            trial = [b if q == p else q for q in xi]
            if _valid(trial, pos):
                xi = trial
                used.add(b)
                break
    return xi


def score_week(
    plan: WeekPlan,
    pts: dict[int, int],
    mins: dict[int, int],
    pos: dict[int, int],
) -> tuple[int, int, int]:
    """Points for the week (before hits), captain's points counted once, bench points."""
    if plan.chip == "bboost":
        xi = list(plan.squad)
    else:
        xi = auto_subs(plan.starters, plan.bench, mins, pos)
    total = sum(pts.get(p, 0) for p in xi)
    mult = 3 if plan.chip == "3xc" else 2
    cap = plan.captain if mins.get(plan.captain, 0) > 0 else (plan.vice if mins.get(plan.vice, 0) > 0 else None)
    cap_pts = pts.get(cap, 0) if cap is not None else 0
    total += (mult - 1) * cap_pts
    bench_pts = sum(pts.get(p, 0) for p in plan.squad if p not in xi)
    return total, cap_pts, bench_pts


Decider = Callable[[int, Team, dict[int, int]], WeekPlan]


def run_season(
    pm: pd.DataFrame,
    pos: dict[int, int],
    prices_at: Callable[[int], dict[int, int]],
    decide: Decider,
    gws: list[int],
    *,
    team: Team | None = None,
    ft_topups: dict[int, int] | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[list[GWResult], Team]:
    team = team or Team.fresh()
    results: list[GWResult] = []
    for gw in gws:
        if ft_topups and gw in ft_topups:
            team.ft = max(team.ft, ft_topups[gw])
        prices = prices_at(gw)
        plan = decide(gw, team, prices)
        initial = len(team.squad) == 0
        chip = plan.chip
        if chip:
            h = half_of(gw)
            if not team.chips_left.get((chip, h), False):
                raise RuntimeError(f"GW{gw}: chip {chip} not available")
        backup = (list(team.squad), dict(team.purchase), team.bank) if chip == "freehit" else None
        if initial:
            team.squad = list(plan.squad)
            team.purchase = {p: prices[p] for p in team.squad}
            team.bank = 1000 - sum(team.purchase.values())
            n_tr = 0
        else:
            target = list(plan.squad)
            outs = [p for p in team.squad if p not in target]
            ins = [p for p in target if p not in team.squad]
            income = sum(sell_price(team.purchase[p], prices.get(p, team.purchase[p])) for p in outs)
            spend = sum(prices[p] for p in ins)
            team.bank += income - spend
            for p in outs:
                team.purchase.pop(p, None)
            for p in ins:
                team.purchase[p] = prices[p]
            team.squad = target
            n_tr = len(ins)
        if team.bank < 0:
            raise RuntimeError(f"GW{gw}: bank negative ({team.bank})")
        if len(team.squad) != 15:
            raise RuntimeError(f"GW{gw}: squad has {len(team.squad)} players")
        if not initial and chip not in ("wildcard", "freehit"):
            hits = max(0, n_tr - team.ft)
            team.ft = min(5, team.ft - (n_tr - hits) + 1)
        else:
            hits = 0
            if initial:
                team.ft = 1
        if chip:
            team.chips_left[(chip, half_of(gw))] = False
        pts, mins = gw_actuals(pm, gw)
        total, cap_pts, bench_pts = score_week(plan, pts, mins, pos)
        total -= 4 * hits
        results.append(
            GWResult(
                gw,
                total,
                chip,
                n_tr,
                hits,
                plan.captain,
                cap_pts,
                bench_pts,
                plan.xp,
                [p for p in plan.transfers_in],
                [p for p in plan.transfers_out],
                list(plan.starters),
                list(plan.squad),
            )
        )
        if log:
            log(
                f"GW{gw:>2} {total:>4} pts  chip={chip or '-':<8} transfers={n_tr} hits={hits} "
                f"ft_next={team.ft} bank={team.bank / 10:.1f}"
            )
        if backup is not None:
            team.squad, team.purchase, team.bank = backup
    return results, team
