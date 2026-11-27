"""Tiny synthetic season for tests: 6 clubs x 12 players, round-robin fixtures."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from fpl_manager.season import STAT_COLUMNS, SeasonData

TEAMS = [1, 2, 3, 4, 5, 6]
LAYOUT = [1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4]  # positions per club


def fixtures_frame(n_gw: int, finished_through: int, doubles: dict[int, list[tuple[int, int]]] | None = None,
                   blanks: dict[int, list[int]] | None = None) -> pd.DataFrame:
    rounds = []
    pairs = list(itertools.combinations(TEAMS, 2))
    rng = np.random.default_rng(3)
    for gw in range(1, n_gw + 1):
        order = list(TEAMS)
        rng.shuffle(order)
        rounds.append([(gw, order[0], order[1]), (gw, order[2], order[3]), (gw, order[4], order[5])])
    rows = []
    fid = 1
    for rnd in rounds:
        for gw, h, a in rnd:
            if blanks and gw in blanks and (h in blanks[gw] or a in blanks[gw]):
                continue
            rows.append((fid, gw, h, a))
            fid += 1
    for gw, extra in (doubles or {}).items():
        for h, a in extra:
            rows.append((fid, gw, h, a))
            fid += 1
    df = pd.DataFrame(rows, columns=["id", "event", "team_h", "team_a"])
    df["kickoff"] = [f"2026-08-{10 + e:02d}T15:00:00Z" for e in df.event]
    df["finished"] = df.event <= finished_through
    df["h_score"] = np.where(df.finished, 1.0, np.nan)
    df["a_score"] = np.where(df.finished, 1.0, np.nan)
    _ = pairs
    return df.sort_values(["event", "id"]).reset_index(drop=True)


def make_season(
    *,
    n_gw: int = 10,
    played: int = 3,
    stars: dict[int, float] | None = None,
    strong_team: int | None = 1,
    injured: set[int] | None = None,
    doubles: dict[int, list[tuple[int, int]]] | None = None,
    blanks: dict[int, list[int]] | None = None,
) -> SeasonData:
    """`stars`: element -> xG per 90 (attackers). `strong_team` concedes little / scores a lot."""
    stars = stars or {}
    injured = injured or set()
    players = []
    pid = 1
    for t in TEAMS:
        for k in LAYOUT:
            price = {1: 45, 2: 45, 3: 55, 4: 60}[k]
            if pid in stars:
                price = 100
            players.append((pid, pid + 1000, f"P{pid}", k, t, price))
            pid += 1
    pl = pd.DataFrame(players, columns=["element", "code", "name", "pos", "team", "price"])
    fx = fixtures_frame(n_gw, played, doubles, blanks)
    rows = []
    for f in fx[fx.finished].itertuples():
        for side, team, opp in ((True, f.team_h, f.team_a), (False, f.team_a, f.team_h)):
            squad = pl[pl.team == team]
            gk_seen = False
            for p in squad.itertuples():
                starter = (p.pos != 1 or not gk_seen) and p.element not in injured
                if p.pos == 1:
                    gk_seen = True
                mins = 90 if starter else 0
                xg = stars.get(p.element, {1: 0.0, 2: 0.04, 3: 0.1, 4: 0.25}[p.pos]) if mins else 0.0
                if strong_team == team:
                    xg *= 1.5
                if strong_team == opp:
                    xg *= 0.5
                xgc = (0.6 if team == strong_team else 1.6) if mins else 0.0
                row = {c: 0.0 for c in STAT_COLUMNS.values()}
                row.update(
                    element=p.element, fixture=f.id, gw=f.event, kickoff=f.kickoff, team=team, opp=opp, home=side,
                    minutes=mins, starts=1 if mins else 0, xg=xg, xa=xg / 2, xgc=xgc, value=p.price,
                    points=2 if mins else 0, selected=100000, t_bal=0,
                )
                rows.append(row)
    pm = pd.DataFrame(rows)
    prev = pd.DataFrame(columns=["minutes", "starts", "xg", "xa", "bonus", "saves", "yc", "dc", "points", "xgc", "cs", "goals", "assists"])
    names = {t: f"T{t}" for t in TEAMS}
    return SeasonData("test", pl, pm, fx, names, prev, {t: (1.0, 1.0) for t in TEAMS})
