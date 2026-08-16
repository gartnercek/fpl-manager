from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fpl_manager.models.minutes import is_summer_signing

PK_SHARES = {1: 0.80, 2: 0.15, 3: 0.05}


@dataclass
class SetPieceRates:
    pk_goal: float = 0.0  # expected PK goals per 90-minute start, unscaled
    pk_miss: float = 0.0  # expected PK misses per start
    fk_xg90: float = 0.0
    corner_xa90: float = 0.0
    xg90_strip: float = 0.0  # subtract from season xG so pens are not double-counted
    xa90_strip: float = 0.0
    dc90: float = 0.0
    roles: list[str] = field(default_factory=list)


def _order(player: dict[str, Any], key: str) -> int:
    raw = player.get(key)
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def setpiece_rates(player: dict[str, Any], n90: float, cfg: dict[str, Any]) -> SetPieceRates:
    sp = cfg.get("set_pieces") or {}
    pk_match = float(sp.get("pk_per_match", 0.13))
    conv = float(sp.get("pk_conversion", 0.76))
    hist_pk = float(sp.get("historical_pk_xg90", 0.09))
    corner_xa = float(sp.get("corner_xa90", 0.16))
    fk_xg = float(sp.get("fk_xg90", 0.04))
    hist_corner = float(sp.get("historical_corner_xa90", 0.12))
    hist_fk = float(sp.get("historical_fk_xg90", 0.03))
    cutoff = str((cfg.get("minutes") or {}).get("summer_cutoff") or "2026-06-01")

    out = SetPieceRates()
    pk_ord = _order(player, "penalties_order")
    cr_ord = _order(player, "corners_and_indirect_freekicks_order")
    fk_ord = _order(player, "direct_freekicks_order")
    share = PK_SHARES.get(pk_ord, 0.0)
    awarded = pk_match * share
    out.pk_goal = awarded * conv
    out.pk_miss = awarded * (1.0 - conv)
    if pk_ord == 1:
        out.roles.append("PK")
    elif pk_ord == 2:
        out.roles.append("PK2")

    if cr_ord == 1:
        out.corner_xa90 = corner_xa
        out.roles.append("CK")
    elif cr_ord == 2:
        out.corner_xa90 = corner_xa * 0.35

    if fk_ord == 1:
        out.fk_xg90 = fk_xg
        out.roles.append("FK")

    stayer = n90 >= 15 and not is_summer_signing(player, cutoff)
    if stayer and pk_ord == 1:
        out.xg90_strip = hist_pk
    if stayer and cr_ord == 1:
        out.xa90_strip = hist_corner
    if stayer and fk_ord == 1:
        out.xg90_strip += hist_fk

    out.dc90 = max(_f(player.get("defensive_contribution_per_90")), 0.0)
    return out
