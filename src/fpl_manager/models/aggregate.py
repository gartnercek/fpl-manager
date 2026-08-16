from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fpl_manager.config import ROOT
from fpl_manager.fixtures import Matchup
from fpl_manager.models.minutes import MinutesEstimate, estimate_minutes
from fpl_manager.models.returns import (
    ASSIST_POINTS,
    CS_POINTS,
    GOAL_POINTS,
    PositionPriors,
    attack_scale,
    build_priors,
    concede_lambda,
    expected_floor_div,
    mean_overall,
    shrink_rate,
    team_xgc90,
)

POSITION_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
BONUS_FACTOR = 0.3  # crude v1: bonus ≈ 0.3 × E[goals + assists]


@dataclass
class FixtureScore:
    mu: float
    sigma2: float
    p_cs: float
    opponent: int
    is_home: bool
    difficulty: int


@dataclass
class PlayerPrediction:
    player_id: int
    web_name: str
    team_id: int
    position: int
    now_cost: int
    status: str
    news: str
    ep_next: float
    minutes: MinutesEstimate
    xg90: float
    xa90: float
    mu: float
    sigma2: float
    mu_horizon: dict[int, float]
    fixtures: list[FixtureScore] = field(default_factory=list)

    @property
    def sd(self) -> float:
        return float(np.sqrt(max(self.sigma2, 0.0)))

    @property
    def position_name(self) -> str:
        return POSITION_NAME.get(self.position, "?")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _match_points(
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
) -> FixtureScore:
    pos = int(player["element_type"])
    team_id = int(player["team"])
    atk = attack_scale(teams, team_id, matchup, mean_r)
    lam_conc = concede_lambda(teams, team_xgc, team_id, matchup, mean_r)
    p_cs = float(np.exp(-lam_conc))

    e_min90 = mins.e_minutes / 90.0
    lam_g = max(xg90 * e_min90 * atk, 0.0)
    lam_a = max(xa90 * e_min90 * atk, 0.0)
    gp = GOAL_POINTS[pos]
    mu_g = lam_g * gp
    var_g = lam_g * gp * gp
    mu_a = lam_a * ASSIST_POINTS
    var_a = lam_a * ASSIST_POINTS * ASSIST_POINTS

    mu_app = 2.0 * mins.p_ge60 + 1.0 * mins.p_1_59
    var_app = (
        (4.0 * mins.p_ge60 + 1.0 * mins.p_1_59)
        - mu_app**2
    )
    var_app = max(var_app, 0.0)

    cs_pts = CS_POINTS[pos]
    mu_cs = mins.p_ge60 * p_cs * cs_pts
    # Bernoulli-like on the CS event, scaled by play probability.
    p_cs_pts = mins.p_ge60 * p_cs
    var_cs = p_cs_pts * (1.0 - p_cs_pts) * cs_pts * cs_pts

    mu_saves = 0.0
    var_saves = 0.0
    mu_gc = 0.0
    var_gc = 0.0
    if pos == 1:
        lam_saves = max(saves90 * e_min90 * (lam_conc / max(team_xgc.get(team_id, 1.35), 0.4)), 0.0)
        mu_saves, var_saves = expected_floor_div(lam_saves, 3, k_max=18)
    if pos in (1, 2):
        lam_gc = lam_conc * mins.p_ge60
        mean_pen, var_pen = expected_floor_div(lam_gc, 2, k_max=12)
        mu_gc = -mean_pen
        var_gc = var_pen

    lam_yc = max(yc90 * e_min90, 0.0)
    mu_yc = -lam_yc
    var_yc = lam_yc

    mu_bonus = BONUS_FACTOR * (lam_g + lam_a)
    var_bonus = (BONUS_FACTOR**2) * (lam_g + lam_a)

    mu = mu_app + mu_g + mu_a + mu_cs + mu_saves + mu_gc + mu_yc + mu_bonus
    sigma2 = var_app + var_g + var_a + var_cs + var_saves + var_gc + var_yc + var_bonus
    return FixtureScore(
        mu=float(mu),
        sigma2=float(max(sigma2, 0.0)),
        p_cs=p_cs,
        opponent=matchup.opponent,
        is_home=matchup.is_home,
        difficulty=matchup.difficulty,
    )


