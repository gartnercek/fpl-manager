import pandas as pd

from fpl_manager.planner import PlannerConfig, SquadState, best_lineup, hold_value, plan_gameweek, plan_weeks
from fpl_manager.projection import availability_from_status, project
from fpl_manager.season import fit_team_ratings

from synth import TEAMS, make_season

FAST = PlannerConfig(horizon=3, time_limit=20, pool_per_pos={1: 6, 2: 12, 3: 12, 4: 8})


def _inputs(season, gw, horizon=3, avail=None):
    proj = project(season, gw, horizon, availability=avail)
    players = proj.table[["pos", "team", "price"]].copy()
    return proj, proj.xp_matrix(), players


def _valid_squad(squad, table):
    pos = table.loc[squad, "pos"].value_counts().to_dict()
    clubs = table.loc[squad, "team"].value_counts()
    return len(squad) == 15 and pos == {1: 2, 2: 5, 3: 5, 4: 3} and clubs.max() <= 3


def test_team_ratings_rank_the_strong_side():
    s = make_season(played=4)
    att, dfn, mu, hfa = fit_team_ratings(s.pm, s.fixtures, TEAMS, prior_matches=1.0)
    assert att[1] == max(att.values())
    assert dfn[1] == min(dfn.values())


def test_star_outscores_fodder_and_injured_is_zero():
    s = make_season(played=4, stars={11: 0.9})
    proj = project(s, 5, 2, availability={23: 0.0})
    t = proj.table
    assert t.at[11, "xp_5"] > t[(t.pos == 4) & (t.index != 11)]["xp_5"].max()
    assert t.at[23, "xp_5"] == 0.0
    assert t.at[23, "xp_6"] > 0.0  # recovers in later weeks


def test_double_gameweek_roughly_doubles_and_blank_is_zero():
    s = make_season(played=4, doubles={6: [(2, 3)]}, blanks={7: [4]})
    proj = project(s, 5, 3)
    t = proj.table
    two = t[t.team == 2]
    ratio = (two["xp_6"] / two["xp_5"]).median()
    assert 1.5 < ratio < 2.6
    assert (t[t.team == 4]["xp_7"] == 0).all()


def test_status_flags_turn_into_availability():
    status = pd.DataFrame(
        {"element": [1, 2, 3, 4], "status": ["a", "d", "i", "s"], "chance": [None, 75, 0, None], "news": ["", "", "", ""]}
    )
    av = availability_from_status(status)
    assert av[1] == 1.0 and av[2] == 0.75 and av[3] == 0.0 and av[4] == 0.0


def test_initial_squad_obeys_fpl_rules():
    s = make_season(played=3, stars={11: 0.9})
    proj, xp, players = _inputs(s, 4)
    plan = plan_weeks(xp, players, SquadState.fresh(), 4, FAST, p_start=proj.table.p_start, chip_weeks={})
    wp = plan.first
    assert _valid_squad(wp.squad, proj.table)
    assert sum(int(players.at[p, "price"]) for p in wp.squad) <= 1000
    assert len(wp.starters) == 11 and wp.captain in wp.starters
    assert wp.captain == 11  # the star


def _owned_state(season, plan, ft=1, bank=0):
    sell = {p: int(season.players.set_index("element").at[p, "price"]) for p in plan.first.squad}
    return SquadState(list(plan.first.squad), sell, bank, ft, {(c, h): True for c in ("wildcard", "freehit", "bboost", "3xc") for h in (1, 2)})


def test_rolls_when_nothing_is_worth_a_transfer_and_uses_banked_fts():
    s = make_season(played=3)
    proj, xp, players = _inputs(s, 4)
    first = plan_weeks(xp, players, SquadState.fresh(), 4, FAST, p_start=proj.table.p_start, chip_weeks={})
    state = _owned_state(s, first, ft=1)
    again = plan_weeks(xp, players, state, 4, FAST, p_start=proj.table.p_start, chip_weeks={})
    assert again.first.transfers_in == []  # already optimal -> roll
    assert again.first.ft_after == 2
    # Two good players appear; with 2 FTs both come in without a hit.
    s2 = make_season(played=3, stars={35: 1.0, 47: 1.0})
    proj2, xp2, players2 = _inputs(s2, 4)
    state2 = _owned_state(s2, first, ft=2, bank=200)
    plan2 = plan_weeks(xp2, players2, state2, 4, FAST, p_start=proj2.table.p_start, chip_weeks={})
    assert {35, 47} & set(plan2.first.transfers_in)
    assert len(plan2.first.transfers_in) == 2  # both banked FTs used
    assert plan2.first.hits == 0


def test_hold_value_falls_to_zero_at_the_end_of_the_half():
    cfg = PlannerConfig()
    import numpy as np

    rng = np.random.default_rng(0)
    early, _ = hold_value("3xc", 5, cfg, {w: 6.0 for w in range(6, 20)}, rng)
    late, _ = hold_value("3xc", 18, cfg, {19: 6.0}, rng)
    last, _ = hold_value("3xc", 19, cfg, {}, rng)
    assert early > late > 0 and last == 0.0


def test_triple_captain_waits_for_a_double_and_then_plays_it():
    s = make_season(n_gw=19, played=3, stars={11: 0.9}, doubles={6: [(1, 2)]})
    proj, xp, players = _inputs(s, 4, horizon=16)
    first = plan_weeks(xp, players, SquadState.fresh(), 4, FAST, p_start=proj.table.p_start, chip_weeks={})
    state = _owned_state(s, first, ft=1)
    chips = {k: False for k in state.chips_left}
    chips[("3xc", 1)] = True
    state.chips_left = chips
    now = plan_gameweek(xp, players, state, 4, FAST, p_start=proj.table.p_start, xp_full=xp)
    assert now.first.chip is None  # GW6 double for the star is ahead
    proj6, xp6, players6 = _inputs(s, 6, horizon=14)
    later = plan_gameweek(xp6, players6, state, 6, FAST, p_start=proj6.table.p_start, xp_full=xp6)
    assert later.first.chip == "3xc" and later.first.captain == 11


def test_expiring_chips_get_played():
    s = make_season(n_gw=19, played=17)
    proj, xp, players = _inputs(s, 18, horizon=2)
    first = plan_weeks(xp, players, SquadState.fresh(), 18, FAST, p_start=proj.table.p_start, chip_weeks={})
    state = _owned_state(s, first, ft=1)
    state.chips_left = {(c, h): (h == 1 and c in ("bboost", "3xc")) for c in ("wildcard", "freehit", "bboost", "3xc") for h in (1, 2)}
    plan = plan_gameweek(xp, players, state, 18, FAST, p_start=proj.table.p_start, xp_full=xp)
    assert plan.first.chip in ("bboost", "3xc")  # 2 chips, 2 GWs left in the half


def test_best_lineup_formation():
    pos = pd.Series({i: p for i, p in enumerate([1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4], start=1)})
    xp = {i: float(i) for i in pos.index}
    xi, cap, vice = best_lineup(list(pos.index), xp, pos)
    assert len(xi) == 11 and sum(1 for p in xi if pos[p] == 1) == 1
    assert sum(1 for p in xi if pos[p] == 2) >= 3 and sum(1 for p in xi if pos[p] == 4) >= 1
    assert cap == 15 and vice == 14
