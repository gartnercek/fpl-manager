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

BONUS_FACTOR = 0.3


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
) -> tuple[float, float, float]:
    """Monte Carlo one fixture. Minutes, goals, CS share the same draws (not independent addends)."""
    pos = int(player["element_type"])
    team_id = int(player["team"])
    atk = attack_scale(teams, team_id, matchup, mean_r)
    lam_conc = concede_lambda(teams, team_xgc, team_id, matchup, mean_r)
    p_cs = float(np.exp(-lam_conc))
    if mins.p_play <= 0 or draws <= 0:
        return 0.0, 0.0, p_cs

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
    goals = rng.poisson(np.maximum(xg90 * min90 * atk, 0.0))
    assists = rng.poisson(np.maximum(xa90 * min90 * atk, 0.0))
    opp_goals = rng.poisson(max(lam_conc, 0.0), draws)
    cs = (opp_goals == 0) & ge60
    gp = GOAL_POINTS[pos]
    pts = app + goals * gp + assists * ASSIST_POINTS + cs * CS_POINTS[pos]
    pts = pts + BONUS_FACTOR * (goals + assists)
    pts = pts - rng.poisson(np.maximum(yc90 * min90, 0.0))

    if pos == 1:
        base = max(team_xgc.get(team_id, 1.35), 0.4)
        lam_s = np.maximum(saves90 * min90 * (lam_conc / base), 0.0)
        pts = pts + (rng.poisson(lam_s) // 3)
    if pos in (1, 2):
        pts = pts - ((opp_goals // 2) * ge60)

    mu = float(pts.mean())
    var = float(pts.var())
    return mu, max(var, 0.0), p_cs
