"""Multi-week transfer + chip planner (one MIP over the next H gameweeks).

The plan for week 1 is what we recommend; weeks 2..H exist so that this week's move is the
first step of a good sequence, not a one-week greedy swap. The model knows:

* free-transfer banking (up to 5 banked; a WC/FH week keeps the bank but adds none), -4 per extra
* sell prices for owned players, bank carried week to week, 3 per club, 2/5/5/3, formations
* all four chips, each at most once per half (GW1-19 / GW20-38), one chip per week,
  Free Hit = a separate one-week squad, Wildcard = free transfers that week,
  Bench Boost = bench counts, Triple Captain = captain counts 3x
* a chip left over after the horizon keeps an "option value" until its half ends, after
  which it is worth nothing (so expiring first-half chips get used).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pulp

SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
CHIPS = ("wildcard", "freehit", "bboost", "3xc")
HALF_END = 19
LAST_GW = 38


def chip_window(chip: str, half: int) -> tuple[int, int]:
    first = 2 if chip in ("wildcard", "freehit") else 1
    return (first, HALF_END) if half == 1 else (HALF_END + 1, LAST_GW)


def half_of(gw: int) -> int:
    return 1 if gw <= HALF_END else 2


@dataclass
class PlannerConfig:
    horizon: int = 6
    decay: float = 0.84  # weight of week k is decay**k (projection uncertainty grows)
    bench_weight: tuple[float, float, float, float] = (0.03, 0.22, 0.09, 0.04)  # GK, out1..3
    ft_value: float = 1.6  # value per banked FT carried past the horizon
    max_transfers: int = 4  # per non-WC week
    max_hits_first_week: int = 1
    hit_cost: float = 4.0
    hit_margin: float = 2.0  # a -4 must beat the plan without it by this much on top
    # A chip not used inside the horizon is worth E[best of the r GWs left in its half after
    # the horizon] = mean + sd * E[max of r standard normals]; 0 once the half is over.
    chip_mean: dict[str, float] = field(
        default_factory=lambda: {"wildcard": 9.0, "freehit": 3.5, "bboost": 6.0, "3xc": 6.5}
    )
    chip_sd: dict[str, float] = field(
        default_factory=lambda: {"wildcard": 5.0, "freehit": 3.0, "bboost": 2.0, "3xc": 1.5}
    )
    # Second half: blanks/doubles are announced late and BB/TC/FH are worth more in them.
    second_half_multiplier: dict[str, float] = field(
        default_factory=lambda: {"wildcard": 1.0, "freehit": 1.6, "bboost": 1.6, "3xc": 1.4}
    )
    chip_prep_bonus: dict[str, float] = field(default_factory=lambda: {"bboost": 1.5, "3xc": 0.5})
    chip_margin: float = 0.0
    transfer_friction: float = 0.4  # per transfer: projections are noisy, a swap must clear this
    # Transfers are planned in the first `transfer_weeks` GWs; after that the squad is held
    # (lineups, captains and BB/TC still re-optimised every week of the horizon). Keeps the MIP
    # small enough to solve in seconds while still seeing "roll now, two next week".
    transfer_weeks: int = 2
    min_start_prob: float = 0.35  # cannot start a player below this P(start)
    pool_per_pos: dict[int, int] = field(default_factory=lambda: {1: 10, 2: 32, 3: 38, 4: 20})
    time_limit: float = 90.0
    threads: int | None = None
    gap: float = 0.003
    lambda_var: float = 0.0  # optional mean-variance penalty on starters' variance


@dataclass
class SquadState:
    squad: list[int]  # 15 element ids (empty = pre-GW1, unlimited changes)
    sell: dict[int, int]  # sell price (tenths) per owned player
    bank: int
    ft: int
    chips_left: dict[tuple[str, int], bool]  # (chip, half) -> still available

    @classmethod
    def fresh(cls) -> SquadState:
        return cls([], {}, 1000, 1, {(c, h): True for c in CHIPS for h in (1, 2)})


@dataclass
class WeekPlan:
    gw: int
    chip: str | None
    squad: list[int]  # squad that plays this GW (FH squad on a Free Hit)
    transfers_in: list[int]
    transfers_out: list[int]
    hits: int
    starters: list[int]
    bench: list[int]  # FPL order: GK first, then outfield auto-sub order
    captain: int
    vice: int
    xp: float  # expected points this GW incl. captain/chip, minus hits
    ft_after: int


@dataclass
class Plan:
    weeks: list[WeekPlan]
    objective: float
    status: str
    notes: list[str] = field(default_factory=list)
    baseline_xp: float | None = None  # roll everything: same squad, best XI

    @property
    def first(self) -> WeekPlan:
        return self.weeks[0]


def expected_max_normal(r: int) -> float:
    """E[max of r iid standard normals] (0 for r <= 1)."""
    if r <= 1:
        return 0.0
    zs = np.linspace(-6, 6, 2401)
    pdf = np.exp(-zs**2 / 2) / math.sqrt(2 * math.pi)
    cdf = np.cumsum(pdf) * (zs[1] - zs[0])
    dens = r * pdf * np.clip(cdf, 0, 1) ** (r - 1)
    return float(np.sum(zs * dens) * (zs[1] - zs[0]))


def chip_option_value(chip: str, half: int, weeks_after: int, cfg: "PlannerConfig") -> float:
    if weeks_after <= 0:
        return 0.0
    mean = cfg.chip_mean.get(chip, 0.0)
    sd = cfg.chip_sd.get(chip, 0.0)
    if half == 2:
        mult = cfg.second_half_multiplier.get(chip, 1.0)
        mean, sd = mean * mult, sd * mult
    return mean + sd * expected_max_normal(weeks_after)


try:  # HiGHS via highspy: fast, and we can hand it a feasible starting plan.
    import highspy

    class _WarmHiGHS(pulp.HiGHS):
        def callSolver(self, lp):
            n = lp.solverModel.getNumCol()
            col = [0.0] * n
            have = False
            for var in lp.variables():
                if var.varValue is not None and getattr(var, "index", None) is not None:
                    col[var.index] = float(var.varValue)
                    have = True
            if have:
                try:
                    sol = highspy.HighsSolution()
                    sol.col_value = col
                    lp.solverModel.setSolution(sol)
                except Exception:
                    pass
            lp.solverModel.run()

except Exception:  # pragma: no cover
    highspy = None
    _WarmHiGHS = None


_HEUR = [0.3]


def _solver(time_limit: float, gap: float, threads: int | None = None):
    if _WarmHiGHS is not None:
        try:
            solver = _WarmHiGHS(msg=False, timeLimit=time_limit, gapRel=gap, threads=threads, mip_heuristic_effort=_HEUR[0])
            if solver.available():
                return solver
        except Exception:
            pass
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=gap, warmStart=True)


def select_pool(
    xp: pd.DataFrame,
    pos: pd.Series,
    price: pd.Series,
    keep: Iterable[int],
    per_pos: dict[int, int],
    weights: np.ndarray,
    team: pd.Series | None = None,
) -> list[int]:
    keep = set(int(k) for k in keep)
    score = xp.to_numpy() @ weights
    s = pd.Series(score, index=xp.index)
    first = xp.iloc[:, 0]
    chosen = set(keep)
    for p, n in per_pos.items():
        ids = pos.index[pos == p]
        if len(ids) == 0:
            continue
        chosen.update(s[ids].nlargest(n).index)
        chosen.update(first[ids].nlargest(max(4, n // 4)).index)
        # Value picks and cheap enablers, so the budget can balance.
        value = s[ids] / np.maximum(price[ids] / 10.0 - 3.5, 0.5)
        chosen.update(value.nlargest(max(4, n // 3)).index)
        cheap = price[ids].sort_values().index
        cheap_good = s[cheap][price[cheap] <= price[ids].min() + 5].nlargest(3).index
        chosen.update(cheap_good)
    if team is not None:
        # Every club's best two, so the 3-per-club rule can always be met.
        for _club, ids in team.groupby(team).groups.items():
            ids = list(ids)
            chosen.update(s[ids].nlargest(2).index)
            for p in (2, 3):  # 5 DEF + 5 MID need at least 4 clubs each
                sub = [i for i in ids if pos.get(i) == p]
                if sub:
                    chosen.update(s[sub].nlargest(1).index)
    return sorted(int(c) for c in chosen)


def plan_weeks(
    xp: pd.DataFrame,
    players: pd.DataFrame,
    state: SquadState,
    gw: int,
    cfg: PlannerConfig | None = None,
    *,
    p_start: pd.Series | None = None,
    var: pd.DataFrame | None = None,
    forced_chip: str | None = None,
    allow_chips: bool = True,
    after_horizon: dict[str, float] | None = None,
    chip_weeks: dict[str, list[int]] | None = None,
    option_values: bool = True,
) -> Plan:
    """xp: index element, columns = gameweeks (ints, consecutive from `gw`).

    players: index element with columns pos, team, price (buy price, tenths).
    """
    cfg = cfg or PlannerConfig()
    weeks = [w for w in xp.columns if w >= gw][: cfg.horizon]
    xp = xp[weeks].fillna(0.0)
    disc = np.array([cfg.decay**k for k in range(len(weeks))])
    pos = players.pos.astype(int)
    price = players.price.astype(int)
    team = players.team.astype(int)
    initial = len(state.squad) == 0
    pool = select_pool(
        xp, pos.reindex(xp.index), price.reindex(xp.index), state.squad, cfg.pool_per_pos, disc, team.reindex(xp.index)
    )
    pool = [p for p in pool if p in players.index]
    P = pool
    W = weeks
    owned = set(state.squad)
    buy = {p: int(price[p]) for p in P}
    sellp = {p: int(state.sell.get(p, buy[p])) if p in owned else buy[p] for p in P}
    X = {(p, w): float(xp.at[p, w]) for p in P for w in W}
    V = {(p, w): float(var.at[p, w]) if var is not None and w in var.columns else 0.0 for p in P for w in W}
    ps = p_start if p_start is not None else pd.Series(1.0, index=xp.index)
    can_start = {p: float(ps.get(p, 1.0)) >= cfg.min_start_prob for p in P}

    prob = pulp.LpProblem("fpl_plan", pulp.LpMaximize)
    B = pulp.LpBinary
    # Only squad membership (and chip/FH choices) must be integer: given an integer squad the
    # lineup, captain and transfer variables have integral optima (interval constraints).
    x = pulp.LpVariable.dicts("x", (P, W), cat=B)
    s = pulp.LpVariable.dicts("s", (P, W), lowBound=0, upBound=1)
    c = pulp.LpVariable.dicts("c", (P, W), lowBound=0, upBound=1)
    bv = pulp.LpVariable.dicts("bench", (P, W), lowBound=0, upBound=1)
    tin = pulp.LpVariable.dicts("tin", (P, W), lowBound=0, upBound=1)
    tout = pulp.LpVariable.dicts("tout", (P, W), lowBound=0, upBound=1)
    bank = pulp.LpVariable.dicts("bank", W, lowBound=0)
    ft = pulp.LpVariable.dicts("ft", W + ["end"], lowBound=0, upBound=5, cat="Integer")
    fu = pulp.LpVariable.dicts("ftused", W, lowBound=0, cat="Integer")
    hit = pulp.LpVariable.dicts("hit", W, lowBound=0, cat="Integer")
    T = max(1, min(cfg.transfer_weeks, len(W)))
    for w in W[T:]:
        for p in P:
            x[p][w] = x[p][W[T - 1]]
        bank[w] = bank[W[T - 1]]

    # ---------------- chips
    chip_vars: dict[str, dict[int, Any]] = {}
    for chip in CHIPS:
        chip_vars[chip] = {}
        for w in W:
            h = half_of(w)
            lo, hi = chip_window(chip, h)
            usable = allow_chips and state.chips_left.get((chip, h), False) and lo <= w <= hi
            if initial and chip in ("wildcard", "freehit"):
                usable = False
            if chip_weeks is not None:
                usable = usable and w in chip_weeks.get(chip, [])
            if forced_chip is not None and chip == forced_chip:
                usable = usable and w == W[0]
            if usable:
                chip_vars[chip][w] = pulp.LpVariable(f"chip_{chip}_{w}", cat=B)
    for chip in CHIPS:
        for h in (1, 2):
            vs = [v for w, v in chip_vars[chip].items() if half_of(w) == h]
            if vs:
                prob += pulp.lpSum(vs) <= 1
    if forced_chip is not None:
        if W[0] in chip_vars.get(forced_chip, {}):
            prob += chip_vars[forced_chip][W[0]] == 1
    for w in W:
        vs = [chip_vars[ch][w] for ch in CHIPS if w in chip_vars[ch]]
        if len(vs) > 1:
            prob += pulp.lpSum(vs) <= 1

    def chipv(chip: str, w: int):
        return chip_vars[chip].get(w, 0)

    # Free Hit squad (only exists in weeks where FH is possible).
    fh_weeks = list(chip_vars["freehit"].keys())
    y = pulp.LpVariable.dicts("y", (P, fh_weeks), cat=B) if fh_weeks else {}
    tc = pulp.LpVariable.dicts("tc", (P, list(chip_vars["3xc"].keys())), lowBound=0, upBound=1) if chip_vars["3xc"] else {}
    bb_weeks = list(chip_vars["bboost"].keys())
    bbv = pulp.LpVariable.dicts("bbv", (P, bb_weeks), lowBound=0, upBound=1) if bb_weeks else {}

    clubs = sorted({int(team[p]) for p in P})
    big = 15
    for i, w in enumerate(W):
        prev_w = W[i - 1] if i > 0 else None
        fhw = chipv("freehit", w)
        wcw = chipv("wildcard", w)
        if i < T:
            _transfer_week(prob, i, w, W, T, P, x, tin, tout, bank, ft, fu, hit, pos, team, clubs, owned, buy, sellp,
                           state, initial, cfg, wcw, fhw, fh_weeks, big)
        # lineup
        _lineup_week(prob, w, P, x, s, c, bv, y, tc, bbv, pos, can_start, X, fh_weeks, bb_weeks, chip_vars, chipv, fhw)
        if w in fh_weeks:
            _fh_squad(prob, w, prev_w, P, y, pos, team, clubs, buy, sellp, owned, state, bank, x, fhw)


    # ---------------- objective
    terms = []
    bw = cfg.bench_weight
    for i, w in enumerate(W):
        d = disc[i]
        for p in P:
            terms.append(d * X[p, w] * (s[p][w] + c[p][w]))
            # Bench value: auto-sub insurance. Weight by position (GK sub rarely plays).
            wgt = bw[0] if pos[p] == 1 else sum(bw[1:]) / 3.0
            terms.append(d * wgt * X[p, w] * bv[p][w])
            if w in bb_weeks:
                terms.append(d * (1.0 - wgt) * X[p, w] * bbv[p][w])
            if w in chip_vars["3xc"]:
                terms.append(d * X[p, w] * tc[p][w])
            if cfg.lambda_var > 0:
                terms.append(-d * cfg.lambda_var * V[p, w] * s[p][w])
        if i < T:
            terms.append(-d * (cfg.hit_cost + cfg.hit_margin) * hit[w])
            terms.append(-d * cfg.transfer_friction * (fu[w] + hit[w]))
    terms.append(cfg.ft_value * disc[T - 1] * ft["end"])
    # Chips not used in the horizon keep an option value until their half ends.
    # Only as many leftover chips as there are GWs left in the half after the horizon can
    # still be played (one chip per GW); the rest expire worthless.
    last = W[-1]
    for h in ((1, 2) if option_values else ()):
        leftovers = []
        opts = []
        for chip in CHIPS:
            if not state.chips_left.get((chip, h), False):
                continue
            lo, hi = chip_window(chip, h)
            if hi < gw:
                continue
            after = max(0, hi - max(last, lo - 1))
            base = chip_option_value(chip, h, after, cfg)
            if after_horizon and chip in after_horizon:
                base = after_horizon[chip]
            used = pulp.lpSum(v for w, v in chip_vars[chip].items() if half_of(w) == h)
            left = 1 - used
            opt = base * disc[-1]
            terms.append(opt * left)
            leftovers.append(left)
            opts.append(opt)
        if not leftovers:
            continue
        half_hi = HALF_END if h == 1 else LAST_GW
        room = max(0, half_hi - max(last, gw - 1))
        lost = pulp.LpVariable(f"lost_{h}", lowBound=0)
        prob += lost >= pulp.lpSum(leftovers) - room
        terms.append(-max(opts) * lost)
    prob += pulp.lpSum(terms)

    if not initial:
        _warm_start(prob, P, W, owned, X, pos, can_start, state, x, s, c, bv, tin, tout, bank, ft, fu, hit, cfg, gw)
    status = prob.solve(_solver(cfg.time_limit, cfg.gap, cfg.threads))
    st = pulp.LpStatus[status]
    if st not in ("Optimal",) and pulp.value(prob.objective) is None:
        raise RuntimeError(f"planner MIP failed: {st}")

    def val(v) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        out = v.value()
        return float(out) if out is not None else 0.0

    plans: list[WeekPlan] = []
    for i, w in enumerate(W):
        chip = next((ch for ch in CHIPS if val(chipv(ch, w)) > 0.5), None)
        squad_main = [p for p in P if val(x[p][w]) > 0.5]
        squad = [p for p in P if val(y[p][w]) > 0.5] if chip == "freehit" else squad_main
        if len(squad) != 15:
            raise RuntimeError(f"planner MIP returned no integer squad ({st}, GW{w})")
        starters = [p for p in squad if val(s[p][w]) > 0.5]
        xp_w = {p: X[p, w] for p in P}
        if len(starters) != 11:
            starters, _, _ = best_lineup(squad, xp_w, pos)
        cap = max(starters, key=lambda p: (round(val(c[p][w]), 3), X[p, w]))
        others = sorted([p for p in starters if p != cap], key=lambda p: -X[p, w])
        vice = others[0] if others else cap
        bench = order_bench(squad, starters, {p: X[p, w] for p in P}, pos)
        t_in = [p for p in P if i < T and val(tin[p][w]) > 0.5]
        t_out = [p for p in P if i < T and val(tout[p][w]) > 0.5]
        mult = 3.0 if chip == "3xc" else 2.0
        exp_pts = sum(X[p, w] for p in starters) + (mult - 1.0) * X[cap, w]
        if chip == "bboost":
            exp_pts += sum(X[p, w] for p in bench)
        h = int(round(val(hit[w]))) if i < T else 0
        nxt = ft["end"] if i >= T - 1 else ft[W[i + 1]]
        plans.append(
            WeekPlan(
                gw=w,
                chip=chip,
                squad=squad,
                transfers_in=t_in,
                transfers_out=t_out,
                hits=h,
                starters=starters,
                bench=bench,
                captain=cap,
                vice=vice,
                xp=exp_pts - cfg.hit_cost * h,
                ft_after=int(round(val(nxt))),
            )
        )
    return Plan(plans, float(pulp.value(prob.objective) or 0.0), st)


def _transfer_week(prob, i, w, W, T, P, x, tin, tout, bank, ft, fu, hit, pos, team, clubs, owned, buy, sellp,
                   state, initial, cfg, wcw, fhw, fh_weeks, big) -> None:
    prev_w = W[i - 1] if i > 0 else None
    prob += pulp.lpSum(x[p][w] for p in P) == 15
    for k, n in SQUAD.items():
        prob += pulp.lpSum(x[p][w] for p in P if pos[p] == k) == n
    for cl in clubs:
        prob += pulp.lpSum(x[p][w] for p in P if team[p] == cl) <= 3
    for p in P:
        before = (1 if p in owned else 0) if prev_w is None else x[p][prev_w]
        prob += x[p][w] == before + tin[p][w] - tout[p][w]
        prob += tin[p][w] + tout[p][w] <= 1
    n_in = pulp.lpSum(tin[p][w] for p in P)
    spend = pulp.lpSum(buy[p] * tin[p][w] for p in P)
    income = pulp.lpSum(sellp[p] * tout[p][w] for p in P)
    before_bank = state.bank if prev_w is None else bank[prev_w]
    if initial and i == 0:
        prob += bank[w] == 1000 - pulp.lpSum(buy[p] * x[p][w] for p in P)
    else:
        prob += bank[w] == before_bank + income - spend
    ft_now = state.ft if i == 0 else ft[w]
    nxt = ft["end"] if i == T - 1 else ft[W[i + 1]]
    if i == 0:
        prob += ft[w] == state.ft
    if initial and i == 0:
        prob += fu[w] == 0
        prob += hit[w] == 0
        prob += nxt <= 1
        return
    prob += n_in <= fu[w] + hit[w] + big * wcw + big * fhw
    prob += fu[w] <= ft_now
    # FTs are kept through a WC/FH week, but that week does not add one.
    prob += nxt <= ft_now - fu[w] + 1 - wcw - fhw
    prob += n_in <= cfg.max_transfers + big * wcw
    if fh_weeks and w in fh_weeks:
        prob += n_in <= big * (1 - fhw)
    prob += hit[w] <= (cfg.max_hits_first_week if i == 0 else 1)


def _lineup_week(prob, w, P, x, s, c, bv, y, tc, bbv, pos, can_start, X, fh_weeks, bb_weeks, chip_vars, chipv, fhw) -> None:
    prob += pulp.lpSum(s[p][w] for p in P) == 11
    prob += pulp.lpSum(s[p][w] for p in P if pos[p] == 1) == 1
    prob += pulp.lpSum(s[p][w] for p in P if pos[p] == 2) >= 3
    prob += pulp.lpSum(s[p][w] for p in P if pos[p] == 4) >= 1
    prob += pulp.lpSum(c[p][w] for p in P) == 1
    for p in P:
        prob += c[p][w] <= s[p][w]
        if not can_start[p] and X[p, w] < 1.0:
            prob += s[p][w] == 0
        if w in fh_weeks:
            prob += s[p][w] <= x[p][w] + fhw
            prob += s[p][w] <= y[p][w] + 1 - fhw
            prob += y[p][w] <= fhw
            prob += bv[p][w] <= 1 - fhw
        else:
            prob += s[p][w] <= x[p][w]
        prob += bv[p][w] <= x[p][w]
        prob += bv[p][w] <= 1 - s[p][w]
        if w in bb_weeks:
            prob += bbv[p][w] <= bv[p][w]
            prob += bbv[p][w] <= chipv("bboost", w)
        if w in chip_vars["3xc"]:
            prob += tc[p][w] <= c[p][w]
    if w in chip_vars["3xc"]:
        prob += pulp.lpSum(tc[p][w] for p in P) == chipv("3xc", w)


def _fh_squad(prob, w, prev_w, P, y, pos, team, clubs, buy, sellp, owned, state, bank, x, fhw) -> None:
    prob += pulp.lpSum(y[p][w] for p in P) == 15 * fhw
    for k, n in SQUAD.items():
        prob += pulp.lpSum(y[p][w] for p in P if pos[p] == k) == n * fhw
    for cl in clubs:
        prob += pulp.lpSum(y[p][w] for p in P if team[p] == cl) <= 3
    value_before = (
        state.bank + sum(sellp[p] for p in owned if p in sellp)
        if prev_w is None
        else bank[prev_w] + pulp.lpSum(sellp[p] * x[p][prev_w] for p in P)
    )
    prob += pulp.lpSum(buy[p] * y[p][w] for p in P) <= value_before + 2000 * (1 - fhw)


def _warm_start(prob, P, W, owned, X, pos, can_start, state, x, s, c, bv, tin, tout, bank, ft, fu, hit, cfg, gw) -> None:
    """Feasible start: keep the current 15, best XI each week, bank the FTs."""
    for v in prob.variables():
        v.setInitialValue(0.0)
    squad = [p for p in P if p in owned]
    if len(squad) != 15:
        return
    ft_w = state.ft
    for i, w in enumerate(W):
        xp_w = {p: (X[p, w] if can_start[p] or X[p, w] >= 1.0 else -50.0) for p in squad}
        xi, cap, _ = best_lineup(squad, xp_w, pos)
        for p in P:
            x[p][w].setInitialValue(1.0 if p in owned else 0.0)
            s[p][w].setInitialValue(1.0 if p in xi else 0.0)
            c[p][w].setInitialValue(1.0 if p == cap else 0.0)
            bv[p][w].setInitialValue(1.0 if (p in owned and p not in xi) else 0.0)
        if i < max(1, min(cfg.transfer_weeks, len(W))):
            bank[w].setInitialValue(float(state.bank))
            ft[w].setInitialValue(float(ft_w))
            ft_w = min(5, ft_w + 1)
    ft["end"].setInitialValue(float(ft_w))
    for v in prob.variables():
        if v.name.startswith("lost_"):
            h = int(v.name.split("_")[1])
            half_hi = HALF_END if h == 1 else LAST_GW
            n_left = sum(1 for (ch, hh), ok in state.chips_left.items() if ok and hh == h and chip_window(ch, h)[1] >= gw)
            room = max(0, half_hi - max(W[-1], gw - 1))
            v.setInitialValue(float(max(0, n_left - room)))


def order_bench(squad: list[int], starters: list[int], xp_w: dict[int, float], pos: pd.Series) -> list[int]:
    rest = [p for p in squad if p not in starters]
    gks = [p for p in rest if int(pos[p]) == 1]
    out = sorted([p for p in rest if int(pos[p]) != 1], key=lambda p: -xp_w.get(p, 0.0))
    return gks + out


def best_lineup(squad: list[int], xp_w: dict[int, float], pos: pd.Series) -> tuple[list[int], int, int]:
    """Best XI from a fixed 15 for one week (no MIP needed): returns starters, captain, vice."""
    by_pos = {k: sorted([p for p in squad if int(pos[p]) == k], key=lambda p: -xp_w.get(p, 0.0)) for k in SQUAD}
    best: tuple[float, list[int]] | None = None
    for d in (3, 4, 5):
        for f in (1, 2, 3):
            m = 10 - d - f
            if m < 2 or m > 5:
                continue
            xi = by_pos[1][:1] + by_pos[2][:d] + by_pos[3][:m] + by_pos[4][:f]
            if len(xi) != 11:
                continue
            tot = sum(xp_w.get(p, 0.0) for p in xi)
            if best is None or tot > best[0]:
                best = (tot, xi)
    xi = best[1] if best else squad[:11]
    ranked = sorted(xi, key=lambda p: -xp_w.get(p, 0.0))
    return xi, ranked[0], ranked[1] if len(ranked) > 1 else ranked[0]


def _future_chip_gains(
    xp_full: pd.DataFrame,
    plan: Plan,
    pos: pd.Series,
    gw: int,
    hi: int,
) -> dict[str, dict[int, float]]:
    """Projected TC / BB gain in each later GW of the half, holding the base plan's squads."""
    out: dict[str, dict[int, float]] = {"3xc": {}, "bboost": {}}
    squads = {wp.gw: wp.squad for wp in plan.weeks}
    last_squad = plan.weeks[-1].squad
    for w in range(gw + 1, hi + 1):
        if w not in xp_full.columns:
            continue
        squad = squads.get(w, last_squad)
        xp_w = {p: float(xp_full.at[p, w]) if p in xp_full.index else 0.0 for p in squad}
        xi, cap, _ = best_lineup(squad, xp_w, pos)
        out["3xc"][w] = xp_w[cap]
        out["bboost"][w] = sum(v for p, v in xp_w.items() if p not in xi)
    return out


