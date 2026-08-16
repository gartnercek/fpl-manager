from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from fpl_manager.fixtures import Matchup

LEAGUE_AVG_GOALS = 1.35
GOAL_POINTS = {1: 6, 2: 6, 3: 5, 4: 4}
CS_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}
ASSIST_POINTS = 3
FDR_ATTACK = {1: 1.15, 2: 1.08, 3: 1.0, 4: 0.92, 5: 0.85}


@dataclass
class PositionPriors:
    xg90: dict[int, float]
    xa90: dict[int, float]
    yc90: dict[int, float]
    saves90: dict[int, float]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_priors(players: list[dict[str, Any]]) -> PositionPriors:
    buckets: dict[int, list[tuple[float, float, float, float, float]]] = {1: [], 2: [], 3: [], 4: []}
    for p in players:
        pos = int(p["element_type"])
        minutes = _f(p.get("minutes"))
        if minutes < 900:
            continue
        n90 = minutes / 90.0
        buckets[pos].append(
            (
                n90,
                _f(p.get("expected_goals_per_90")),
                _f(p.get("expected_assists_per_90")),
                _f(p.get("yellow_cards")) / n90,
                _f(p.get("saves_per_90")),
            )
        )
    xg90, xa90, yc90, saves90 = {}, {}, {}, {}
    defaults = {
        1: (0.0, 0.01, 0.05, 2.8),
        2: (0.05, 0.06, 0.15, 0.0),
        3: (0.15, 0.12, 0.18, 0.0),
        4: (0.35, 0.12, 0.12, 0.0),
    }
    for pos in (1, 2, 3, 4):
        rows = buckets[pos]
        if not rows:
            xg90[pos], xa90[pos], yc90[pos], saves90[pos] = defaults[pos]
            continue
        weights = np.array([r[0] for r in rows])
        w = weights / weights.sum()
        xg90[pos] = float(np.dot(w, [r[1] for r in rows]))
        xa90[pos] = float(np.dot(w, [r[2] for r in rows]))
        yc90[pos] = float(np.dot(w, [r[3] for r in rows]))
        saves90[pos] = float(np.dot(w, [r[4] for r in rows]))
    return PositionPriors(xg90=xg90, xa90=xa90, yc90=yc90, saves90=saves90)


def shrink_rate(observed: float, n90: float, prior: float, prior_strength: float = 5.0) -> float:
    w = n90 / (n90 + prior_strength)
    return w * observed + (1.0 - w) * prior


def team_rating(team: dict[str, Any], *, home: bool, kind: str) -> float:
    suffix = "home" if home else "away"
    specific = _f(team.get(f"strength_{kind}_{suffix}"))
    if specific > 0:
        return specific
    overall = _f(team.get(f"strength_overall_{suffix}"), 3.0)
    return overall if overall > 0 else 3.0


def mean_overall(teams: dict[int, dict[str, Any]]) -> float:
    vals = [team_rating(t, home=True, kind="overall") for t in teams.values()]
    return float(np.mean(vals)) if vals else 3.0


def attack_scale(
    teams: dict[int, dict[str, Any]],
    team_id: int,
    matchup: Matchup,
    mean_r: float,
) -> float:
    our = teams[team_id]
    opp = teams[matchup.opponent]
    our_atk = team_rating(our, home=matchup.is_home, kind="attack")
    if our_atk <= 0:
        our_atk = team_rating(our, home=matchup.is_home, kind="overall")
    opp_def = team_rating(opp, home=not matchup.is_home, kind="defence")
    if opp_def <= 0:
        opp_def = team_rating(opp, home=not matchup.is_home, kind="overall")
    base = (our_atk / mean_r) / max(opp_def / mean_r, 0.25)
    fdr = FDR_ATTACK.get(int(matchup.difficulty), 1.0)
    home = 1.08 if matchup.is_home else 0.95
    return float(np.clip(base * fdr * home, 0.5, 1.85))


def concede_lambda(
    teams: dict[int, dict[str, Any]],
    team_xgc90: dict[int, float],
    team_id: int,
    matchup: Matchup,
    mean_r: float,
) -> float:
    our = teams[team_id]
    opp = teams[matchup.opponent]
    our_def = team_rating(our, home=matchup.is_home, kind="defence")
    if our_def <= 0:
        our_def = team_rating(our, home=matchup.is_home, kind="overall")
    opp_atk = team_rating(opp, home=not matchup.is_home, kind="attack")
    if opp_atk <= 0:
        opp_atk = team_rating(opp, home=not matchup.is_home, kind="overall")
    scale = (opp_atk / mean_r) / max(our_def / mean_r, 0.25)
    home = 0.92 if matchup.is_home else 1.08
    base = team_xgc90.get(team_id, LEAGUE_AVG_GOALS)
    return float(np.clip(base * scale * home, 0.35, 3.2))


def poisson_pmf_up_to(lam: float, k_max: int) -> np.ndarray:
    pmf = np.zeros(k_max + 1)
    if lam <= 0:
        pmf[0] = 1.0
        return pmf
    p = np.exp(-lam)
    pmf[0] = p
    for k in range(0, k_max):
        p *= lam / (k + 1)
        pmf[k + 1] = p
    total = pmf.sum()
    if total > 0:
        pmf /= total
    return pmf


def expected_floor_div(lam: float, divisor: int, k_max: int = 20) -> tuple[float, float]:
    pmf = poisson_pmf_up_to(lam, k_max)
    ks = np.arange(k_max + 1)
    vals = ks // divisor
    mean = float(np.dot(pmf, vals))
    var = float(np.dot(pmf, vals.astype(float) ** 2) - mean**2)
    return mean, max(var, 0.0)


def team_xgc90(players: list[dict[str, Any]]) -> dict[int, float]:
    acc: dict[int, list[tuple[float, float]]] = {}
    for p in players:
        if int(p["element_type"]) != 1:
            continue
        minutes = _f(p.get("minutes"))
        if minutes < 90:
            continue
        rate = _f(p.get("expected_goals_conceded_per_90"))
        if rate <= 0:
            gc = _f(p.get("goals_conceded"))
            rate = gc / (minutes / 90.0) if minutes else LEAGUE_AVG_GOALS
        acc.setdefault(int(p["team"]), []).append((minutes, rate))
    out: dict[int, float] = {}
    for team_id, rows in acc.items():
        w = sum(m for m, _ in rows)
        out[team_id] = sum(m * r for m, r in rows) / w if w else LEAGUE_AVG_GOALS
    return out
