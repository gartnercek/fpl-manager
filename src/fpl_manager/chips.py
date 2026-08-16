from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from fpl_manager.fixtures import Matchup
from fpl_manager.models.aggregate import PlayerPrediction
from fpl_manager.optimize import Plan


@dataclass
class ChipVerdict:
    name: str
    play: bool
    v_now: float
    e_max_future: float
    best_future_gw: int | None
    reason: str


@dataclass
class ChipAdvice:
    triple_captain: ChipVerdict
    bench_boost: ChipVerdict
    wildcard_note: str | None
    free_hit_note: str | None


def _player_mu_by_gw(pred: PlayerPrediction, events: list[int]) -> dict[int, float]:
    out: dict[int, float] = {}
    prev = 0.0
    for i, event in enumerate(events, start=1):
        total = pred.mu_horizon.get(i, prev)
        out[event] = total - prev
        prev = total
    return out


def _is_dgw(matchups: list[Matchup]) -> bool:
    return len(matchups) >= 2


def _captain_value_by_gw(
    preds: list[PlayerPrediction],
    events: list[int],
    fixtures_by_team: dict[int, dict[int, list[Matchup]]],
) -> dict[int, tuple[float, int | None, bool]]:
    """event -> (best captain extra xP, player id, is_dgw for that player)."""
    result: dict[int, tuple[float, int | None, bool]] = {}
    for event in events:
        best = 0.0
        best_id = None
        best_dgw = False
        for pred in preds:
            mu_g = _player_mu_by_gw(pred, events).get(event, 0.0)
            dgw = _is_dgw(list(fixtures_by_team.get(pred.team_id, {}).get(event, [])))
            if mu_g > best:
                best, best_id, best_dgw = mu_g, pred.player_id, dgw
        result[event] = (best, best_id, best_dgw)
    return result


def _bench_value_by_gw(
    preds: list[PlayerPrediction],
    events: list[int],
    plan: Plan,
) -> dict[int, float]:
    by_id = {p.player_id: p for p in preds}
    out: dict[int, float] = {}
    for event in events:
        total = 0.0
        for pid in plan.bench:
            pred = by_id.get(pid)
            if pred is None:
                continue
            total += _player_mu_by_gw(pred, events).get(event, 0.0)
        out[event] = total
    return out


def expected_max_future(
    future_values: list[tuple[int, float, bool]],
    *,
    p_future_dgw: float,
    dgw_multiplier: float,
    noise_sd: float,
    draws: int,
    rng: np.random.Generator,
) -> tuple[float, int | None]:
    if not future_values:
        # Unannounced DGW is the only remaining option.
        synthetic = dgw_multiplier * 5.0
        return synthetic * p_future_dgw, None
    point = {gw: val for gw, val, _ in future_values}
    confirmed_dgw = any(is_dgw for _, _, is_dgw in future_values)
    samples = []
    for _ in range(draws):
        rolled = []
        for gw, val, is_dgw in future_values:
            rolled.append((gw, max(0.0, val + rng.normal(0.0, noise_sd))))
        if not confirmed_dgw and rng.random() < p_future_dgw:
            base = max(point.values()) if point else 5.0
            rolled.append((-1, max(0.0, base * dgw_multiplier + rng.normal(0.0, noise_sd))))
        gw_star, v_star = max(rolled, key=lambda x: x[1])
        samples.append((v_star, gw_star))
    mean_v = float(np.mean([s[0] for s in samples]))
    # Modal future gw among draws (ignore synthetic -1 when reporting).
    gws = [s[1] for s in samples if s[1] != -1]
    best_gw = int(max(set(gws), key=gws.count)) if gws else None
    return mean_v, best_gw


