from __future__ import annotations

import pytest

from fpl_manager.config import load_config
from fpl_manager.fixtures import Matchup
from fpl_manager.models.aggregate import predict_player
from fpl_manager.models.minutes import estimate_minutes
from fpl_manager.models.returns import PositionPriors, mean_overall, team_xgc90


def _player(**overrides):
    base = {
        "id": 1,
        "web_name": "Haaland",
        "element_type": 4,
        "team": 1,
        "now_cost": 140,
        "status": "a",
        "chance_of_playing_next_round": None,
        "minutes": 3000,
        "starts": 34,
        "expected_goals_per_90": 0.85,
        "expected_assists_per_90": 0.15,
        "expected_goals_conceded_per_90": 1.1,
        "yellow_cards": 4,
        "saves_per_90": 0.0,
        "ep_next": 8.0,
        "news": "",
        "removed": False,
    }
    base.update(overrides)
    return base


def _teams():
    return {
        1: {
            "id": 1,
            "short_name": "MCI",
            "strength_overall_home": 5,
            "strength_overall_away": 5,
            "strength_attack_home": 0,
            "strength_attack_away": 0,
            "strength_defence_home": 0,
            "strength_defence_away": 0,
        },
        2: {
            "id": 2,
            "short_name": "HUL",
            "strength_overall_home": 2,
            "strength_overall_away": 2,
            "strength_attack_home": 0,
            "strength_attack_away": 0,
            "strength_defence_home": 0,
            "strength_defence_away": 0,
        },
    }


def _priors():
    return PositionPriors(
        xg90={1: 0.0, 2: 0.05, 3: 0.15, 4: 0.35},
        xa90={1: 0.01, 2: 0.06, 3: 0.12, 4: 0.12},
        yc90={1: 0.05, 2: 0.15, 3: 0.18, 4: 0.12},
        saves90={1: 2.8, 2: 0.0, 3: 0.0, 4: 0.0},
    )


def _cfg():
    cfg = load_config()
    cfg["simulate_draws"] = 400
    cfg["ep_next_blend"] = 0.0
    return cfg


def _predict(player, matchups):
    teams = _teams()
    events = sorted({m.event for m in matchups}) or [1]
    by_event: dict[int, list] = {}
    for m in matchups:
        by_event.setdefault(m.event, []).append(m)
    return predict_player(
        player,
        None,
        by_event,
        events,
        teams,
        _priors(),
        {1: 0.9, 2: 1.8},
        mean_overall(teams),
        _cfg(),
    )


def test_injured_player_mu_is_zero():
    easy = Matchup(event=1, opponent=2, is_home=True, difficulty=2, kickoff=None)
    healthy = _predict(_player(), [easy])
    injured = _predict(_player(status="i", chance_of_playing_next_round=0, web_name="Out"), [easy])
    assert healthy.mu > 4.0
    assert injured.mu == 0.0
    assert injured.minutes.p_play == 0.0


def test_premium_striker_outranks_bench_fodder():
    easy = Matchup(event=1, opponent=2, is_home=True, difficulty=2, kickoff=None)
    star = _predict(_player(), [easy])
    fodder = _predict(
        _player(
            id=2,
            web_name="Fodder",
            now_cost=45,
            minutes=90,
            starts=2,
            expected_goals_per_90=0.05,
            expected_assists_per_90=0.02,
            ep_next=1.0,
        ),
        [easy],
    )
    assert star.mu > fodder.mu + 2.0


def test_double_gameweek_roughly_doubles_mu():
    fx = Matchup(event=1, opponent=2, is_home=True, difficulty=2, kickoff=None)
    sgw = _predict(_player(), [fx])
    dgw = _predict(_player(id=3, web_name="DGW"), [fx, fx])
    assert dgw.mu == pytest.approx(2 * sgw.mu, rel=0.08)


def test_estimate_minutes_ewma_prefers_recent():
    player = _player()
    history = [{"minutes": 0}] * 5 + [{"minutes": 90}]
    cfg = _cfg()
    est = estimate_minutes(player, history, cfg)
    assert est.p_ge60 > 0.2
    assert est.p_play <= 1.0


def test_summer_signing_with_no_minutes_is_not_treated_as_nailed():
    cfg = _cfg()
    stayer = _player(team_join_date="2024-07-01", selected_by_percent="6.0")
    mover = _player(
        id=2,
        web_name="Mover",
        team_join_date="2026-07-20",
        selected_by_percent="6.0",
        minutes=0,
        starts=0,
    )
    a = estimate_minutes(stayer, None, cfg)
    b = estimate_minutes(mover, None, cfg)
    assert b.p_start < a.p_start - 0.08


def test_one_start_does_not_nail_a_player():
    player = _player(
        minutes=80,
        starts=1,
        now_cost=55,
        selected_by_percent="40.0",
        team_join_date="2024-07-01",
        expected_goals_per_90=0.05,
    )
    est = estimate_minutes(player, None, _cfg())
    assert 0.40 < est.p_start < 0.85


def test_cameo_does_not_wipe_a_premium_prior():
    cameo = _player(
        minutes=27,
        starts=0,
        now_cost=77,
        selected_by_percent="18",
        team_join_date="2024-07-01",
    )
    season = _player(
        id=2,
        web_name="Nailed",
        minutes=3000,
        starts=34,
        now_cost=77,
        selected_by_percent="18",
        team_join_date="2024-07-01",
    )
    thin = estimate_minutes(cameo, None, _cfg())
    full = estimate_minutes(season, None, _cfg())
    assert thin.p_start > 0.45
    assert full.p_start > thin.p_start
    assert full.p_start > 0.9


def test_one_game_clean_sheet_does_not_set_team_xgc():
    players = [
        _player(element_type=1, team=1, minutes=90, starts=1, expected_goals_conceded_per_90=0.2, web_name="GK")
    ]
    rates = team_xgc90(players)
    assert rates[1] > 1.0
    assert rates[1] < 1.35


def test_team_xgc_from_goalkeepers():
    players = [
        _player(element_type=1, team=1, minutes=3420, expected_goals_conceded_per_90=0.8, web_name="GK"),
        _player(id=9, element_type=4, team=1, minutes=3000),
    ]
    rates = team_xgc90(players)
    assert rates[1] == pytest.approx(0.8, rel=0.01)