def hold_value(
    chip: str,
    gw: int,
    cfg: PlannerConfig,
    future: dict[int, float] | None,
    rng: np.random.Generator,
) -> tuple[float, int | None]:
    """Value of keeping `chip` past this GW: E[best gain over the GWs left in its half]."""
    h = half_of(gw)
    lo, hi = chip_window(chip, h)
    r = max(0, hi - max(gw, lo - 1))
    if r <= 0:
        return 0.0, None
    prior = chip_option_value(chip, h, r, cfg)
    if not future:
        return prior, None
    weeks = sorted(future)
    base = np.array([future[w] for w in weeks]) + cfg.chip_prep_bonus.get(chip, 0.0)
    sd = cfg.chip_sd.get(chip, 1.5) * (1.0 + 0.08 * np.arange(1, len(weeks) + 1))
    draws = base[None, :] + rng.normal(0.0, 1.0, (800, len(weeks))) * sd[None, :]
    best = draws.max(axis=1)
    value = float(best.mean())
    best_w = weeks[int(np.bincount(draws.argmax(axis=1), minlength=len(weeks)).argmax())]
    if h == 2:
        # Doubles are announced late: never value a second-half chip below the DGW prior.
        value = max(value, prior)
    return value, best_w


def plan_gameweek(
    xp: pd.DataFrame,
    players: pd.DataFrame,
    state: SquadState,
    gw: int,
    cfg: PlannerConfig | None = None,
    *,
    p_start: pd.Series | None = None,
    var: pd.DataFrame | None = None,
    xp_full: pd.DataFrame | None = None,
) -> Plan:
    """Transfers from the multi-week MIP; chips by optimal stopping.

    gain(chip) = plan value with the chip played this GW - plan value without it.
    hold(chip) = expected best gain over the GWs left in the chip's half (TC/BB from our own
    projections of later weeks plus noise; WC/FH from a prior). Play the chip with the largest
    gain - hold > 0 (one chip per GW). If chips left in the half outnumber the GWs left, the
    best one is played now even at a small loss, because otherwise one expires unused.
    """
    cfg = cfg or PlannerConfig()
    xp_full = xp if xp_full is None else xp_full
    h = half_of(gw)
    kw = dict(p_start=p_start, var=var, option_values=False)
    none: dict[str, list[int]] = {}
    base = plan_weeks(xp, players, state, gw, cfg, chip_weeks=none, **kw)
    base.baseline_xp = base.first.xp
    rng = np.random.default_rng(gw * 7919 + 1)
    pos = players.pos.astype(int)
    live = [
        ch
        for ch in CHIPS
        if state.chips_left.get((ch, h), False)
        and chip_window(ch, h)[0] <= gw <= chip_window(ch, h)[1]
        and not (not state.squad and ch in ("wildcard", "freehit"))
    ]
    if not live:
        base.notes.insert(0, "No chip available this GW.")
        return base
    hi_half = HALF_END if h == 1 else LAST_GW
    future = _future_chip_gains(xp_full, base, pos, gw, hi_half)
    scored = []
    for ch in live:
        try:
            alt = plan_weeks(xp, players, state, gw, cfg, chip_weeks={ch: [gw]}, forced_chip=ch, **kw)
        except RuntimeError:
            continue
        gain = alt.objective - base.objective
        hold, when = hold_value(ch, gw, cfg, future.get(ch), rng)
        scored.append((ch, alt, gain, hold, when))
    n_left = sum(1 for (ch, hh), ok in state.chips_left.items() if ok and hh == h and chip_window(ch, h)[1] >= gw)
    weeks_left = hi_half - gw + 1
    must_play = n_left >= weeks_left and n_left > 0
    best = None
    if scored:
        top = max(scored, key=lambda t: t[2] - t[3])
        if top[2] - top[3] > cfg.chip_margin or must_play:
            best = top
    names = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
    notes = []
    for ch, alt, gain, hold, when in sorted(scored, key=lambda t: -(t[2] - t[3])):
        verdict = "PLAY" if best is not None and ch == best[0] else "HOLD"
        when_txt = f", best later GW{when}" if when else ""
        notes.append(
            f"{verdict} {names[ch]}: +{gain:.1f} xP if played now vs +{hold:.1f} expected "
            f"from keeping it (GW{gw + 1}-{hi_half}{when_txt})."
        )
    if must_play and best is not None:
        notes.append(f"{n_left} chips left and {weeks_left} GWs left in this half: one has to go now.")
    chosen = best[1] if best is not None else base
    chosen.baseline_xp = base.first.xp
    chosen.notes = notes + chosen.notes
    return chosen
