"""Point-in-time season data.

Everything the projection model and the backtest need is built from per-match rows
(one row per player per fixture). The same tables come from two sources:

* the live FPL API (`from_api`): `element-summary/{id}` history + `fixtures/` + `bootstrap-static/`
* a vaastav/Fantasy-Premier-League season folder (`from_vaastav`), used to replay past seasons

so the model that is backtested is exactly the model that writes the weekly letter.
"""
from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

POS_CODE = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}

# Per-match columns kept from FPL history rows (FPL name -> our name).
STAT_COLUMNS = {
    "minutes": "minutes",
    "starts": "starts",
    "goals_scored": "goals",
    "assists": "assists",
    "expected_goals": "xg",
    "expected_assists": "xa",
    "expected_goals_conceded": "xgc",
    "clean_sheets": "cs",
    "goals_conceded": "gc",
    "saves": "saves",
    "bonus": "bonus",
    "bps": "bps",
    "defensive_contribution": "dc",
    "yellow_cards": "yc",
    "red_cards": "rc",
    "penalties_missed": "pen_miss",
    "penalties_saved": "pen_save",
    "own_goals": "og",
    "total_points": "points",
    "value": "value",
    "selected": "selected",
    "transfers_balance": "t_bal",
}

PREV_COLUMNS = ["minutes", "starts", "xg", "xa", "bonus", "saves", "yc", "dc", "points", "xgc", "cs", "goals", "assists"]


@dataclass
class SeasonData:
    season: str
    players: pd.DataFrame  # element, code, name, pos, team, price (latest known, tenths)
    pm: pd.DataFrame  # player-match rows (finished fixtures only)
    fixtures: pd.DataFrame  # id, event, team_h, team_a, kickoff, finished, h_score, a_score
    team_names: dict[int, str]
    prev: pd.DataFrame  # previous-season totals keyed by player code
    prev_team: dict[int, tuple[float, float]] = field(default_factory=dict)  # team -> (attack, defence)
    status: pd.DataFrame | None = None  # live only: element, status, chance, news

    @property
    def last_finished_gw(self) -> int:
        done = self.fixtures[self.fixtures.finished]
        if done.empty:
            return 0
        return int(done.event.max())

    def price_at(self, gw: int) -> dict[int, int]:
        """Buy price (tenths) at the GW `gw` deadline. Latest earlier price if no row that GW."""
        rows = self.pm[self.pm.gw <= gw].sort_values(["gw", "kickoff"])
        last = rows.groupby("element")["value"].last()
        out = {int(k): int(v) for k, v in last.items() if v and v > 0}
        for pid, price in zip(self.players.element, self.players.price):
            out.setdefault(int(pid), int(price))
        return out


def _num(df: pd.DataFrame, cols: list[str]) -> None:
    for col in cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)


def _team_of_row(pm: pd.DataFrame, fixtures: pd.DataFrame) -> pd.Series:
    fx = fixtures.set_index("id")
    th = pm["fixture"].map(fx["team_h"])
    ta = pm["fixture"].map(fx["team_a"])
    return np.where(pm["home"], th, ta).astype(int)