def _verdict(
    name: str,
    v_now: float,
    future_values: list[tuple[int, float, bool]],
    cfg: dict[str, Any],
    rng: np.random.Generator,
    used: bool,
) -> ChipVerdict:
    if used:
        return ChipVerdict(name, False, v_now, 0.0, None, f"{name} already used.")
    chips_cfg = cfg.get("chips") or {}
    e_max, best_gw = expected_max_future(
        future_values,
        p_future_dgw=float(chips_cfg.get("p_future_dgw", 0.9)),
        dgw_multiplier=float(chips_cfg.get("dgw_captain_multiplier", 1.6)),
        noise_sd=float(chips_cfg.get("v_noise_sd", 0.8)),
        draws=int(chips_cfg.get("mc_draws", 2000)),
        rng=rng,
    )
    epsilon = float(chips_cfg.get("epsilon", 0.5))
    play = v_now >= e_max - epsilon
    if play:
        reason = (
            f"PLAY {name}: V_now={v_now:.2f} >= E[max future]={e_max:.2f} - {epsilon}."
        )
    else:
        gw_bit = f" (likely GW{best_gw})" if best_gw else ""
        reason = (
            f"HOLD {name}: V_now={v_now:.2f} vs expected best future{gw_bit} {e_max:.2f}."
        )
    return ChipVerdict(name, play, v_now, e_max, best_gw, reason)


def advise_chips(
    preds: list[PlayerPrediction],
    plan: Plan,
    events: list[int],
    fixtures_by_team: dict[int, dict[int, list[Matchup]]],
    cfg: dict[str, Any],
    *,
    chips_used: Iterable[str] = (),
    rng: np.random.Generator | None = None,
    unconstrained_gap_4gw: float | None = None,
) -> ChipAdvice:
    rng = rng or np.random.default_rng(0)
    used = {str(c).lower() for c in chips_used}
    by_id = {p.player_id: p for p in preds}
    cap = by_id[plan.captain]
    v_tc_now = cap.mu
    v_bb_now = sum(by_id[pid].mu for pid in plan.bench if pid in by_id)

    cap_by_gw = _captain_value_by_gw(preds, events, fixtures_by_team)
    bench_by_gw = _bench_value_by_gw(preds, events, plan)
    now_event = events[0]
    future_events = events[1:]

    tc_future = [(gw, cap_by_gw[gw][0], cap_by_gw[gw][2]) for gw in future_events]
    bb_future = []
    for gw in future_events:
        is_dgw = False
        for pid in plan.bench:
            pred = by_id.get(pid)
            if pred and _is_dgw(list(fixtures_by_team.get(pred.team_id, {}).get(gw, []))):
                is_dgw = True
                break
        bb_future.append((gw, bench_by_gw.get(gw, 0.0), is_dgw))

    tc = _verdict("triple_captain", v_tc_now, tc_future, cfg, rng, "3xc" in used or "triple" in "".join(used))
    bb = _verdict(
        "bench_boost",
        v_bb_now,
        bb_future,
        cfg,
        rng,
        "bboost" in used or "bench" in "".join(used),
    )

    if tc.play and bb.play:
        tc_edge = tc.v_now - tc.e_max_future
        bb_edge = bb.v_now - bb.e_max_future
        if tc_edge >= bb_edge:
            bb = ChipVerdict(bb.name, False, bb.v_now, bb.e_max_future, bb.best_future_gw, bb.reason + " Deferred: do not play TC and BB together.")
        else:
            tc = ChipVerdict(tc.name, False, tc.v_now, tc.e_max_future, tc.best_future_gw, tc.reason + " Deferred: do not play TC and BB together.")

    wc_note = None
    threshold = float(cfg.get("wildcard_gap_threshold") or 12.0)
    if unconstrained_gap_4gw is not None and unconstrained_gap_4gw >= threshold:
        wc_note = (
            f"Your squad's 4-week xP is {unconstrained_gap_4gw:.1f} pts below an unconstrained "
            f"optimal squad (>= {threshold:.0f}) — consider Wildcard."
        )
    fh_note = None
    blanks = 0
    for pid in plan.starters:
        pred = by_id.get(pid)
        if pred is None:
            continue
        if not fixtures_by_team.get(pred.team_id, {}).get(now_event):
            blanks += 1
    if blanks >= 3:
        fh_note = f"{blanks} starters have a blank this GW — consider Free Hit."

    return ChipAdvice(tc, bb, wc_note, fh_note)
