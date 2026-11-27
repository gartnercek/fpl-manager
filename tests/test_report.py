from fpl_manager.planner import PlannerConfig, SquadState, plan_gameweek, plan_weeks
from fpl_manager.projection import project
from fpl_manager.report import fingerprint, pair_transfers, render_report

from synth import make_season

FAST = PlannerConfig(horizon=3, time_limit=20, pool_per_pos={1: 6, 2: 12, 3: 12, 4: 8})
EVENT = {"id": 4, "deadline_time": "2030-08-14T17:30:00Z"}


def _setup():
    s = make_season(played=3, stars={11: 0.9, 35: 1.0})
    proj = project(s, 4, 3)
    players = proj.table[["pos", "team", "price"]]
    xp = proj.xp_matrix()
    first = plan_weeks(xp, players, SquadState.fresh(), 4, FAST, p_start=proj.table.p_start, chip_weeks={})
    sell = {p: int(players.at[p, "price"]) for p in first.first.squad}
    chips = {(c, h): True for c in ("wildcard", "freehit", "bboost", "3xc") for h in (1, 2)}
    return s, proj, players, xp, first, SquadState(list(first.first.squad), sell, 150, 2, chips)


def test_initial_letter_names_players_bench_and_chips():
    s, proj, players, xp, first, _ = _setup()
    subject, text, html = render_report(event=EVENT, plan=first, proj=proj, state=SquadState.fresh(),
                                        timezone="Europe/Ljubljana", team_names=s.team_names)
    assert "GW4" in subject and "(C)" in subject
    assert "Pick this 15" in text
    assert "Bench order: 1." in text and "Bench (auto-sub order)" in html
    assert "Chips left (GW1-19, expire after GW19)" in text
    name = str(proj.table.at[first.first.captain, "name"])
    assert f"Captain {name}" in text


def test_letter_for_existing_squad_shows_fts_and_plan_ahead():
    s, proj, players, xp, first, state = _setup()
    plan = plan_gameweek(xp, players, state, 4, FAST, p_start=proj.table.p_start, xp_full=xp)
    subject, text, html = render_report(event=EVENT, plan=plan, proj=proj, state=state,
                                        timezone="Europe/Ljubljana", team_names=s.team_names)
    assert "Free transfers: 2 available this GW" in text
    assert "PLAN FOR THE NEXT WEEKS" in text and "GW5:" in text
    assert "HOLD" in text or "PLAY" in text
    fp = fingerprint(plan)
    assert '"chip"' in fp and '"c"' in fp


def test_pair_transfers_matches_same_position():
    s, proj, players, xp, first, state = _setup()
    t = proj.table
    out_ids = [p for p in state.squad if t.at[p, "pos"] == 4][:1] + [p for p in state.squad if t.at[p, "pos"] == 2][:1]
    in_ids = [p for p in t.index if t.at[p, "pos"] == 2 and p not in state.squad][:1] + [
        p for p in t.index if t.at[p, "pos"] == 4 and p not in state.squad
    ][:1]
    pairs = pair_transfers(out_ids, in_ids, proj, 4, [4, 5, 6], state)
    for pr in pairs:
        assert pr["pos"] in ("DEF", "FWD")
    by_pos = {pr["pos"]: pr for pr in pairs}
    assert by_pos["FWD"]["in_name"] == str(t.at[in_ids[1], "name"])
