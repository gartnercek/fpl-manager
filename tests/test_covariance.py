import numpy as np

from fpl_manager.fixtures import Matchup
from fpl_manager.models.minutes import MinutesEstimate
from fpl_manager.models.setpieces import SetPieceRates
from fpl_manager.models.simulate import FixtureScores, simulate_match


def _player(*, team=1, pos=2):
    return {
        "id": 1,
        "web_name": "Def",
        "element_type": pos,
        "team": team,
        "now_cost": 50,
        "status": "a",
        "minutes": 90,
        "starts": 1,
        "expected_goals_per_90": 0.05,
        "expected_assists_per_90": 0.05,
        "expected_goals_conceded_per_90": 1.0,
        "yellow_cards": 0,
        "saves_per_90": 0.0,
        "news": "",
        "penalties_order": None,
        "corners_and_indirect_freekicks_order": None,
        "direct_freekicks_order": None,
        "defensive_contribution_per_90": 0.0,
    }


def _teams():
    return {
        1: {"id": 1, "short_name": "ARS", "strength_overall_home": 4, "strength_overall_away": 4},
        2: {"id": 2, "short_name": "AVL", "strength_overall_home": 3, "strength_overall_away": 3},
    }


def _mins():
    return MinutesEstimate(1.0, 0.0, 90.0, 1.0, 0.0, 1.0)


def test_fixture_score_draws_are_shared():
    board = FixtureScores()
    first = board.goals(2, 1, 2, 1.2, 0.8, 400)
    second = board.goals(2, 1, 2, 1.2, 0.8, 400)
    assert np.array_equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])


def test_attacker_cannot_score_when_team_blank():
    board = FixtureScores()
    fx = Matchup(event=2, opponent=2, is_home=True, difficulty=3, kickoff=None)
    teams = _teams()
    fwd = _player(team=1, pos=4)
    mu_shared, _, _ = simulate_match(
        fwd,
        _mins(),
        0.8,
        0.4,
        0.1,
        0.0,
        fx,
        teams,
        {1: 1.0, 2: 1.0},
        3.5,
        np.random.default_rng(1),
        2000,
        SetPieceRates(),
        scores=board,
        lam_home=0.0,
        lam_away=1.5,
    )
    mu_indep, _, _ = simulate_match(
        fwd,
        _mins(),
        0.8,
        0.4,
        0.1,
        0.0,
        fx,
        teams,
        {1: 1.0, 2: 1.0},
        3.5,
        np.random.default_rng(1),
        2000,
        SetPieceRates(),
        lam_home=0.0,
        lam_away=1.5,
    )
    assert mu_shared < mu_indep - 0.4
