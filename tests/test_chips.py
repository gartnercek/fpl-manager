from __future__ import annotations

import numpy as np

from fpl_manager.chips import advise_chips, expected_max_future
from fpl_manager.fixtures import Matchup
from fpl_manager.models.aggregate import PlayerPrediction
from fpl_manager.models.minutes import MinutesEstimate
from fpl_manager.optimize import Plan


def _mins():
    return MinutesEstimate(0.95, 0.05, 85.0, 0.95, 0.05, 1.0)


def _pred(pid, name, team, pos, mu, n_events=6, dgw_event: int | None = None):
    horizon = {}
    running = 0.0
    for i in range(1, n_events + 1):
        event = i
        extra = mu * (1.6 if dgw_event == event else 1.0)
        running += extra
        horizon[i] = running
    return PlayerPrediction(
        player_id=pid,
        web_name=name,
        team_id=team,
        position=pos,
        now_cost=80,
        status="a",
        news="",
        ep_next=mu,
        minutes=_mins(),
        xg90=0.4,
        xa90=0.1,
        mu=mu * (1.6 if dgw_event == 1 else 1.0),
        sigma2=4.0,
        mu_horizon=horizon,
        fixtures=[],
    )


def _plan(captain, bench, starters=None):
    starters = starters or [captain, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    return Plan(
        mode="initial",
        transfers=0,
        hit_cost=0,
        owned=starters + bench,
        starters=starters,
        bench=bench,
        captain=captain,
        vice=2,
        xi_mu=70.0,
        xi_sd=12.0,
        objective=70.0,
        horizon3=180.0,
        cost=1000,
        bank=0,
    )


def test_expected_max_future_prefers_confirmed_dgw():
    rng = np.random.default_rng(0)
    mean_v, best = expected_max_future(
        [(2, 6.0, False), (3, 6.2, False), (34, 11.0, True)],
        p_future_dgw=0.9,
        dgw_multiplier=1.6,
        noise_sd=0.1,
        draws=500,
        rng=rng,
    )
    assert best == 34
    assert mean_v > 9.0


def test_hold_triple_captain_in_ordinary_early_week():
    events = list(range(1, 7))
    preds = [_pred(i, f"P{i}", 1 + (i % 10), 3 if i > 2 else 4, 6.0 if i == 1 else 4.0) for i in range(1, 16)]
    preds[0] = _pred(1, "Star", 1, 4, 6.5)
    fixtures = {t: {e: [Matchup(e, 2, True, 3, None)] for e in events} for t in range(1, 12)}
    cfg = {
        "chips": {
            "mc_draws": 400,
            "epsilon": 0.5,
            "p_future_dgw": 0.9,
            "dgw_captain_multiplier": 1.6,
            "v_noise_sd": 0.3,
        }
    }
    advice = advise_chips(preds, _plan(1, [12, 13, 14, 15]), events, fixtures, cfg, rng=np.random.default_rng(1))
    assert advice.triple_captain.play is False
    assert advice.bench_boost.play is False
    assert "HOLD" in advice.triple_captain.reason


def test_play_triple_captain_on_confirmed_dgw():
    events = list(range(1, 7))
    star = _pred(1, "Star", 1, 4, 7.0, dgw_event=1)
    others = [_pred(i, f"P{i}", 2, 3, 3.0) for i in range(2, 16)]
    preds = [star, *others]
    fixtures = {
        1: {1: [Matchup(1, 2, True, 2, None), Matchup(1, 3, False, 2, None)], **{e: [Matchup(e, 2, True, 3, None)] for e in events[1:]}},
        2: {e: [Matchup(e, 1, False, 3, None)] for e in events},
    }
    cfg = {
        "chips": {
            "mc_draws": 400,
            "epsilon": 0.5,
            "p_future_dgw": 0.0,
            "dgw_captain_multiplier": 1.6,
            "v_noise_sd": 0.2,
        }
    }
    advice = advise_chips(preds, _plan(1, [12, 13, 14, 15]), events, fixtures, cfg, rng=np.random.default_rng(2))
    assert advice.triple_captain.play is True
    assert "PLAY" in advice.triple_captain.reason


def test_play_bench_boost_when_bench_is_loaded_and_no_future_dgw():
    events = list(range(1, 7))
    preds = []
    for i in range(1, 12):
        preds.append(_pred(i, f"S{i}", 1, 3, 5.0))
    for i in range(12, 16):
        preds.append(_pred(i, f"B{i}", 1, 2, 5.5))
    fixtures = {1: {e: [Matchup(e, 2, True, 2, None)] for e in events}}
    cfg = {
        "chips": {
            "mc_draws": 300,
            "epsilon": 0.5,
            "p_future_dgw": 0.0,
            "dgw_captain_multiplier": 1.6,
            "v_noise_sd": 0.1,
        }
    }
    # Future bench xP equals now, so with no DGW prior the expected max future is ~now and epsilon allows PLAY.
    # Make now a double for bench teams so V_now is clearly larger.
    fixtures[1][1] = [Matchup(1, 2, True, 2, None), Matchup(1, 3, False, 2, None)]
    for i in range(12, 16):
        preds[i - 1] = _pred(i, f"B{i}", 1, 2, 5.5, dgw_event=1)
    advice = advise_chips(preds, _plan(1, [12, 13, 14, 15]), events, fixtures, cfg, rng=np.random.default_rng(3))
    assert advice.bench_boost.play is True
