from __future__ import annotations

from dataclasses import dataclass
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
    p_ge60 = p_start
    p_1_59 = p_cameo
    e_minutes = p_ge60 * e_start + p_1_59 * e_cameo
    return MinutesEstimate(
        p_start=p_start,
        p_cameo=p_cameo,
        e_minutes=float(e_minutes),
        p_ge60=p_ge60,
        p_1_59=p_1_59,
        p_play=p_ge60 + p_1_59,
    )


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


def _new_player_prior(player: dict[str, Any], cfg: dict[str, Any]) -> MinutesEstimate:
    np_cfg = cfg.get("new_player") or {}
    cost = int(player.get("now_cost") or 0)
    if cost >= int(np_cfg.get("premium_cost", 75)):
        p_start = float(np_cfg.get("p_start_premium", 0.75))
    elif cost >= int(np_cfg.get("mid_cost", 55)):
        p_start = float(np_cfg.get("p_start_mid", 0.40))
    else:
        p_start = float(np_cfg.get("p_start_cheap", 0.10))
    p_cameo = 0.10 if p_start < 0.7 else 0.05
    avail = _availability(player)
    p_start *= avail
    p_cameo *= avail
    e_minutes = p_start * 82.0 + p_cameo * 18.0
    return MinutesEstimate(p_start, p_cameo, e_minutes, p_start, p_cameo, p_start + p_cameo)


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

    minutes = float(player.get("minutes") or 0)
    starts = float(player.get("starts") or 0)
    if minutes < 90:
        return _new_player_prior(player, cfg)

    games = max(starts, minutes / 90.0, 1.0)
    # Cap the denominator at a full season so one-off cup sides aren't treated as ever-present.
    games = min(games, 38.0)
    p_start = float(np.clip(starts / games, 0.0, 0.98))
    leftover_90s = max(minutes / 90.0 - starts * 0.90, 0.0)
    p_cameo = float(np.clip(leftover_90s / games, 0.0, 1.0 - p_start))
    e_start = float(np.clip(minutes / max(starts, 1.0), 60.0, 90.0)) if starts else 75.0
    p_start *= avail
    p_cameo *= avail
    e_minutes = p_start * e_start + p_cameo * 20.0
    return MinutesEstimate(p_start, p_cameo, e_minutes, p_start, p_cameo, p_start + p_cameo)
