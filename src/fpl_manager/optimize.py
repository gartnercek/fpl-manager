from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pulp

from fpl_manager.models.aggregate import PlayerPrediction

BUDGET_TENTHS = 1000
SQUAD_COUNTS = {1: 2, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3
BENCH_WEIGHT = 0.1
SOLVER_TIME = 60


@dataclass
class OptPlayer:
    id: int
    name: str
    team_id: int
    position: int
    now_cost: int
    sell_cost: int
    mu: float
    sigma2: float
    mu_h3: float
    p_play: float
    mu_by_gw: dict[int, float] = field(default_factory=dict)
    sigma2_by_gw: dict[int, float] = field(default_factory=dict)

    @classmethod
    def from_pred(cls, pred: PlayerPrediction, sell_cost: int | None = None) -> OptPlayer:
        return cls(
            id=pred.player_id,
            name=pred.web_name,
            team_id=pred.team_id,
            position=pred.position,
            now_cost=pred.now_cost,
            sell_cost=sell_cost if sell_cost is not None else pred.now_cost,
            mu=pred.mu,
            sigma2=pred.sigma2,
            mu_h3=pred.mu_horizon.get(3, pred.mu),
            p_play=pred.minutes.p_play,
            mu_by_gw=dict(pred.mu_by_gw),
            sigma2_by_gw=dict(pred.sigma2_by_gw),
        )

    def mu_at(self, week: int) -> float:
        if self.mu_by_gw:
            return float(self.mu_by_gw.get(week, 0.0))
        return self.mu

    def var_at(self, week: int) -> float:
        if self.sigma2_by_gw:
            return float(self.sigma2_by_gw.get(week, self.sigma2))
        return self.sigma2


@dataclass
class Plan:
    mode: str
    transfers: int
    hit_cost: int
    owned: list[int]
    starters: list[int]
    bench: list[int]
    captain: int
    vice: int
    xi_mu: float
    xi_sd: float
    objective: float
    horizon3: float
    cost: int
    bank: int
    notes: list[str] = field(default_factory=list)
    in_ids: list[int] = field(default_factory=list)
    out_ids: list[int] = field(default_factory=list)


def _cfg_float(cfg: dict[str, Any], key: str, default: float) -> float:
    raw = cfg.get(key, default)
    if raw is None:
        return default
    return float(raw)


def sell_price(purchase: int, now: int) -> int:
    if now <= purchase:
        return now
    return purchase + (now - purchase) // 2


def _solver():
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=SOLVER_TIME, options=["randomSeed", "1"])


