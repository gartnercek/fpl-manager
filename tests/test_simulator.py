import pandas as pd

from fpl_manager.planner import WeekPlan
from fpl_manager.simulator import Team, auto_subs, run_season, score_week, sell_price
from fpl_manager.squad import chips_left_from_history, free_transfers_from_history

POS = {1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 3, 12: 3, 13: 4, 14: 4, 15: 4}
XI = [1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14]
BENCH = [2, 6, 7, 15]


def _plan(chip=None, captain=13, vice=8, squad=None):
    return WeekPlan(1, chip, squad or list(POS), [], [], 0, list(XI), list(BENCH), captain, vice, 0.0, 1)


def test_sell_price_keeps_half_the_profit_rounded_down():
    assert sell_price(50, 53) == 51
    assert sell_price(50, 54) == 52
    assert sell_price(50, 48) == 48


def test_auto_sub_keeps_formation_and_gk_for_gk():
    mins = {p: 90 for p in POS}
    mins[1] = 0  # starting GK out -> bench GK
    mins[3] = 0  # a defender out: first outfield bench is DEF 6
    xi = auto_subs(XI, BENCH, mins, POS)
    assert 2 in xi and 1 not in xi
    assert 6 in xi and 3 not in xi


def test_auto_sub_skips_bench_player_that_breaks_formation():
    starters = [1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14]
    bench = [2, 15, 6, 7]  # FWD first on the bench
    mins = {p: 90 for p in POS}
    mins[3] = 0
    mins[4] = 0  # two DEF out from a 3-at-back -> only defenders may come on
    pos = dict(POS)
    starters = [1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14]
    xi = auto_subs(starters, bench, mins, pos)
    assert sum(1 for p in xi if pos[p] == 2) >= 3
    assert 15 not in xi


def test_captain_falls_back_to_vice_and_triple_captain_triples():
    pts = {p: 2 for p in POS}
    pts[13] = 10
    pts[8] = 6
    mins = {p: 90 for p in POS}
    total, cap_pts, _ = score_week(_plan(), pts, mins, POS)
    assert cap_pts == 10 and total == 2 * 9 + 6 + 20
    mins[13] = 0
    total, cap_pts, _ = score_week(_plan(), pts, mins, POS)
    assert cap_pts == 6  # vice doubled, FWD 13 subbed by bench FWD 15
    mins[13] = 90
    total, _, _ = score_week(_plan(chip="3xc"), pts, mins, POS)
    assert total == 2 * 9 + 6 + 30


def test_bench_boost_counts_all_fifteen():
    pts = {p: 1 for p in POS}
    mins = {p: 90 for p in POS}
    total, _, _ = score_week(_plan(chip="bboost"), pts, mins, POS)
    assert total == 15 + 1  # + captain


def test_free_transfer_rules_from_history():
    hist = [{"event": g, "event_transfers": n} for g, n in [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0)]]
    assert free_transfers_from_history(hist, [], 7) == 5  # banked 2..6 -> capped at 5
    hist = [{"event": 1, "event_transfers": 0}, {"event": 2, "event_transfers": 0}, {"event": 3, "event_transfers": 3}]
    assert free_transfers_from_history(hist, [], 4) == 1  # hit taken -> back to 1
    hist = [{"event": 1, "event_transfers": 0}, {"event": 2, "event_transfers": 0}, {"event": 3, "event_transfers": 9}]
    chips = [{"name": "wildcard", "event": 3}]
    assert free_transfers_from_history(hist, chips, 4) == 2  # WC week keeps the 2, adds none


def test_chips_reset_each_half():
    left = chips_left_from_history([{"name": "3xc", "event": 7}, {"name": "wildcard", "event": 25}])
    assert left[("3xc", 1)] is False and left[("3xc", 2)] is True
    assert left[("wildcard", 2)] is False and left[("wildcard", 1)] is True
    assert left[("bboost", 1)] and left[("freehit", 1)]


def test_season_free_hit_reverts_and_ft_banks():
    squad = list(POS)
    other = [p + 100 for p in squad]
    pos = {**POS, **{p + 100: POS[p] for p in POS}}
    rows = []
    for gw in (1, 2, 3):
        for p in squad + other:
            rows.append({"gw": gw, "element": p, "points": 2, "minutes": 90})
    pm = pd.DataFrame(rows)
    prices = {p: 60 for p in squad + other}

    def decide(gw, team, _prices):
        if gw == 1:
            return WeekPlan(1, None, squad, [], [], 0, XI, BENCH, 13, 8, 0.0, 1)
        if gw == 2:
            xi = [p + 100 for p in XI]
            return WeekPlan(2, "freehit", other, [], [], 0, xi, [p + 100 for p in BENCH], 113, 108, 0.0, 1)
        return WeekPlan(3, None, list(team.squad), [], [], 0, XI, BENCH, 13, 8, 0.0, 2)

    team = Team.fresh()
    team.bank = 1000
    results, team = run_season(pm, pos, lambda g: prices, decide, [1, 2, 3], team=team)
    assert team.squad == squad  # Free Hit squad gone again
    assert results[1].chip == "freehit" and results[1].hits == 0
    assert team.ft == 2  # GW2 FH keeps 1, GW3 rolled -> 2
    assert team.chips_left[("freehit", 1)] is False
