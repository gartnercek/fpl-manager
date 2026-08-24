from fpl_manager.chip_rules import (
    decide_rebuild_chip,
    expiry_adjusted_threshold,
    free_hit_available,
    in_explore_window,
    trust_weight,
    used_in_half,
    wildcard_available,
)
import numpy as np


def test_chips_locked_in_gw1():
    assert wildcard_available([], 1) is False
    assert free_hit_available([], 1) is False


def test_chips_open_in_gw2_if_unused():
    assert wildcard_available([], 2) is True
    assert free_hit_available([], 2) is True


def test_used_wildcard_blocks_that_half_only():
    records = [{"name": "wildcard", "event": 8}]
    assert wildcard_available(records, 9) is False
    assert wildcard_available(records, 20) is True
    assert "wildcard" in used_in_half(records, 8)


def test_expiry_threshold_drops_near_half_end():
    assert expiry_adjusted_threshold(12.0, 2) == 12.0
    assert expiry_adjusted_threshold(12.0, 17) == 12.0 * 0.35
    assert expiry_adjusted_threshold(12.0, 19) == 0.0
    assert expiry_adjusted_threshold(8.0, 38) == 0.0


def test_gw2_is_in_secretary_sample():
    assert in_explore_window(2) is True
    assert in_explore_window(8) is True
    assert in_explore_window(9) is False
    assert trust_weight(2, 5.0) == 1 / 6


def test_stopping_holds_gw2_even_for_large_raw_gap():
    play, why = decide_rebuild_chip(
        28.0,
        2,
        bar=12.0,
        typical=16.0,
        noise_sd=8.0,
        override=32.0,
        prior_gws=5.0,
        explore_fraction=0.37,
        epsilon=0.5,
        p_future_dgw=0.9,
        dgw_multiplier=1.35,
        draws=400,
        rng=np.random.default_rng(0),
    )
    assert play is False
    assert "first" in why


def test_stopping_plays_extreme_outlier_in_sample():
    play, why = decide_rebuild_chip(
        80.0,
        2,
        bar=12.0,
        typical=16.0,
        noise_sd=8.0,
        override=32.0,
        prior_gws=5.0,
        explore_fraction=0.37,
        epsilon=0.5,
        p_future_dgw=0.0,
        dgw_multiplier=1.35,
        draws=400,
        rng=np.random.default_rng(0),
    )
    # 80 × 1/6 ≈ 13.3 still below override 32 and explore window → still hold
    assert play is False
    play, why = decide_rebuild_chip(
        220.0,
        2,
        bar=12.0,
        typical=16.0,
        noise_sd=8.0,
        override=32.0,
        prior_gws=5.0,
        explore_fraction=0.37,
        epsilon=0.5,
        p_future_dgw=0.0,
        dgw_multiplier=1.35,
        draws=200,
        rng=np.random.default_rng(1),
    )
    assert play is True
    assert "play" in why
