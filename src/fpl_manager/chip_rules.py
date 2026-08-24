from __future__ import annotations

from math import e as EULER_E
from typing import Any, Iterable

import numpy as np

FIRST_HALF_LAST_GW = 19
LAST_GW = 38
FIRST_REBUILD_GW = 2


def chip_name(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.lower().replace(" ", "")
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("chip") or "").lower().replace(" ", "")
    return str(raw or "").lower().replace(" ", "")


def half_bounds(event_id: int) -> tuple[int, int]:
    if event_id <= FIRST_HALF_LAST_GW:
        return 1, FIRST_HALF_LAST_GW
    return FIRST_HALF_LAST_GW + 1, LAST_GW


def _event_of(raw: Any) -> int | None:
    if isinstance(raw, dict) and raw.get("event") is not None:
        try:
            return int(raw["event"])
        except (TypeError, ValueError):
            return None
    return None


def used_in_half(chip_records: Iterable[Any], event_id: int) -> set[str]:
    """Chip names already played in the current half (GW1–19 or GW20–38)."""
    lo, hi = half_bounds(event_id)
    used: set[str] = set()
    for raw in chip_records or []:
        name = chip_name(raw)
        if not name:
            continue
        ev = _event_of(raw)
        if ev is None or lo <= ev <= hi:
            used.add(name)
    return used


def _aliases(kind: str) -> tuple[str, ...]:
    if kind == "wildcard":
        return ("wildcard", "wc")
    if kind == "freehit":
        return ("freehit", "free_hit", "fh")
    if kind == "triple":
        return ("3xc", "triplecaptain", "triple")
    if kind == "bboost":
        return ("bboost", "benchboost", "bench")
    return (kind,)


def _kind_used(used: set[str], kind: str) -> bool:
    aliases = _aliases(kind)
    return any(any(a == u or a in u for a in aliases) for u in used)


def wildcard_available(chip_records: Iterable[Any], event_id: int) -> bool:
    if event_id < 2:
        return False
    return not _kind_used(used_in_half(chip_records, event_id), "wildcard")


def free_hit_available(chip_records: Iterable[Any], event_id: int) -> bool:
    if event_id < 2:
        return False
    return not _kind_used(used_in_half(chip_records, event_id), "freehit")


def expiry_adjusted_threshold(base: float, event_id: int) -> float:
    """Lower the bar as the half ends so unused WC/FH are not taken to the grave."""
    _, hi = half_bounds(event_id)
    left = hi - event_id
    if left <= 0:
        return 0.0
    if left <= 3:
        return float(base) * 0.35
    return float(base)


def first_rebuild_gw(event_id: int) -> int:
    return FIRST_REBUILD_GW if event_id <= FIRST_HALF_LAST_GW else FIRST_HALF_LAST_GW + 1


def trust_weight(event_id: int, prior_gws: float = 5.0) -> float:
    """How much to believe this week's WC/FH gap. GW2 has one result; shrink hard."""
    n_obs = max(event_id - 1, 0)
    return n_obs / (n_obs + max(prior_gws, 0.0))


def in_explore_window(event_id: int, explore_fraction: float = 1.0 / EULER_E) -> bool:
    """Secretary-sample: first n/e usable weeks of the half are for looking, not stopping."""
    lo = first_rebuild_gw(event_id)
    _, hi = half_bounds(event_id)
    n = hi - lo + 1
    explore_n = max(1, int(round(n * explore_fraction)))
    week_index = event_id - lo + 1
    return week_index <= explore_n


def expected_best_remaining(
    event_id: int,
    *,
    typical: float,
    noise_sd: float,
    p_future_dgw: float,
    dgw_multiplier: float,
    draws: int,
    rng: np.random.Generator,
) -> tuple[float, int | None]:
    """Cardinal option value of waiting: one later swing week, plus a possible DGW."""
    _, hi = half_bounds(event_id)
    n_left = hi - event_id
    if n_left <= 0:
        return 0.0, None
    future = [(event_id + i, typical, False) for i in range(1, min(n_left, 4) + 1)]
    confirmed_dgw = False
    samples = []
    for _ in range(max(draws, 1)):
        rolled = [(gw, max(0.0, val + rng.normal(0.0, noise_sd))) for gw, val, _ in future]
        if not confirmed_dgw and rng.random() < p_future_dgw:
            base = max(typical, 0.0)
            rolled.append((-1, max(0.0, base * dgw_multiplier + rng.normal(0.0, noise_sd))))
        gw_star, v_star = max(rolled, key=lambda x: x[1])
        samples.append((v_star, gw_star))
    mean_v = float(np.mean([s[0] for s in samples]))
    gws = [s[1] for s in samples if s[1] != -1]
    best_gw = int(max(set(gws), key=gws.count)) if gws else None
    return mean_v, best_gw


def decide_rebuild_chip(
    gap: float,
    event_id: int,
    *,
    bar: float,
    typical: float,
    noise_sd: float,
    override: float,
    prior_gws: float,
    explore_fraction: float,
    epsilon: float,
    p_future_dgw: float,
    dgw_multiplier: float,
    draws: int,
    rng: np.random.Generator | None = None,
) -> tuple[bool, str]:
    """Play a once-per-half rebuild chip only if now beats waiting.

    The MIP gap is shrunk (early GWs are noisy). The first n/e weeks of the half
    are an explore window unless the shrunk gap is an override outlier. After that,
    stop iff the trusted gap beats E[best remaining swing] − epsilon.
    """
    rng = rng or np.random.default_rng(event_id + 17)
    w = trust_weight(event_id, prior_gws)
    g_hat = float(gap) * w
    exploring = in_explore_window(event_id, explore_fraction)
    e_max, best_gw = expected_best_remaining(
        event_id,
        typical=typical,
        noise_sd=noise_sd,
        p_future_dgw=p_future_dgw,
        dgw_multiplier=dgw_multiplier,
        draws=draws,
        rng=rng,
    )
    gw_bit = f" (likely GW{best_gw})" if best_gw else ""
    if exploring and g_hat < override:
        return False, (
            f"held at GW{event_id}: trusted gap {g_hat:.1f} (raw {gap:.1f} × trust {w:.2f}) "
            f"in first {explore_fraction:.2f} of the half — wait unless ≥ {override:.0f}."
        )
    if g_hat < bar:
        return False, (
            f"held: trusted gap {g_hat:.1f} < {bar:.1f} bar "
            f"(raw {gap:.1f}, trust {w:.2f})."
        )
    if g_hat + epsilon < e_max:
        return False, (
            f"held: trusted gap {g_hat:.1f} vs expected best future{gw_bit} {e_max:.1f}."
        )
    return True, (
        f"play: trusted gap {g_hat:.1f} ≥ max({bar:.1f}, future{gw_bit} {e_max:.1f} − {epsilon})."
    )
