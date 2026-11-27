from __future__ import annotations

from dataclasses import fields, replace
from typing import Any

import requests

from fpl_manager.api import FPLClient, next_event
from fpl_manager.config import ROOT
from fpl_manager.planner import HALF_END, LAST_GW, PlannerConfig, SquadState, plan_gameweek
from fpl_manager.projection import Projection, ProjectionConfig, availability_from_status, project
from fpl_manager.report import bank_after, fingerprint, render_report
from fpl_manager.season import (
    SeasonData,
    _carry_team_prior,
    _prev_team_from_season,
    _vaastav_season,
    fetch_summaries,
    from_api,
)
from fpl_manager.squad import load_entry_state

VAASTAV = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


class SquadError(RuntimeError):
    pass


def _dataclass_from(cls, raw: dict[str, Any] | None):
    raw = dict(raw or {})
    names = {f.name for f in fields(cls)}
    obj = cls()
    clean = {k: v for k, v in raw.items() if k in names}
    for key in ("pool_per_pos", "assist_mult", "goal_mult"):
        if key in clean and isinstance(clean[key], dict):
            clean[key] = {int(k): float(v) if key != "pool_per_pos" else int(v) for k, v in clean[key].items()}
    if "bench_weight" in clean:
        clean["bench_weight"] = tuple(float(v) for v in clean["bench_weight"])
    return replace(obj, **clean)


def projection_config(cfg: dict[str, Any]) -> ProjectionConfig:
    return _dataclass_from(ProjectionConfig, cfg.get("projection"))


def planner_config(cfg: dict[str, Any]) -> PlannerConfig:
    return _dataclass_from(PlannerConfig, cfg.get("planner"))


def _previous_team_priors(bootstrap: dict[str, Any], season_label: str) -> dict[int, tuple[float, float]] | None:
    """Last season's xG team ratings (from the vaastav archive). None if it cannot be fetched."""
    year = int(season_label[:4])
    prev = f"{year - 1}-{str(year)[-2:]}"
    folder = ROOT / "data" / "vaastav" / prev
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("merged_gw.csv", "fixtures.csv", "teams.csv", "players_raw.csv"):
            path = folder / name
            if path.exists() and path.stat().st_size > 0:
                continue
            src = f"{VAASTAV}/{prev}/{'gws/' if name == 'merged_gw.csv' else ''}{name}"
            resp = requests.get(src, timeout=60)
            resp.raise_for_status()
            path.write_bytes(resp.content)
        pm, fx, _players, names = _vaastav_season(folder)
        ratings = _prev_team_from_season(pm, fx, sorted(names))
        now_names = {int(t["id"]): str(t["short_name"]) for t in bootstrap["teams"]}
        return _carry_team_prior(ratings, names, now_names)
    except Exception as exc:  # network or format problem: fall back to neutral priors
        print(f"previous-season team priors unavailable ({exc.__class__.__name__}); using neutral priors")
        return None


def load_season(client: FPLClient | None = None) -> SeasonData:
    client = client or FPLClient()
    bootstrap = client.bootstrap()
    fixtures = client.fixtures()
    ids = [int(p["id"]) for p in bootstrap["elements"]]
    summaries = fetch_summaries(client, ids)
    season = from_api(bootstrap, fixtures, summaries)
    priors = _previous_team_priors(bootstrap, season.season)
    if priors:
        season.prev_team = priors
    return season


def _market_overrides(season: SeasonData, bootstrap: dict[str, Any], gw: int, cfg: dict[str, Any]) -> dict | None:
    mkt = cfg.get("markets") or {}
    if not mkt.get("enabled", False):
        return None
    try:
        from fpl_manager.markets import (
            blend_lambda,
            fetch_team_totals,
            lambda_from_p_blank,
            lookup_blanks,
            map_fpl_teams,
            market_weight,
        )

        totals = fetch_team_totals()
    except Exception as exc:
        print(f"Polymarket fetch failed ({exc.__class__.__name__}); model lambdas only")
        return None
    names = map_fpl_teams(list(bootstrap["teams"]))
    out = {}
    fx = season.fixtures[season.fixtures.event == gw]
    for row in fx.itertuples():
        home, away = lookup_blanks(totals, int(row.team_h), int(row.team_a), row.kickoff, names)
        pair = []
        for side in (home, away):
            if side is None:
                pair.append(None)
                continue
            w = market_weight(
                side.liquidity,
                blend=float(mkt.get("blend", 0.7)),
                min_liquidity=float(mkt.get("min_liquidity", 80)),
                full_liquidity=float(mkt.get("full_liquidity", 800)),
            )
            pair.append((lambda_from_p_blank(side.p_blank), w) if w > 0 else None)
        if any(pair):
            out[(gw, int(row.team_h), int(row.team_a))] = (pair, blend_lambda)
    print(f"Polymarket team totals: {len(out)} fixtures blended")
    return out or None


