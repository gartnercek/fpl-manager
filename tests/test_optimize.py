from __future__ import annotations

from collections import Counter

from fpl_manager.optimize import OptPlayer, choose_plan, sell_price, solve_squad


def P(pid, name, pos, team, cost, mu, var=1.0):
    return OptPlayer(pid, name, team, pos, cost, cost, mu, var, mu * 3, 1.0)


def _pool():
    players = [
        P(1, "GK-A", 1, 1, 45, 4.0),
        P(2, "GK-B", 1, 2, 40, 3.5),
        P(3, "GK-C", 1, 3, 50, 3.0),
        P(10, "DEF-star1", 2, 9, 55, 6.0),
        P(11, "DEF-star2", 2, 9, 55, 5.8),
        P(12, "DEF-star3", 2, 9, 55, 5.5),
        P(13, "DEF-star4", 2, 9, 55, 5.4),
        P(14, "DEF-ok1", 2, 1, 45, 3.2),
        P(15, "DEF-ok2", 2, 2, 45, 3.1),
        P(16, "DEF-ok3", 2, 3, 45, 3.0),
        P(20, "MID-1", 3, 1, 70, 7.5),
        P(21, "MID-2", 3, 2, 80, 7.0),
        P(22, "MID-3", 3, 3, 90, 6.8),
        P(23, "MID-4", 3, 4, 60, 5.0),
        P(24, "MID-5", 3, 5, 50, 4.5),
        P(25, "MID-6", 3, 6, 45, 4.0),
        P(30, "FWD-C", 4, 4, 140, 9.0),
        P(31, "FWD-2", 4, 5, 80, 6.0),
        P(32, "FWD-3", 4, 6, 60, 4.5),
        P(33, "FWD-4", 4, 7, 50, 3.5),
    ]
    return players


def test_sell_price_half_profit():
    assert sell_price(50, 53) == 51
    assert sell_price(50, 50) == 50
    assert sell_price(50, 47) == 47


def test_initial_squad_respects_club_limit_and_counts():
    plan = solve_squad(_pool(), lambda_var=0.02)
    assert plan is not None
    by_id = {p.id: p for p in _pool()}
    owned = [by_id[i] for i in plan.owned]
    counts = Counter(p.position for p in owned)
    assert counts[1] == 2 and counts[2] == 5 and counts[3] == 5 and counts[4] == 3
    club9 = sum(1 for p in owned if p.team_id == 9)
    assert club9 <= 3
    assert plan.captain == 30
    assert plan.captain in plan.starters
    assert plan.vice in plan.starters
    assert plan.captain != plan.vice
    assert len(plan.starters) == 11
    gk_starters = sum(1 for i in plan.starters if by_id[i].position == 1)
    assert gk_starters == 1
    assert plan.cost <= 1000


def test_formation_has_minimum_defenders():
    plan = solve_squad(_pool(), lambda_var=0.0)
    by_id = {p.id: p for p in _pool()}
    n_def = sum(1 for i in plan.starters if by_id[i].position == 2)
    n_mid = sum(1 for i in plan.starters if by_id[i].position == 3)
    n_fwd = sum(1 for i in plan.starters if by_id[i].position == 4)
    assert n_def >= 3
    assert n_mid >= 2
    assert n_fwd >= 1


def test_transfer_count_is_exact():
    pool = _pool()
    initial = solve_squad(pool, lambda_var=0.0)
    current = set(initial.owned)
    # Force a 1-transfer by making a non-owned mid insanely good.
    for p in pool:
        if p.id == 25:
            p.mu = 20.0
            p.mu_h3 = 60.0
    plan = solve_squad(pool, lambda_var=0.0, current_ids=current, transfers=1, bank=50)
    assert plan is not None
    assert plan.transfers == 1
    assert len(plan.in_ids) == 1
    assert len(plan.out_ids) == 1
    assert 25 in plan.in_ids


def test_five_week_objective_keeps_steady_forward():
    pool = _pool()
    for p in pool:
        if p.id == 30:
            p.mu = 12.0
            p.mu_by_gw = {1: 12.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}
        if p.id == 31:
            p.mu = 6.2
            p.mu_by_gw = {w: 6.2 for w in range(1, 6)}
    plan = solve_squad(pool, lambda_var=0.0, weeks=[1, 2, 3, 4, 5])
    assert plan is not None
    assert 31 in plan.owned
    assert plan.captain == 30