def solve_squad(
    players: list[OptPlayer],
    *,
    lambda_var: float,
    current_ids: set[int] | None = None,
    transfers: int | None = None,
    bank: int = 0,
    budget: int = BUDGET_TENTHS,
    weeks: list[int] | None = None,
    discount: float = 1.0,
    fifth_defender_penalty: float = 1.2,
    one_forward_penalty: float = 0.8,
    min_start_p_play: float = 0.55,
) -> Plan | None:
    """15-man squad. XI and captain are chosen independently each week in `weeks`."""
    ids = [p.id for p in players]
    by_id = {p.id: p for p in players}
    if weeks:
        week_ids = list(weeks)
    else:
        week_ids = sorted({w for p in players for w in p.mu_by_gw}) or [0]
    w0 = week_ids[0]

    own = {pid: pulp.LpVariable(f"own_{pid}", cat="Binary") for pid in ids}
    start = {
        pid: {w: pulp.LpVariable(f"st_{pid}_{w}", cat="Binary") for w in week_ids} for pid in ids
    }
    cap = {pid: {w: pulp.LpVariable(f"cap_{pid}_{w}", cat="Binary") for w in week_ids} for pid in ids}
    vice = {pid: pulp.LpVariable(f"vice_{pid}", cat="Binary") for pid in ids}
    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)

    for pid in ids:
        for w in week_ids:
            prob += start[pid][w] <= own[pid]
            prob += cap[pid][w] <= start[pid][w]
        prob += vice[pid] <= start[pid][w0]
        prob += cap[pid][w0] + vice[pid] <= 1

    prob += pulp.lpSum(own[pid] for pid in ids) == 15
    for w in week_ids:
        prob += pulp.lpSum(start[pid][w] for pid in ids) == 11
        prob += pulp.lpSum(cap[pid][w] for pid in ids) == 1
    prob += pulp.lpSum(vice[pid] for pid in ids) == 1

    for pos, n in SQUAD_COUNTS.items():
        pos_ids = [p.id for p in players if p.position == pos]
        prob += pulp.lpSum(own[pid] for pid in pos_ids) == n
    gk = [p.id for p in players if p.position == 1]
    defs = [p.id for p in players if p.position == 2]
    mids = [p.id for p in players if p.position == 3]
    fwds = [p.id for p in players if p.position == 4]
    for w in week_ids:
        prob += pulp.lpSum(start[pid][w] for pid in gk) == 1
        prob += pulp.lpSum(start[pid][w] for pid in defs) >= 3
        prob += pulp.lpSum(start[pid][w] for pid in mids) >= 2
        prob += pulp.lpSum(start[pid][w] for pid in fwds) >= 1

    for pid in ids:
        if by_id[pid].p_play < min_start_p_play:
            for w in week_ids:
                prob += start[pid][w] == 0

    fifth = {w: pulp.LpVariable(f"fifth_{w}", cat="Binary") for w in week_ids}
    one_fwd = {w: pulp.LpVariable(f"onefwd_{w}", cat="Binary") for w in week_ids}
    for w in week_ids:
        n_def = pulp.lpSum(start[pid][w] for pid in defs)
        n_fwd_w = pulp.lpSum(start[pid][w] for pid in fwds)
        prob += n_def <= 4 + fifth[w]
        prob += n_fwd_w >= 2 - one_fwd[w]

    clubs = {p.team_id for p in players}
    for club in clubs:
        club_ids = [p.id for p in players if p.team_id == club]
        prob += pulp.lpSum(own[pid] for pid in club_ids) <= MAX_PER_CLUB

    if current_ids is None:
        prob += pulp.lpSum(own[pid] * by_id[pid].now_cost for pid in ids) <= budget
        t_used = 0
        hit = 0
    else:
        in_ids = [pid for pid in ids if pid not in current_ids]
        out_ids = [pid for pid in ids if pid in current_ids]
        t = int(transfers or 0)
        incoming = pulp.lpSum(own[pid] for pid in in_ids)
        outgoing = pulp.lpSum(1 - own[pid] for pid in out_ids)
        prob += incoming == t
        prob += outgoing == t
        proceeds = pulp.lpSum((1 - own[pid]) * by_id[pid].sell_cost for pid in out_ids)
        spend = pulp.lpSum(own[pid] * by_id[pid].now_cost for pid in in_ids)
        prob += spend <= bank + proceeds
        t_used = t
        hit = 0

    mean_term = []
    var_term = []
    bench_term = []
    for i, w in enumerate(week_ids):
        wt = discount**i
        for pid in ids:
            p = by_id[pid]
            mu = p.mu_at(w)
            var = p.var_at(w)
            mean_term.append(wt * start[pid][w] * mu)
            mean_term.append(wt * cap[pid][w] * mu)
            var_term.append(wt * start[pid][w] * var)
            var_term.append(wt * 3.0 * cap[pid][w] * var)
            bench_term.append(wt * BENCH_WEIGHT * (own[pid] - start[pid][w]) * mu)
        mean_term.append(-wt * fifth_defender_penalty * fifth[w])
        mean_term.append(-wt * one_forward_penalty * one_fwd[w])
    prob += pulp.lpSum(mean_term) + pulp.lpSum(bench_term) - lambda_var * pulp.lpSum(var_term)

    status = prob.solve(_solver())
    if pulp.LpStatus[status] != "Optimal":
        return None

    owned = [pid for pid in ids if own[pid].value() > 0.5]
    starters = [pid for pid in ids if start[pid][w0].value() > 0.5]
    captain = next(pid for pid in ids if cap[pid][w0].value() > 0.5)
    vice = next(pid for pid in ids if vice[pid].value() > 0.5)
    bench = _order_bench(owned, starters, by_id)
    xi_mu = sum(by_id[pid].mu_at(w0) for pid in starters) + by_id[captain].mu_at(w0)
    xi_var = sum(by_id[pid].var_at(w0) for pid in starters) + 3.0 * by_id[captain].var_at(w0)
    first3 = week_ids[:3]
    h3 = sum(
        sum(by_id[pid].mu_at(w) for pid in starters) + by_id[captain].mu_at(w) for w in first3
    )
    cost = sum(by_id[pid].now_cost for pid in owned)
    in_list: list[int] = []
    out_list: list[int] = []
    bank_after = budget - cost
    if current_ids is not None:
        in_list = [pid for pid in owned if pid not in current_ids]
        out_list = [pid for pid in current_ids if pid not in owned]
        sold = sum(by_id[pid].sell_cost for pid in out_list)
        bought = sum(by_id[pid].now_cost for pid in in_list)
        bank_after = bank + sold - bought
    return Plan(
        mode="initial" if current_ids is None else "transfers",
        transfers=t_used,
        hit_cost=hit,
        owned=owned,
        starters=starters,
        bench=bench,
        captain=captain,
        vice=vice,
        xi_mu=xi_mu,
        xi_sd=float(np.sqrt(max(xi_var, 0.0))),
        objective=float(pulp.value(prob.objective) or 0.0),
        horizon3=h3,
        cost=cost,
        bank=bank_after,
        in_ids=in_list,
        out_ids=out_list,
    )


