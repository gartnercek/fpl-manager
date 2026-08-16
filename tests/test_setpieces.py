from __future__ import annotations

import numpy as np

from fpl_manager.config import load_config
from fpl_manager.fixtures import Matchup
from fpl_manager.models.minutes import MinutesEstimate
from fpl_manager.models.setpieces import SetPieceRates, setpiece_rates
from fpl_manager.models.simulate import simulate_match


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
        "expected_goals_per_90": 0.55,
        "expected_assists_per_90": 0.12,
        "expected_goals_conceded_per_90": 1.1,
        "yellow_cards": 4,
        "saves_per_90": 0.0,
        "ep_next": 0.0,
        "news": "",
        "removed": False,
        "penalties_order": None,
        "corners_and_indirect_freekicks_order": None,
        "direct_freekicks_order": None,
        "team_join_date": "2024-07-01",
        "defensive_contribution_per_90": 0.0,
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


def _sim(pieces: SetPieceRates, *, pos: int = 4, dc90: float = 0.0) -> float:
    player = _player(element_type=pos, defensive_contribution_per_90=dc90)
    mins = MinutesEstimate(0.95, 0.05, 82.0, 0.95, 0.05, 1.0)
    teams = _teams()
    fx = Matchup(event=1, opponent=2, is_home=True, difficulty=2, kickoff=None)
    rng = np.random.default_rng(0)
    mu, _, _ = simulate_match(
        player,
        mins,
        0.40,
        0.10,
        0.10,
        0.0,
        fx,
        teams,
        {1: 0.9, 2: 1.8},
        3.5,
        rng,
        2500,
        pieces,
    )
    return mu


def test_primary_penalty_taker_outscores_clone_without_pens():
    none = _sim(SetPieceRates())
    taker = _sim(SetPieceRates(pk_goal=0.079, pk_miss=0.025, roles=["PK"]))
    assert taker > none + 0.25


def test_setpiece_rates_strip_historical_pens_for_stayers():
    cfg = load_config()
    stayer = setpiece_rates(_player(penalties_order=1, minutes=3000), 33.0, cfg)
    signing = setpiece_rates(
        _player(penalties_order=1, minutes=3000, team_join_date="2026-07-10"), 33.0, cfg
    )
    assert "PK" in stayer.roles
    assert stayer.xg90_strip > 0
    assert signing.xg90_strip == 0
    assert signing.pk_goal > 0


def test_corner_taker_gets_assist_uplift():
    none = _sim(SetPieceRates())
    ck = _sim(SetPieceRates(corner_xa90=0.16, roles=["CK"]))
    assert ck > none + 0.15


def test_defcon_can_add_points_for_tackling_defender():
    none = _sim(SetPieceRates(), pos=2)
    dc = _sim(SetPieceRates(dc90=14.0), pos=2)
    assert dc > none + 0.15