def test_low_p_play_cannot_start():
    pool = _pool()
    for p in pool:
        if p.id == 10:
            p.p_play = 0.40
            p.mu = 20.0
            p.mu_h3 = 60.0
    plan = solve_squad(pool, lambda_var=0.0)
    assert plan is not None
    assert 10 not in plan.starters


def test_vice_is_second_best_starter_by_mu():
    plan = solve_squad(_pool(), lambda_var=0.0)
    assert plan is not None
    by_id = {p.id: p for p in _pool()}
    ordered = sorted(plan.starters, key=lambda i: -by_id[i].mu)
    assert plan.captain == ordered[0]
    assert plan.vice == ordered[1]


def _plan_kwargs(**overrides):
    base = dict(
        lambda_var=0.0,
        bank=0,
        free_transfers=1,
        ft_future_value=1.5,
        hit_margin=1.0,
        weeks=[1, 2, 3, 4, 5],
        wildcard_available=False,
        free_hit_available=False,
        wildcard_gap_threshold=12.0,
        free_hit_gap_threshold=8.0,
        event_id=2,
    )
    base.update(overrides)
    return base


def test_wildcard_rebuilds_when_current_squad_is_dead():
    pool = _pool()
    initial = solve_squad(pool, lambda_var=0.0, weeks=[1, 2, 3, 4, 5])
    current = set(initial.owned)
    for p in pool:
        weeks = {w: (0.4 if p.id in current else 8.0) for w in range(1, 6)}
        p.mu = weeks[1]
        p.mu_h3 = weeks[1] * 3
        p.mu_by_gw = weeks
    plan = choose_plan(pool, current_ids=current, **_plan_kwargs(wildcard_available=True, wildcard_gap_threshold=1.0))
    assert plan.mode == "wildcard"
    assert len(plan.in_ids) >= 3


def test_wildcard_held_when_gap_is_small():
    pool = _pool()
    initial = solve_squad(pool, lambda_var=0.0, weeks=[1, 2, 3, 4, 5])
    current = set(initial.owned)
    plan = choose_plan(pool, current_ids=current, **_plan_kwargs(wildcard_available=True, wildcard_gap_threshold=12.0))
    assert plan.mode == "transfers"
    assert any("Wildcard held" in n or "held" in n for n in plan.notes)


def test_wildcard_held_in_secretary_sample_even_if_gap_is_large():
    pool = _pool()
    initial = solve_squad(pool, lambda_var=0.0, weeks=[1, 2, 3, 4, 5])
    current = set(initial.owned)
    for p in pool:
        weeks = {w: (0.4 if p.id in current else 8.0) for w in range(1, 6)}
        p.mu = weeks[1]
        p.mu_h3 = weeks[1] * 3
        p.mu_by_gw = weeks
    stop = {
        "prior_gws": 5.0,
        "explore_fraction": 0.37,
        "wc_typical": 16.0,
        "wc_noise_sd": 8.0,
        "wc_override": 32.0,
        "epsilon": 0.5,
        "p_future_dgw": 0.0,
        "mc_draws": 200,
    }
    plan = choose_plan(
        pool,
        current_ids=current,
        **_plan_kwargs(wildcard_available=True, wildcard_gap_threshold=1.0, rebuild_stop=stop),
    )
    assert plan.mode == "transfers"


def test_free_hit_preferred_for_one_week_punt():
    pool = _pool()
    initial = solve_squad(pool, lambda_var=0.0, weeks=[1, 2, 3, 4, 5])
    current = set(initial.owned)
    for p in pool:
        if p.id in current:
            p.mu_by_gw = {1: 0.5, 2: 6.0, 3: 6.0, 4: 6.0, 5: 6.0}
        else:
            p.mu_by_gw = {1: 12.0, 2: 0.3, 3: 0.3, 4: 0.3, 5: 0.3}
        p.mu = p.mu_by_gw[1]
        p.mu_h3 = sum(p.mu_by_gw[w] for w in (1, 2, 3))
    plan = choose_plan(
        pool,
        current_ids=current,
        **_plan_kwargs(wildcard_available=True, free_hit_available=True, wildcard_gap_threshold=12.0, free_hit_gap_threshold=1.0),
    )
    assert plan.mode == "free_hit"
    assert plan.in_ids