def _order_bench(owned: list[int], starters: list[int], by_id: dict[int, OptPlayer]) -> list[int]:
    leftover = [pid for pid in owned if pid not in starters]
    gk = [pid for pid in leftover if by_id[pid].position == 1]
    others = [pid for pid in leftover if by_id[pid].position != 1]
    others.sort(key=lambda pid: by_id[pid].mu, reverse=True)
    return gk + others


def choose_plan(
    players: list[OptPlayer],
    *,
    lambda_var: float,
    current_ids: set[int] | None,
    bank: int,
    free_transfers: int,
    ft_future_value: float,
    hit_margin: float,
    weeks: list[int] | None = None,
    discount: float = 1.0,
    fifth_defender_penalty: float = 1.2,
    one_forward_penalty: float = 0.8,
    min_start_p_play: float = 0.55,
) -> Plan:
    shape = dict(
        fifth_defender_penalty=fifth_defender_penalty,
        one_forward_penalty=one_forward_penalty,
        min_start_p_play=min_start_p_play,
    )
    if current_ids is None:
        plan = solve_squad(
            players, lambda_var=lambda_var, weeks=weeks, discount=discount, **shape
        )
        if plan is None:
            raise RuntimeError("Initial squad MIP was infeasible")
        n = len(weeks) if weeks else 1
        plan.notes.append(
            f"Squad chosen to maximize {n}-week XI+captain xP (captain/XI re-picked each GW)."
        )
        return plan

    candidates: list[Plan] = []
    for t in (0, 1, 2):
        plan = solve_squad(
            players,
            lambda_var=lambda_var,
            current_ids=current_ids,
            transfers=t,
            bank=bank,
            weeks=weeks,
            discount=discount,
            **shape,
        )
        if plan is None:
            continue
        hit = max(0, t - free_transfers) * 4
        plan.hit_cost = hit
        unused_ft = max(free_transfers - t, 0)
        plan.objective = plan.objective - hit + unused_ft * ft_future_value
        plan.notes.append(f"T={t} hit={hit} unused_ft_bonus={unused_ft * ft_future_value:.1f}")
        candidates.append(plan)
    if not candidates:
        raise RuntimeError("No feasible transfer plan")

    by_t = {p.transfers: p for p in candidates}
    base_t = min(free_transfers, max(by_t))
    base = by_t.get(base_t) or candidates[0]
    best = max(candidates, key=lambda p: p.objective)
    if best.hit_cost > 0:
        gain3 = best.horizon3 - base.horizon3
        if gain3 < best.hit_cost + hit_margin:
            best.notes.append(
                f"Hit rejected: 3GW gain {gain3:.1f} < {best.hit_cost}+{hit_margin} margin. Rolling back to T={base.transfers}."
            )
            return base
    return best