def _fixtures_frame(rows: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(rows).copy()
    df = df[df["event"].notna()].copy()
    out = pd.DataFrame(
        {
            "id": df["id"].astype(int),
            "event": df["event"].astype(int),
            "team_h": df["team_h"].astype(int),
            "team_a": df["team_a"].astype(int),
            "kickoff": df["kickoff_time"].astype(str),
            "finished": df["finished"].astype(str).str.lower().isin(["true", "1"]),
            "h_score": pd.to_numeric(df.get("team_h_score"), errors="coerce"),
            "a_score": pd.to_numeric(df.get("team_a_score"), errors="coerce"),
        }
    )
    return out.sort_values(["event", "kickoff", "id"]).reset_index(drop=True)


def _clean_pm(raw: pd.DataFrame, fixtures: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=STAT_COLUMNS).copy()
    df["element"] = df["element"].astype(int)
    df["fixture"] = df["fixture"].astype(int)
    df["gw"] = df["round"].astype(int)
    df["home"] = df["was_home"].astype(str).str.lower().isin(["true", "1"])
    df["opp"] = df["opponent_team"].astype(int)
    df["kickoff"] = df["kickoff_time"].astype(str)
    _num(df, list(STAT_COLUMNS.values()))
    finished = set(fixtures.loc[fixtures.finished, "id"].astype(int))
    df = df[df["fixture"].isin(finished)].copy()
    df["team"] = _team_of_row(df, fixtures)
    keep = ["element", "fixture", "gw", "kickoff", "team", "opp", "home"] + list(STAT_COLUMNS.values())
    df = df[keep].drop_duplicates(["element", "fixture"]).sort_values(["element", "gw", "kickoff"])
    return df.reset_index(drop=True)


def fit_team_ratings(
    pm: pd.DataFrame,
    fixtures: pd.DataFrame,
    teams: list[int],
    *,
    prior: dict[int, tuple[float, float]] | None = None,
    prior_matches: float = 6.0,
    half_life: float = 12.0,
    iters: int = 30,
) -> tuple[dict[int, float], dict[int, float], float, float]:
    """Multiplicative xG ratings: E[xG home] = mu * att_h * def_a * hfa, away = mu * att_a * def_h / hfa.

    Fit by Poisson-gamma fixed point on match xG (sum of player xG per side), recency weighted per
    team match, shrunk to `prior` with the weight of `prior_matches` matches.
    """
    prior = prior or {}
    side = (
        pm.groupby(["fixture", "team"], as_index=False)
        .agg(xg=("xg", "sum"))
        .merge(fixtures[["id", "event", "team_h", "team_a", "kickoff"]], left_on="fixture", right_on="id")
    )
    matches = []
    for fid, grp in side.groupby("fixture"):
        row = grp.iloc[0]
        h, a = int(row.team_h), int(row.team_a)
        xh = float(grp.loc[grp.team == h, "xg"].sum())
        xa = float(grp.loc[grp.team == a, "xg"].sum())
        matches.append((int(row.event), str(row.kickoff), h, a, xh, xa))
    att = {t: float(prior.get(t, (1.0, 1.0))[0]) for t in teams}
    dfn = {t: float(prior.get(t, (1.0, 1.0))[1]) for t in teams}
    if not matches:
        return att, dfn, 1.35, 1.12
    mdf = pd.DataFrame(matches, columns=["event", "kickoff", "h", "a", "xh", "xa"]).sort_values(["event", "kickoff"])
    # Recency: age of each match in that team's own match sequence (0 = latest).
    n_by_team: dict[int, int] = {}
    ages_h, ages_a = [], []
    order = list(mdf.itertuples(index=False))
    for row in reversed(order):
        ages_h.append(n_by_team.get(row.h, 0))
        n_by_team[row.h] = n_by_team.get(row.h, 0) + 1
        ages_a.append(n_by_team.get(row.a, 0))
        n_by_team[row.a] = n_by_team.get(row.a, 0) + 1
    ages_h.reverse()
    ages_a.reverse()
    decay = 0.5 ** (1.0 / max(half_life, 0.1))
    wh = decay ** np.asarray(ages_h, dtype=float)
    wa = decay ** np.asarray(ages_a, dtype=float)
    w = np.sqrt(wh * wa)
    hs = mdf.h.to_numpy()
    as_ = mdf.a.to_numpy()
    xh = mdf.xh.to_numpy()
    xa = mdf.xa.to_numpy()
    mu = float(np.sum(w * (xh + xa)) / (2 * np.sum(w))) if np.sum(w) > 0 else 1.35
    hfa = 1.12
    p0 = float(prior_matches)
    idx = {t: i for i, t in enumerate(teams)}
    hi = np.array([idx[t] for t in hs])
    ai = np.array([idx[t] for t in as_])
    A = np.array([att[t] for t in teams])
    D = np.array([dfn[t] for t in teams])
    A0, D0 = A.copy(), D.copy()
    for _ in range(iters):
        exp_h = mu * D[ai] * hfa
        exp_a = mu * D[hi] / hfa
        num = np.zeros(len(teams))
        den = np.zeros(len(teams))
        np.add.at(num, hi, w * xh)
        np.add.at(den, hi, w * exp_h)
        np.add.at(num, ai, w * xa)
        np.add.at(den, ai, w * exp_a)
        A = (num + p0 * A0 * mu) / (den + p0 * mu)
        exp_h = mu * A[hi] * hfa
        exp_a = mu * A[ai] / hfa
        num = np.zeros(len(teams))
        den = np.zeros(len(teams))
        np.add.at(num, ai, w * xh)
        np.add.at(den, ai, w * exp_h)
        np.add.at(num, hi, w * xa)
        np.add.at(den, hi, w * exp_a)
        D = (num + p0 * D0 * mu) / (den + p0 * mu)
        # Normalise so the league average team is 1.0 on both axes.
        A /= np.mean(A)
        D /= np.mean(D)
        pred_h = mu * A[hi] * D[ai]
        pred_a = mu * A[ai] * D[hi]
        ratio = np.sum(w * xh) / max(np.sum(w * pred_h), 1e-9)
        ratio_a = np.sum(w * xa) / max(np.sum(w * pred_a), 1e-9)
        hfa = float(np.clip(np.sqrt(ratio / max(ratio_a, 1e-9)), 1.0, 1.3))
        mu = float(np.sum(w * (xh + xa)) / max(np.sum(w * (pred_h * hfa + pred_a / hfa)) / mu, 1e-9))
    return {t: float(A[idx[t]]) for t in teams}, {t: float(D[idx[t]]) for t in teams}, mu, hfa


def _prev_team_from_season(pm: pd.DataFrame, fixtures: pd.DataFrame, teams: list[int]) -> dict[int, tuple[float, float]]:
    att, dfn, _, _ = fit_team_ratings(pm, fixtures, teams, prior_matches=4.0, half_life=1e9)
    return {t: (att[t], dfn[t]) for t in teams}


PROMOTED_PRIOR = (0.80, 1.22)
# Last season's ratings regress toward the mean over the summer.
PRIOR_REGRESSION = 0.65


def _carry_team_prior(
    prev_ratings: dict[int, tuple[float, float]] | None,
    prev_names: dict[int, str],
    names: dict[int, str],
) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    by_name = {}
    if prev_ratings:
        for tid, (a, d) in prev_ratings.items():
            by_name[prev_names.get(tid, "")] = (a, d)
    for tid, name in names.items():
        if name in by_name:
            a, d = by_name[name]
            out[tid] = (1.0 + PRIOR_REGRESSION * (a - 1.0), 1.0 + PRIOR_REGRESSION * (d - 1.0))
        else:
            out[tid] = PROMOTED_PRIOR
    return out


# ---------------------------------------------------------------- vaastav (past seasons)


def _vaastav_season(folder: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[int, str]]:
    raw = pd.read_csv(folder / "merged_gw.csv", low_memory=False)
    if "round" not in raw.columns:
        raw["round"] = raw["GW"]
    fixtures = _fixtures_frame(pd.read_csv(folder / "fixtures.csv"))
    teams = pd.read_csv(folder / "teams.csv")
    names = {int(r.id): str(r.short_name) for r in teams.itertuples()}
    raw = raw[raw["position"].isin(POS_CODE)].copy()
    pm = _clean_pm(raw, fixtures)
    pr = pd.read_csv(folder / "players_raw.csv")
    pos_of = raw.groupby("element")["position"].first().map(POS_CODE)
    last_team = pm.sort_values(["gw", "kickoff"]).groupby("element")["team"].last()
    last_price = pm.sort_values(["gw", "kickoff"]).groupby("element")["value"].last()
    players = pd.DataFrame({"element": pr["id"].astype(int), "code": pr["code"].astype(int)})
    players["name"] = pr["web_name"].astype(str).values
    players["pos"] = players.element.map(pos_of).fillna(pr["element_type"]).astype(int)
    players["team"] = players.element.map(last_team).fillna(pr["team"]).astype(int)
    players["price"] = players.element.map(last_price).fillna(pr["now_cost"]).astype(int)
    players = players[players.pos.isin([1, 2, 3, 4])]
    return pm, fixtures, players.reset_index(drop=True), names


def _season_totals(pm: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    tot = pm.groupby("element")[PREV_COLUMNS].sum()
    tot["code"] = tot.index.map(dict(zip(players.element, players.code)))
    tot = tot.dropna(subset=["code"])
    tot["code"] = tot["code"].astype(int)
    return tot.groupby("code")[PREV_COLUMNS].sum()


def from_vaastav(folder: str | Path, prev_folder: str | Path | None = None) -> SeasonData:
    folder = Path(folder)
    pm, fixtures, players, names = _vaastav_season(folder)
    prev = pd.DataFrame(columns=PREV_COLUMNS)
    prev_team: dict[int, tuple[float, float]] = {}
    if prev_folder and Path(prev_folder).exists():
        ppm, pfx, pplayers, pnames = _vaastav_season(Path(prev_folder))
        prev = _season_totals(ppm, pplayers)
        ratings = _prev_team_from_season(ppm, pfx, sorted(pnames))
        prev_team = _carry_team_prior(ratings, pnames, names)
    else:
        prev_team = {t: (1.0, 1.0) for t in names}
    return SeasonData(folder.name, players, pm, fixtures, names, prev, prev_team)


# ---------------------------------------------------------------- live FPL API


def fetch_summaries(client: Any, ids: list[int], workers: int = 6) -> dict[int, dict[str, Any]]:
    def _one(pid: int) -> tuple[int, dict[str, Any] | None]:
        try:
            return pid, client.element_summary(pid)
        except Exception:
            return pid, None

    out: dict[int, dict[str, Any]] = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for pid, data in ex.map(_one, ids):
            if data is not None:
                out[pid] = data
    return out


def from_api(
    bootstrap: dict[str, Any],
    fixtures_raw: list[dict[str, Any]],
    summaries: dict[int, dict[str, Any]],
    *,
    prev_team: dict[int, tuple[float, float]] | None = None,
) -> SeasonData:
    fixtures = _fixtures_frame(fixtures_raw)
    names = {int(t["id"]): str(t["short_name"]) for t in bootstrap["teams"]}
    rows: list[dict[str, Any]] = []
    prev_rows: list[dict[str, Any]] = []
    elements = {int(p["id"]): p for p in bootstrap["elements"]}
    prev_name = _previous_season_name(bootstrap)
    for pid, summary in summaries.items():
        rows.extend(summary.get("history") or [])
        for past in summary.get("history_past") or []:
            if prev_name and past.get("season_name") != prev_name:
                continue
            row = {"code": int(past.get("element_code") or elements.get(pid, {}).get("code") or 0)}
            for src, dst in STAT_COLUMNS.items():
                if dst in PREV_COLUMNS:
                    row[dst] = past.get(src, 0)
            prev_rows.append(row)
    if rows:
        pm = _clean_pm(pd.DataFrame(rows), fixtures)
    else:
        pm = pd.DataFrame(columns=["element", "fixture", "gw", "kickoff", "team", "opp", "home", *STAT_COLUMNS.values()])
    prev = pd.DataFrame(prev_rows)
    if prev.empty:
        prev = pd.DataFrame(columns=PREV_COLUMNS)
    else:
        _num(prev, PREV_COLUMNS)
        prev = prev.groupby("code")[PREV_COLUMNS].sum()
    players = pd.DataFrame(
        {
            "element": [int(p["id"]) for p in bootstrap["elements"]],
            "code": [int(p.get("code") or 0) for p in bootstrap["elements"]],
            "name": [str(p.get("web_name") or p["id"]) for p in bootstrap["elements"]],
            "pos": [int(p["element_type"]) for p in bootstrap["elements"]],
            "team": [int(p["team"]) for p in bootstrap["elements"]],
            "price": [int(p.get("now_cost") or 0) for p in bootstrap["elements"]],
        }
    )
    players = players[players.pos.isin([1, 2, 3, 4])].reset_index(drop=True)
    status = pd.DataFrame(
        {
            "element": [int(p["id"]) for p in bootstrap["elements"]],
            "status": [str(p.get("status") or "a") for p in bootstrap["elements"]],
            "chance": [p.get("chance_of_playing_next_round") for p in bootstrap["elements"]],
            "news": [str(p.get("news") or "") for p in bootstrap["elements"]],
        }
    )
    if prev_team is None:
        prev_team = {t: (1.0, 1.0) for t in names}
    return SeasonData(_season_label(bootstrap), players, pm, fixtures, names, prev, prev_team, status)


def _season_label(bootstrap: dict[str, Any]) -> str:
    events = bootstrap.get("events") or []
    if not events:
        return "current"
    year = int(str(events[0].get("deadline_time", "2026"))[:4])
    return f"{year}-{str(year + 1)[-2:]}"


def _previous_season_name(bootstrap: dict[str, Any]) -> str | None:
    events = bootstrap.get("events") or []
    if not events:
        return None
    year = int(str(events[0].get("deadline_time", "2026"))[:4])
    return f"{year - 1}/{str(year)[-2:]}"