def build_predictions(cfg: dict[str, Any], *, fetch_summaries: bool = True) -> dict[str, Any]:
    client = FPLClient()
    season = load_season(client)
    bootstrap = client.bootstrap()
    event = next_event(bootstrap)
    gw = int(event["id"])
    pcfg = projection_config(cfg)
    plcfg = planner_config(cfg)
    half_end = HALF_END if gw <= HALF_END else LAST_GW
    horizon = max(plcfg.horizon, half_end - gw + 1)
    avail = availability_from_status(season.status)
    overrides = _market_overrides(season, bootstrap, gw, cfg)
    proj = project(season, gw, horizon, pcfg, avail, market=overrides)
    save_predictions(proj)
    return {
        "client": client,
        "bootstrap": bootstrap,
        "event": event,
        "season": season,
        "proj": proj,
        "team_names": season.team_names,
        "pcfg": pcfg,
        "plcfg": plcfg,
    }


def save_predictions(proj: Projection) -> None:
    out_dir = ROOT / "data" / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        proj.table.to_parquet(out_dir / f"gw{proj.gw}.parquet")
    except Exception:
        proj.table.to_csv(out_dir / f"gw{proj.gw}.csv")


def letter_for_team(
    cfg: dict[str, Any],
    shared: dict[str, Any],
    team_id: int,
    *,
    free_transfers: int | None = None,
    require_squad: bool = False,
) -> dict[str, Any]:
    bootstrap = shared["bootstrap"]
    event = shared["event"]
    proj: Projection = shared["proj"]
    client = shared["client"]
    gw = int(event["id"])
    plcfg: PlannerConfig = shared["plcfg"]
    state = SquadState.fresh()
    squad_info: dict[str, Any] = {}
    if team_id:
        now_costs = {int(p["id"]): int(p["now_cost"]) for p in bootstrap["elements"]}
        topups = {int(k): int(v) for k, v in (cfg.get("ft_topups") or {}).items()}
        season = shared["season"]

        def price_at_gw(g: int) -> dict[int, int]:
            if g <= 1 and season.start_prices:
                return season.start_prices
            return season.price_at(g)

        squad_info = load_entry_state(client, team_id, gw, now_costs, topups=topups, price_at_gw=price_at_gw)
        ids = sorted(squad_info["current_ids"])
        if require_squad and not ids:
            raise SquadError(f"No public squad for team {team_id}")
        if ids:
            ft = squad_info.get("free_transfers")
            if ft is None:
                ft = int(free_transfers or 1)
            state = SquadState(
                squad=ids,
                sell=dict(squad_info["sell_prices"]),
                bank=int(squad_info["bank"]),
                ft=int(ft),
                chips_left=dict(squad_info["chips_left"]),
            )
    xp_full = proj.xp_matrix()
    xp = xp_full[[w for w in xp_full.columns if w < gw + plcfg.horizon]]
    players = proj.table[["pos", "team", "price"]].copy()
    plan = plan_gameweek(xp, players, state, gw, plcfg, p_start=proj.table.p_start, xp_full=xp_full)
    left = bank_after(plan, proj, state)
    if left < 0:
        raise SquadError(
            f"team {team_id}: plan is {-left / 10:.1f}m over budget at today's prices; no letter sent"
        )
    timezone = str((cfg.get("email") or {}).get("timezone") or "Europe/Ljubljana")
    subject, text, html = render_report(
        event=event,
        plan=plan,
        proj=proj,
        state=state,
        timezone=timezone,
        team_names=shared["team_names"],
        pending_transfers=int(squad_info.get("pending_transfers") or 0),
    )
    return {
        "plan": plan,
        "state": state,
        "subject": subject,
        "text": text,
        "html": html,
        "fp": fingerprint(plan),
    }


def run_pipeline(cfg: dict[str, Any], *, fetch_summaries: bool = True) -> dict[str, Any]:
    shared = build_predictions(cfg, fetch_summaries=fetch_summaries)
    letter = letter_for_team(cfg, shared, int(cfg.get("team_id") or 0))
    return {**shared, **letter}
