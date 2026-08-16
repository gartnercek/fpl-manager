from __future__ import annotations

from typing import Any

import numpy as np

from fpl_manager.fixtures import Matchup
from fpl_manager.models.minutes import MinutesEstimate
from fpl_manager.models.returns import (
    ASSIST_POINTS,
    CS_POINTS,
    GOAL_POINTS,
    attack_scale,
    concede_lambda,
)
from fpl_manager.models.setpieces import SetPieceRates

BONUS_FACTOR = 0.3
DEFCON_THRESHOLD = {2: 10, 3: 12, 4: 12}


def simulate_match(
    player: dict[str, Any],
    mins: MinutesEstimate,
    xg90: float,
    xa90: float,
    yc90: float,
    saves90: float,
    matchup: Matchup,
    teams: dict[int, dict[str, Any]],
    team_xgc: dict[int, float],
    mean_r: float,
    rng: np.random.Generator,
    draws: int,
    pieces: SetPieceRates | None = None,
) -> tuple[float, float, float]:
    """Monte Carlo one fixture. Minutes, goals, CS, pens, DEFCON share the same draws."""
    pos = int(player["element_type"])
    team_id = int(player["team"])
    atk = attack_scale(teams, team_id, matchup, mean_r)
    lam_conc = concede_lambda(teams, team_xgc, team_id, matchup, mean_r)
    p_cs = float(np.exp(-lam_conc))
    if mins.p_play <= 0 or draws <= 0:
        return 0.0, 0.0, p_cs
    pieces = pieces or SetPieceRates()

    u = rng.random(draws)
    start = u < mins.p_start
    cameo = (~start) & (u < mins.p_start + mins.p_cameo)
    minutes = np.zeros(draws)
    n_start = int(start.sum())
    n_cameo = int(cameo.sum())
    if n_start:
        minutes[start] = rng.normal(82.0, 6.0, n_start).clip(60.0, 90.0)
    if n_cameo:
        minutes[cameo] = rng.normal(22.0, 8.0, n_cameo).clip(1.0, 59.0)
    ge60 = minutes >= 60.0
    played = minutes > 0
    app = np.where(ge60, 2.0, np.where(played, 1.0, 0.0))

    min90 = minutes / 90.0
    open_g = rng.poisson(np.maximum(xg90 * min90 * atk, 0.0))
    open_a = rng.poisson(np.maximum(xa90 * min90 * atk, 0.0))
    fk_g = rng.poisson(np.maximum(pieces.fk_xg90 * min90 * atk, 0.0))
    ck_a = rng.poisson(np.maximum(pieces.corner_xa90 * min90 * atk, 0.0))
    # Pens are a start-time event, scaled by how much you dominate the match.
    pk_g = np.zeros(draws, dtype=int)
    pk_m = np.zeros(draws, dtype=int)
    if n_start and (pieces.pk_goal > 0 or pieces.pk_miss > 0):
        pk_g[start] = rng.poisson(max(pieces.pk_goal * atk, 0.0), n_start)
        pk_m[start] = rng.poisson(max(pieces.pk_miss * atk, 0.0), n_start)

    goals = open_g + fk_g + pk_g
    assists = open_a + ck_a
    opp_goals = rng.poisson(max(lam_conc, 0.0), draws)
    cs = (opp_goals == 0) & ge60
    gp = GOAL_POINTS[pos]
    pts = app + goals * gp + assists * ASSIST_POINTS + cs * CS_POINTS[pos]
    pts = pts + BONUS_FACTOR * (goals + assists)
    pts = pts - rng.poisson(np.maximum(yc90 * min90, 0.0))
    pts = pts - 2.0 * pk_m

    if pos == 1:
        base = max(team_xgc.get(team_id, 1.35), 0.4)
        lam_s = np.maximum(saves90 * min90 * (lam_conc / base), 0.0)
        pts = pts + (rng.poisson(lam_s) // 3)
    if pos in (1, 2):
        pts = pts - ((opp_goals // 2) * ge60)

    thresh = DEFCON_THRESHOLD.get(pos)
    if thresh and pieces.dc90 > 0:
        dc = rng.poisson(np.maximum(pieces.dc90 * min90, 0.0))
        pts = pts + 2.0 * ((dc >= thresh) & (minutes >= 45.0))

    mu = float(pts.mean())
    var = float(pts.var())
    return mu, max(var, 0.0), p_cs