def prune_players(
    preds: Iterable[PlayerPrediction],
    current_ids: set[int] | None,
    per_position: int = 50,
) -> list[PlayerPrediction]:
    current_ids = current_ids or set()
    chosen: dict[int, PlayerPrediction] = {}
    for pred in preds:
        if pred.player_id in current_ids:
            chosen[pred.player_id] = pred
    by_pos: dict[int, list[PlayerPrediction]] = {1: [], 2: [], 3: [], 4: []}
    for pred in preds:
        if pred.minutes.p_play <= 0 and pred.player_id not in current_ids:
            continue
        if pred.mu <= 0 and pred.mu_horizon.get(5, 0) <= 0 and pred.player_id not in current_ids:
            continue
        by_pos.setdefault(pred.position, []).append(pred)
    for pos, group in by_pos.items():
        by_h = sorted(group, key=lambda p: p.mu_horizon.get(5, p.mu), reverse=True)
        by_1 = sorted(group, key=lambda p: p.mu, reverse=True)
        for pred in by_h[:per_position] + by_1[: max(15, per_position // 2)]:
            chosen[pred.player_id] = pred
    return list(chosen.values())


def run_optimize(
    cfg: dict[str, Any],
    bootstrap: dict[str, Any],
    event: dict[str, Any],
    preds: list[PlayerPrediction],
    *,
    current_ids: set[int] | None = None,
    bank: int | None = None,
    sell_prices: dict[int, int] | None = None,
    free_transfers: int | None = None,
) -> Plan:
    event_id = int(event["id"])
    initial = current_ids is None or (event_id == 1 and not event.get("finished"))
    pruned = prune_players(preds, None if initial else current_ids)
    sell_prices = sell_prices or {}
    players = [OptPlayer.from_pred(p, sell_prices.get(p.player_id)) for p in pruned]
    lambda_var = _cfg_float(cfg, "lambda_var", 0.02)
    horizon = int(cfg.get("horizon_gws") or 5)
    start_gw = int(event["id"])
    weeks = list(range(start_gw, start_gw + horizon))
    discount = _cfg_float(cfg, "horizon_discount", 1.0)
    kwargs = dict(
        lambda_var=lambda_var,
        weeks=weeks,
        discount=discount,
        ft_future_value=_cfg_float(cfg, "free_transfer_future_value", 1.5),
        hit_margin=_cfg_float(cfg, "hit_margin", 1.0),
        fifth_defender_penalty=_cfg_float(cfg, "fifth_defender_penalty", 1.2),
        one_forward_penalty=_cfg_float(cfg, "one_forward_penalty", 0.8),
        min_start_p_play=_cfg_float(cfg, "min_start_p_play", 0.55),
    )
    if initial:
        return choose_plan(players, current_ids=None, bank=0, free_transfers=0, **kwargs)
    return choose_plan(
        players,
        current_ids=set(current_ids or []),
        bank=int(bank or 0),
        free_transfers=int(free_transfers if free_transfers is not None else 1),
        **kwargs,
    )


def print_plan(plan: Plan, team_names: dict[int, str], players: list[OptPlayer] | None = None) -> None:
    by_id = {p.id: p for p in players} if players else None
    print()
    print(
        f"Plan ({plan.mode})  transfers={plan.transfers}  hit=-{plan.hit_cost}  "
        f"cost={plan.cost / 10:.1f}m  bank={plan.bank / 10:.1f}m"
    )
    print(f"XI xP with captain: {plan.xi_mu:.2f} +/- {plan.xi_sd:.2f}   3GW horizon {plan.horizon3:.1f}")
    if plan.in_ids or plan.out_ids:
        print(f"OUT: {plan.out_ids}   IN: {plan.in_ids}")
    pos_name = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    if by_id:
        print("Starting XI")
        for pid in sorted(plan.starters, key=lambda i: (by_id[i].position, -by_id[i].mu)):
            p = by_id[pid]
            flag = " (C)" if pid == plan.captain else " (V)" if pid == plan.vice else ""
            name = p.name.encode("ascii", "replace").decode("ascii")
            print(f"  {pos_name[p.position]} {name}{flag}  {p.mu:.2f}  {p.now_cost / 10:.1f}m")
        print("Bench")
        for pid in plan.bench:
            p = by_id[pid]
            name = p.name.encode("ascii", "replace").decode("ascii")
            print(f"  {pos_name[p.position]} {name}  {p.mu:.2f}  {p.now_cost / 10:.1f}m")
    for note in plan.notes:
        print(f"note: {note}")
