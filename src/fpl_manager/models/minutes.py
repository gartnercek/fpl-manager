from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

import numpy as np

STATUS_OUT = frozenset({"i", "s", "u", "n"})
HALF_LIFE = 3.0
LOOKBACK = 6


@dataclass
class MinutesEstimate:
    p_start: float
    p_cameo: float
    e_minutes: float
    p_ge60: float
    p_1_59: float
    p_play: float

    @classmethod
    def zero(cls) -> MinutesEstimate:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _ewma_weights(n: int, half_life: float = HALF_LIFE) -> np.ndarray:
    ages = np.arange(n - 1, -1, -1, dtype=float)
    w = 0.5 ** (ages / half_life)
    return w / w.sum()


def _from_minute_list(minutes: Sequence[float], availability: float) -> MinutesEstimate:
    if not minutes:
        return MinutesEstimate.zero()
    arr = np.asarray(minutes, dtype=float)
    w = _ewma_weights(len(arr))
    starts = arr >= 60
    cameos = (arr > 0) & (arr < 60)
    p_start = float(np.clip(np.dot(w, starts.astype(float)), 0.0, 1.0))
    p_cameo = float(np.clip(np.dot(w, cameos.astype(float)), 0.0, 1.0 - p_start))
    if starts.any():
        sw = w[starts]
        e_start = float(np.dot(sw, arr[starts]) / sw.sum())
    else:
        e_start = 75.0
    if cameos.any():
        cw = w[cameos]
        e_cameo = float(np.dot(cw, arr[cameos]) / cw.sum())
    else:
        e_cameo = 20.0
    p_start *= availability
    p_cameo *= availability
    e_minutes = p_start * e_start + p_cameo * e_cameo
    return MinutesEstimate(p_start, p_cameo, e_minutes, p_start, p_cameo, p_start + p_cameo)


def _availability(player: dict[str, Any]) -> float:
    status = str(player.get("status") or "a")
    if status in STATUS_OUT:
        return 0.0
    chance = player.get("chance_of_playing_next_round")
    if chance is None:
        return 1.0
    frac = float(chance) / 100.0
    if status == "d":
        return frac
    return frac


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def is_summer_signing(player: dict[str, Any], cutoff: str = "2026-06-01") -> bool:
    raw = player.get("team_join_date")
    if not raw:
        return False
    try:
        joined = date.fromisoformat(str(raw)[:10])
        return joined >= date.fromisoformat(cutoff)
    except ValueError:
        return False


def _price_prior(player: dict[str, Any], cfg: dict[str, Any]) -> MinutesEstimate:
    np_cfg = cfg.get("new_player") or {}
    cost = int(player.get("now_cost") or 0)
    if cost >= int(np_cfg.get("premium_cost", 75)):
        p_start = float(np_cfg.get("p_start_premium", 0.75))
    elif cost >= int(np_cfg.get("mid_cost", 55)):
        p_start = float(np_cfg.get("p_start_mid", 0.40))
    else:
        p_start = float(np_cfg.get("p_start_cheap", 0.10))
    p_cameo = 0.10 if p_start < 0.7 else 0.05
    e_minutes = p_start * 82.0 + p_cameo * 18.0
    return MinutesEstimate(p_start, p_cameo, e_minutes, p_start, p_cameo, p_start + p_cameo)


def _from_season_totals(player: dict[str, Any]) -> MinutesEstimate:
    minutes = _f(player.get("minutes"))
    starts = _f(player.get("starts"))
    if minutes <= 0:
        return MinutesEstimate.zero()
    games = min(max(starts, minutes / 90.0, 1.0), 38.0)
    p_start = float(np.clip(starts / games, 0.0, 0.98))
    leftover_90s = max(minutes / 90.0 - starts * 0.90, 0.0)
    p_cameo = float(np.clip(leftover_90s / games, 0.0, 1.0 - p_start))
    e_start = float(np.clip(minutes / max(starts, 1.0), 60.0, 90.0)) if starts else 75.0
    e_minutes = p_start * e_start + p_cameo * 20.0
    return MinutesEstimate(p_start, p_cameo, e_minutes, p_start, p_cameo, p_start + p_cameo)


def _mix(a: MinutesEstimate, b: MinutesEstimate, w_b: float) -> MinutesEstimate:
    w = float(np.clip(w_b, 0.0, 1.0))
    p_start = (1 - w) * a.p_start + w * b.p_start
    p_cameo = (1 - w) * a.p_cameo + w * b.p_cameo
    p_cameo = min(p_cameo, max(0.0, 1.0 - p_start))
    e_minutes = (1 - w) * a.e_minutes + w * b.e_minutes
    return MinutesEstimate(p_start, p_cameo, e_minutes, p_start, p_cameo, p_start + p_cameo)


def _market_start_prob(player: dict[str, Any], full_ownership: float = 25.0) -> float:
    """Ownership is a GW1 minutes market. 25%+ ≈ nailed; 0% ≈ unknown."""
    sel = _f(player.get("selected_by_percent"))
    # logistic around 8% ownership
    return float(1.0 / (1.0 + np.exp(-(sel - 8.0) / 4.5)))


def _apply_avail(est: MinutesEstimate, avail: float) -> MinutesEstimate:
    return MinutesEstimate(
        est.p_start * avail,
        est.p_cameo * avail,
        est.e_minutes * avail,
        est.p_ge60 * avail,
        est.p_1_59 * avail,
        est.p_play * avail,
    )


def estimate_minutes(
    player: dict[str, Any],
    history: Sequence[dict[str, Any]] | None,
    cfg: dict[str, Any],
) -> MinutesEstimate:
    avail = _availability(player)
    if avail <= 0:
        return MinutesEstimate.zero()

    rows = list(history or [])
    recent = [float(r.get("minutes") or 0) for r in rows if r.get("minutes") is not None][-LOOKBACK:]
    if recent:
        return _from_minute_list(recent, avail)

    mins_cfg = cfg.get("minutes") or {}
    cutoff = str(mins_cfg.get("summer_cutoff") or "2026-06-01")
    last_w = float(mins_cfg.get("signing_last_season_weight", 0.40))
    full_own = float(mins_cfg.get("market_full_ownership", 25.0))

    season = _from_season_totals(player)
    price = _price_prior(player, cfg)
    market_p = _market_start_prob(player, full_own)
    market = MinutesEstimate(market_p, 0.08 * (1 - market_p), market_p * 80.0, market_p, 0.08 * (1 - market_p), 0.0)
    market.p_play = market.p_start + market.p_cameo
    market.e_minutes = market.p_start * 82.0 + market.p_cameo * 18.0
    market.p_ge60 = market.p_start
    market.p_1_59 = market.p_cameo

    # FPL's minutes/starts fields are this season only. Before ~10 games they are
    # a coin flip. Keep the price and ownership prior until the sample is large,
    # so one cameo cannot zero a player the pre-season prior still rates.
    prior = _mix(price, market, 0.45)
    if season.p_play <= 0:
        return _apply_avail(prior, avail)

    full = float(mins_cfg.get("full_sample_minutes", 900))
    need = full
    if is_summer_signing(player, cutoff):
        # A new club has no separate last-season feed. Ask for more minutes
        # before this season's start rate replaces the price prior.
        need = full / max(float(np.clip(last_w, 0.05, 1.0)), 0.05)
    trust = float(np.clip(_f(player.get("minutes")) / need, 0.0, 1.0)) if need > 0 else 1.0
    mixed = _mix(prior, season, trust)
    return _apply_avail(mixed, avail)
