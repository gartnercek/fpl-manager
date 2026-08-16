from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fpl_manager.config import ROOT
from fpl_manager.fixtures import Matchup
from fpl_manager.models.minutes import MinutesEstimate, estimate_minutes, is_summer_signing
from fpl_manager.models.returns import PositionPriors, build_priors, mean_overall, shrink_rate, team_xgc90
from fpl_manager.models.setpieces import setpiece_rates
from fpl_manager.models.simulate import simulate_match

POSITION_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


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
    mu_by_gw: dict[int, float] = field(default_factory=dict)
    sigma2_by_gw: dict[int, float] = field(default_factory=dict)
    fixtures: list[FixtureScore] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)

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


def form_attack_multiplier(player: dict[str, Any], history: list[dict[str, Any]] | None, cfg: dict[str, Any]) -> float:
    """Scale xG/xA. Uses last-6 xG when matches exist; else FPL `form` (avg pts ~30d)."""
    form_cfg = cfg.get("form") or {}
    recent_w = float(form_cfg.get("recent_xg_weight", 0.45))
    slope = float(form_cfg.get("points_slope", 0.08))
    rows = [r for r in (history or []) if _f(r.get("minutes")) > 0][-6:]
    if rows:
        mins = sum(_f(r.get("minutes")) for r in rows)
        if mins >= 120:
            xg = sum(_f(r.get("expected_goals")) for r in rows)
            recent_xg90 = xg / (mins / 90.0)
            season_xg90 = _f(player.get("expected_goals_per_90"))
            if season_xg90 > 0:
                ratio = recent_xg90 / season_xg90
                return float(np.clip(1.0 + recent_w * (ratio - 1.0), 0.70, 1.35))
    form = _f(player.get("form"))
    if form <= 0:
        return 1.0
    # Typical FPL form sits near 2–6. 4.0 ≈ average.
    return float(np.clip(1.0 + slope * (form - 4.0), 0.75, 1.30))


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
    rng: np.random.Generator | None = None,
) -> PlayerPrediction:
    pos = int(player["element_type"])
    minutes_played = _f(player.get("minutes"))
    raw_n90 = minutes_played / 90.0
    n90 = raw_n90
    mins_cfg = cfg.get("minutes") or {}
    if is_summer_signing(player, str(mins_cfg.get("summer_cutoff") or "2026-06-01")):
        n90 *= 0.4
    xg90 = shrink_rate(_f(player.get("expected_goals_per_90")), n90, priors.xg90[pos])
    xa90 = shrink_rate(_f(player.get("expected_assists_per_90")), n90, priors.xa90[pos])
    form_m = form_attack_multiplier(player, history, cfg)
    xg90 *= form_m
    xa90 *= form_m
    pieces = setpiece_rates(player, raw_n90, cfg)
    xg90 = max(0.0, xg90 - pieces.xg90_strip)
    xa90 = max(0.0, xa90 - pieces.xa90_strip)
    yc90 = shrink_rate(
        (_f(player.get("yellow_cards")) / n90) if n90 > 0 else priors.yc90[pos],
        n90,
        priors.yc90[pos],
    )
    saves90 = shrink_rate(_f(player.get("saves_per_90")), n90, priors.saves90[pos])
    mins = estimate_minutes(player, history, cfg)
    rng = rng or np.random.default_rng(int(player.get("id") or 0) + 17)
    draws = int(cfg.get("simulate_draws") or 1500)

    by_gw: dict[int, float] = {}
    var_by_gw: dict[int, float] = {}
    next_fx: list[FixtureScore] = []
    next_event = events[0]
    for event in events:
        matchups = matchups_by_event.get(event, [])
        gw_mu = 0.0
        gw_var = 0.0
        for matchup in matchups:
            mu, var, p_cs = simulate_match(
                player,
                mins,
                xg90,
                xa90,
                yc90,
                saves90,
                matchup,
                teams,
                team_xgc,
                mean_r,
                rng,
                draws,
                pieces,
            )
            gw_mu += mu
            gw_var += var
            if event == next_event:
                next_fx.append(
                    FixtureScore(mu, var, p_cs, matchup.opponent, matchup.is_home, matchup.difficulty)
                )
        by_gw[event] = gw_mu
        var_by_gw[event] = gw_var

    ep_next = _f(player.get("ep_next"))
    ep_w = float(cfg.get("ep_next_blend", 0.35))
    sim_mu = by_gw.get(next_event, 0.0)
    if ep_next > 0 and sim_mu > 0 and ep_w > 0:
        by_gw[next_event] = (1.0 - ep_w) * sim_mu + ep_w * ep_next
        var_by_gw[next_event] *= (by_gw[next_event] / sim_mu) ** 2

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
        ep_next=ep_next,
        minutes=mins,
        xg90=xg90,
        xa90=xa90,
        mu=by_gw.get(next_event, 0.0),
        sigma2=var_by_gw.get(next_event, 0.0),
        mu_horizon=horizon,
        mu_by_gw=by_gw,
        sigma2_by_gw=var_by_gw,
        fixtures=next_fx,
        roles=pieces.roles,
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
    rng = np.random.default_rng(2026)
    preds: list[PlayerPrediction] = []
    for player in players:
        if player.get("removed"):
            continue
        team_id = int(player["team"])
        preds.append(
            predict_player(
                player,
                histories.get(int(player["id"])),
                fixtures_by_team.get(team_id, {}),
                events,
                teams,
                priors,
                xgc,
                mean_r,
                cfg,
                rng,
            )
        )
    preds.sort(key=lambda p: (p.mu_horizon.get(len(events), p.mu), p.mu), reverse=True)
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
                "mu_5": round(p.mu_horizon.get(5, p.mu_horizon.get(6, 0.0)), 2),
                "mu_6": round(p.mu_horizon.get(6, 0.0), 2),
                "position": p.position,
                "team_id": p.team_id,
                "now_cost": p.now_cost,
                "sigma2": p.sigma2,
                "roles": "/".join(p.roles),
            }
        )
    return pd.DataFrame(rows)


def save_predictions(df: pd.DataFrame, event_id: int) -> None:
    out_dir = ROOT / "data" / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / f"gw{event_id}.parquet", index=False)