def predict_player(
    player: dict[str, Any],
    history: list[dict[str, Any]] | None,
    matchups_by_event: dict[int, list[Matchup]],
    events: list[int],
    teams: dict[int, dict[str, Any]],
    priors: PositionPriors,
    team_xgc: dict[int, float],
    mean_r: float,
    cfg: dict[str, Any],
) -> PlayerPrediction:
    pos = int(player["element_type"])
    minutes_played = _f(player.get("minutes"))
    n90 = minutes_played / 90.0
    xg90 = shrink_rate(_f(player.get("expected_goals_per_90")), n90, priors.xg90[pos])
    xa90 = shrink_rate(_f(player.get("expected_assists_per_90")), n90, priors.xa90[pos])
    yc90 = shrink_rate(
        (_f(player.get("yellow_cards")) / n90) if n90 > 0 else priors.yc90[pos],
        n90,
        priors.yc90[pos],
    )
    saves90 = shrink_rate(_f(player.get("saves_per_90")), n90, priors.saves90[pos])
    mins = estimate_minutes(player, history, cfg)

    by_gw: dict[int, float] = {}
    next_mu = 0.0
    next_var = 0.0
    next_fx: list[FixtureScore] = []
    next_event = events[0]
    for event in events:
        matchups = matchups_by_event.get(event, [])
        gw_mu = 0.0
        gw_var = 0.0
        for matchup in matchups:
            scored = _match_points(
                player, mins, xg90, xa90, yc90, saves90, matchup, teams, team_xgc, mean_r
            )
            gw_mu += scored.mu
            gw_var += scored.sigma2
            if event == next_event:
                next_fx.append(scored)
        by_gw[event] = gw_mu
        if event == next_event:
            next_mu, next_var = gw_mu, gw_var

    horizon: dict[int, float] = {}
    running = 0.0
    for i, event in enumerate(events, start=1):
        running += by_gw.get(event, 0.0)
        horizon[i] = running

    return PlayerPrediction(
        player_id=int(player["id"]),
        web_name=str(player.get("web_name") or player.get("second_name") or player["id"]),
        team_id=int(player["team"]),
        position=pos,
        now_cost=int(player.get("now_cost") or 0),
        status=str(player.get("status") or "a"),
        news=str(player.get("news") or ""),
        ep_next=_f(player.get("ep_next")),
        minutes=mins,
        xg90=xg90,
        xa90=xa90,
        mu=next_mu,
        sigma2=next_var,
        mu_horizon=horizon,
        fixtures=next_fx,
    )


def predict_all(
    bootstrap: dict[str, Any],
    fixtures_by_team: dict[int, dict[int, list[Matchup]]],
    events: list[int],
    cfg: dict[str, Any],
    histories: dict[int, list[dict[str, Any]]] | None = None,
) -> list[PlayerPrediction]:
    players = bootstrap["elements"]
    teams = {int(t["id"]): t for t in bootstrap["teams"]}
    priors = build_priors(players)
    xgc = team_xgc90(players)
    mean_r = mean_overall(teams)
    histories = histories or {}
    preds: list[PlayerPrediction] = []
    for player in players:
        if player.get("removed"):
            continue
        team_id = int(player["team"])
        matchups_by_event = fixtures_by_team.get(team_id, {})
        preds.append(
            predict_player(
                player,
                histories.get(int(player["id"])),
                matchups_by_event,
                events,
                teams,
                priors,
                xgc,
                mean_r,
                cfg,
            )
        )
    preds.sort(key=lambda p: p.mu, reverse=True)
    return preds


def predictions_frame(preds: list[PlayerPrediction], team_names: dict[int, str]) -> pd.DataFrame:
    rows = []
    for p in preds:
        opp = ", ".join(
            f"{'H' if fx.is_home else 'A'} vs {team_names.get(fx.opponent, fx.opponent)} (FDR {fx.difficulty})"
            for fx in p.fixtures
        ) or "BLANK"
        rows.append(
            {
                "id": p.player_id,
                "name": p.web_name,
                "pos": p.position_name,
                "team": team_names.get(p.team_id, p.team_id),
                "price": p.now_cost / 10.0,
                "mu": round(p.mu, 2),
                "sd": round(p.sd, 2),
                "p_play": round(p.minutes.p_play, 2),
                "p_ge60": round(p.minutes.p_ge60, 2),
                "xg90": round(p.xg90, 2),
                "xa90": round(p.xa90, 2),
                "fixture": opp,
                "ep_next": p.ep_next,
                "status": p.status,
                "news": p.news,
                "mu_3": round(p.mu_horizon.get(3, 0.0), 2),
                "mu_6": round(p.mu_horizon.get(6, 0.0), 2),
                "position": p.position,
                "team_id": p.team_id,
                "now_cost": p.now_cost,
                "sigma2": p.sigma2,
            }
        )
    return pd.DataFrame(rows)


def save_predictions(df: pd.DataFrame, event_id: int) -> None:
    out_dir = ROOT / "data" / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / f"gw{event_id}.parquet", index=False)
