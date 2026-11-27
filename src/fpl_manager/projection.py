"""Expected FPL points per player per gameweek, using only data known before the deadline.

Pieces (all fitted from per-match rows, see `season.py`):

* Team strength: xG attack / defence ratings fitted on this season's matches (recency weighted),
  shrunk to last season's ratings. FPL's own `strength_*` fields are empty in 2026/27.
* Minutes: recency-weighted start / cameo rates from the player's last team matches.
* Per-90 rates (xG, xA, bonus, saves, cards, DEFCON hit rate): this season (recency weighted) +
  last season + a position/price prior, combined as pseudo-counts.
* Points: FPL 2025+ scoring incl. DEFCON. Poisson clean sheets / goals conceded / saves.

No FPL `form`, `ep_next` or points-per-game input: those are lagging point totals and made the
old model chase last week's haul.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fpl_manager.season import SeasonData, fit_team_ratings

GOAL_PTS = {1: 6.0, 2: 6.0, 3: 5.0, 4: 4.0}
CS_PTS = {1: 4.0, 2: 4.0, 3: 1.0, 4: 0.0}
DC_THRESHOLD = {2: 10.0, 3: 12.0, 4: 12.0}
RATE_STATS = ["xg", "xa", "bonus", "saves", "yc", "dc"]


@dataclass
class ProjectionConfig:
    rate_half_life: float = 12.0  # player matches
    prev_weight: float = 0.35  # each last-season 90 counts this much
    prev_cap_90s: float = 30.0
    # Last season fades as this season's matches pile up (as if they were older matches).
    prev_half_life: float = 12.0
    prior_90s: float = 4.0  # position/price prior strength in 90s
    minutes_decay: float = 0.65  # per team match, most recent = 1
    minutes_window: int = 10
    minutes_prior_matches: float = 0.8
    team_half_life: float = 14.0
    team_prior_matches: float = 8.0
    dc_prior_starts: float = 4.0
    future_recovery: float = 0.55  # doubtful/injured availability gap shrinks by this each week
    cameo_minutes: float = 20.0
    attack_exponent: float = 1.0  # fixture multiplier on xG/xA
    bonus_exponent: float = 0.5
    # FPL awards more assists than Opta xA counts (rebounds, deflections, pens won) and
    # defenders finish below xG. Ratios measured on 2025/26 only (2026/27 is the test set).
    assist_mult: dict[int, float] = field(default_factory=lambda: {1: 1.0, 2: 1.25, 3: 1.3, 4: 1.9})
    goal_mult: dict[int, float] = field(default_factory=lambda: {1: 1.0, 2: 0.8, 3: 1.0, 4: 0.97})
    # Final calibration, fitted on 2025/26 by position: actual ~ a * appearance + b * returns.
    # Regular starters collect more small points than the parts credit (a > 1) and raw rate
    # extrapolation over-rates the top end (b < 1), which made the optimiser overpay for
    # premiums. Out of sample on 2026/27 it moves every price band toward the truth.
    cal_app: dict[int, float] = field(default_factory=lambda: {1: 1.28, 2: 1.22, 3: 1.36, 4: 1.30})
    cal_ret: dict[int, float] = field(default_factory=lambda: {1: 0.81, 2: 0.97, 3: 0.73, 4: 0.78})


@dataclass
class Projection:
    gw: int
    weeks: list[int]
    table: pd.DataFrame  # index element; columns name,pos,team,price,p_start,... xp_{gw}, var_{gw}
    fixtures: dict[int, list[tuple[int, int, bool, float, float]]] = field(default_factory=dict)
    # team -> list of (gw, opponent, home, lambda_for, lambda_against)
    team_att: dict[int, float] = field(default_factory=dict)
    team_def: dict[int, float] = field(default_factory=dict)
    news: pd.Series | None = None  # live only: FPL injury/suspension text per element

    def xp(self, pid: int, gw: int) -> float:
        return float(self.table.at[pid, f"xp_{gw}"]) if f"xp_{gw}" in self.table else 0.0

    def xp_matrix(self) -> pd.DataFrame:
        return self.table[[f"xp_{w}" for w in self.weeks]].rename(columns=lambda c: int(c[3:]))

    def var_matrix(self) -> pd.DataFrame:
        return self.table[[f"var_{w}" for w in self.weeks]].rename(columns=lambda c: int(c[4:]))


def _poisson_floor_mean(lam: np.ndarray, div: int, kmax: int = 25) -> np.ndarray:
    lam = np.maximum(np.asarray(lam, dtype=float), 0.0)
    ks = np.arange(kmax + 1)
    logfact = np.cumsum(np.log(np.maximum(ks, 1)))
    logp = ks[None, :] * np.log(np.maximum(lam[:, None], 1e-12)) - lam[:, None] - logfact[None, :]
    pmf = np.exp(logp)
    pmf[lam <= 0, :] = 0.0
    pmf[lam <= 0, 0] = 1.0
    return pmf @ (ks // div)


def _negbin_tail(mean: np.ndarray, thr: float, k: float = 6.0) -> np.ndarray:
    """P(X >= thr) for a negative binomial with mean `mean` and size k (overdispersed counts)."""
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-9)
    p = k / (k + mean)
    n = int(np.ceil(thr))
    xs = np.arange(n)
    from math import lgamma

    lg = np.array([lgamma(x + k) - lgamma(k) - lgamma(x + 1) for x in xs])
    logpmf = lg[None, :] + k * np.log(p[:, None]) + xs[None, :] * np.log(1 - p[:, None])
    return np.clip(1.0 - np.exp(logpmf).sum(axis=1), 0.0, 1.0)


def _price_prior(prev_rates: pd.DataFrame, price: pd.Series, pos: pd.Series) -> pd.DataFrame:
    """Rate prior by position, linear in price, fitted on last season's regulars."""
    out = pd.DataFrame(index=price.index)
    defaults = {
        "xg": {1: 0.0, 2: 0.04, 3: 0.12, 4: 0.30},
        "xa": {1: 0.01, 2: 0.05, 3: 0.12, 4: 0.10},
        "bonus": {1: 0.15, 2: 0.15, 3: 0.15, 4: 0.20},
        "saves": {1: 2.8, 2: 0.0, 3: 0.0, 4: 0.0},
        "yc": {1: 0.04, 2: 0.17, 3: 0.17, 4: 0.12},
        "dc": {1: 0.0, 2: 9.0, 3: 7.0, 4: 3.0},
    }
    for stat in RATE_STATS:
        col = pd.Series(0.0, index=price.index)
        for p in (1, 2, 3, 4):
            mask = pos == p
            fit = prev_rates[(prev_rates.pos == p) & (prev_rates.minutes >= 900)]
            if len(fit) >= 8 and fit[stat].abs().sum() > 0:
                w = fit.minutes.to_numpy()
                x = fit.price.to_numpy() / 10.0
                y = fit[stat].to_numpy()
                xm = np.average(x, weights=w)
                ym = np.average(y, weights=w)
                b = np.sum(w * (x - xm) * (y - ym)) / max(np.sum(w * (x - xm) ** 2), 1e-9)
                if stat in ("yc", "dc", "saves"):
                    b = 0.0 if stat != "dc" else b
                vals = ym + b * (price[mask] / 10.0 - xm)
                lo = 0.0
                col[mask] = np.maximum(vals, lo)
            else:
                col[mask] = defaults[stat][p]
        out[stat] = col
    return out


def availability_from_status(status: pd.DataFrame | None) -> dict[int, float]:
    out: dict[int, float] = {}
    if status is None:
        return out
    for row in status.itertuples():
        st = str(row.status)
        chance = row.chance
        if st in {"i", "s", "u", "n"}:
            a = 0.0 if chance is None or pd.isna(chance) else float(chance) / 100.0
            a = min(a, 0.0 if st in {"s", "u", "n"} else a)
        elif chance is None or pd.isna(chance):
            a = 1.0
        else:
            a = float(chance) / 100.0
        out[int(row.element)] = float(np.clip(a, 0.0, 1.0))
    return out


def availability_proxy(season: SeasonData, gw: int) -> dict[int, float]:
    """Backtest-only news proxy: heavy net selling before the deadline means a flag.

    Calibrated on 2025/26: regular starters losing >20% of owners started ~30% of the time.
    """
    rows = season.pm[season.pm.gw == gw].groupby("element").agg(t_bal=("t_bal", "first"))
    prev = season.pm[season.pm.gw < gw].sort_values(["gw", "kickoff"]).groupby("element")["selected"].last()
    out: dict[int, float] = {}
    for pid, bal in rows.t_bal.items():
        sel = float(prev.get(pid, 0.0) or 0.0)
        if sel < 20000:
            continue
        r = -float(bal) / sel
        if r > 0.06:
            out[int(pid)] = float(np.clip(1.0 - 3.0 * (r - 0.06), 0.1, 1.0))
    return out


def project(
    season: SeasonData,
    gw: int,
    horizon: int = 8,
    cfg: ProjectionConfig | None = None,
    availability: dict[int, float] | None = None,
    last_gw: int = 38,
    market: dict | None = None,
) -> Projection:
    """`market`: optional {(gw, home, away): ([(lam, weight) | None, (lam, weight) | None], blend)}
    from betting team totals; blends the model's goal rates for those fixtures."""
    cfg = cfg or ProjectionConfig()
    weeks = [w for w in range(gw, gw + horizon) if w <= last_gw]
    hist = season.pm[season.pm.gw < gw]
    teams = sorted(season.team_names)
    att, dfn, mu, hfa = fit_team_ratings(
        hist,
        season.fixtures,
        teams,
        prior=season.prev_team,
        prior_matches=cfg.team_prior_matches,
        half_life=cfg.team_half_life,
    )

    players = season.players.set_index("element").copy()
    prices = season.price_at(gw)
    players["price"] = [prices.get(int(e), int(p)) for e, p in zip(players.index, players.price)]
    # Team = latest known club (mid-season moves), else bootstrap team.
    if not hist.empty:
        latest_team = hist.sort_values(["gw", "kickoff"]).groupby("element")["team"].last()
        if season.status is None:  # backtest: also use the club of this GW's row if present
            now_rows = season.pm[season.pm.gw == gw].groupby("element")["team"].first()
            latest_team = latest_team.combine_first(now_rows)
            latest_team.update(now_rows)
            players["team"] = pd.Series(players.index.map(latest_team), index=players.index).fillna(players.team).astype(int)
        else:
            players["team"] = players["team"].astype(int)
    pos = players.pos

    # ---------------- per-90 rates
    h = hist.copy()
    if not h.empty:
        h["age"] = h.groupby("element").cumcount(ascending=False)
        h["w"] = 0.5 ** (h["age"] / cfg.rate_half_life)
        thr = h["element"].map(pos).map(DC_THRESHOLD).fillna(99.0)
        h["dc_hit"] = ((h["dc"] >= thr) & (h["minutes"] >= 60)).astype(float)
        h["long"] = (h["minutes"] >= 60).astype(float)
        agg = pd.DataFrame(
            {
                "n90": (h.w * h.minutes / 90.0).groupby(h.element).sum(),
                **{s: (h.w * h[s]).groupby(h.element).sum() for s in RATE_STATS},
                "dc_hits": (h.w * h.dc_hit).groupby(h.element).sum(),
                "dc_long": (h.w * h.long).groupby(h.element).sum(),
                "season_minutes": h.minutes.groupby(h.element).sum(),
            }
        )
    else:
        agg = pd.DataFrame(columns=["n90", *RATE_STATS, "dc_hits", "dc_long", "season_minutes"])
    agg = agg.reindex(players.index).astype(float).fillna(0.0)

    prev = season.prev.reindex(players.code.values).astype(float).fillna(0.0)
    prev.index = players.index
    prev90 = prev.minutes / 90.0
    prev_rates = pd.DataFrame(index=players.index)
    for s in RATE_STATS:
        prev_rates[s] = np.where(prev90 > 0, prev[s] / np.maximum(prev90, 1e-9), 0.0)
    prev_rates["minutes"] = prev.minutes
    prev_rates["pos"] = pos
    prev_rates["price"] = players.price
    prior = _price_prior(prev_rates, players.price, pos)
    team_games = 0
    if not hist.empty:
        team_games = int(hist.groupby("team").fixture.nunique().median())
    fade = 0.5 ** (team_games / max(cfg.prev_half_life, 1e-6))
    prev_eff = np.minimum(prev90, cfg.prev_cap_90s) * cfg.prev_weight * fade
    rates = pd.DataFrame(index=players.index)
    dc_known = float(season.prev["dc"].sum()) > 0 if "dc" in season.prev else False
    for s in RATE_STATS:
        pe = prev_eff if (s != "dc" or dc_known) else 0.0
        rates[s] = (agg[s] + pe * prev_rates[s] + cfg.prior_90s * prior[s]) / (agg.n90 + pe + cfg.prior_90s)
    rates["xg"] *= pos.map(cfg.goal_mult).astype(float)
    rates["xa"] *= pos.map(cfg.assist_mult).astype(float)

    # DEFCON: P(hit | 60+ min) from hits this season, prior from the per-90 rate.
    dc_prior = np.zeros(len(players))
    for p, thr in DC_THRESHOLD.items():
        m = (pos == p).to_numpy()
        dc_prior[m] = _negbin_tail(rates.dc.to_numpy()[m] * 0.95, thr)
    rates["p_dc"] = (agg.dc_hits + cfg.dc_prior_starts * dc_prior) / (agg.dc_long + cfg.dc_prior_starts)
    rates.loc[pos == 1, "p_dc"] = 0.0

    # ---------------- minutes
    mins = _minutes_model(hist, players, prev, cfg)
    avail = availability or {}
    a0 = pd.Series({pid: avail.get(int(pid), 1.0) for pid in players.index})

    # ---------------- fixtures in the horizon
    fx = season.fixtures[season.fixtures.event.isin(weeks)]
    team_fx: dict[int, list[tuple[int, int, bool, float, float]]] = {t: [] for t in teams}
    for row in fx.itertuples():
        lam_h = mu * att[row.team_h] * dfn[row.team_a] * hfa
        lam_a = mu * att[row.team_a] * dfn[row.team_h] / hfa
        key = (int(row.event), int(row.team_h), int(row.team_a))
        if market and key in market:
            sides, blend = market[key]
            if sides[0]:
                lam_h = blend(lam_h, sides[0][0], sides[0][1])
            if sides[1]:
                lam_a = blend(lam_a, sides[1][0], sides[1][1])
        team_fx[row.team_h].append((int(row.event), int(row.team_a), True, lam_h, lam_a))
        team_fx[row.team_a].append((int(row.event), int(row.team_h), False, lam_a, lam_h))

    xp_cols: dict[str, np.ndarray] = {f"xp_{w}": np.zeros(len(players)) for w in weeks}
    var_cols: dict[str, np.ndarray] = {f"var_{w}": np.zeros(len(players)) for w in weeks}
    n_fx = {w: np.zeros(len(players)) for w in weeks}
    idx_of_team: dict[int, np.ndarray] = {}
    team_arr = players.team.to_numpy()
    for t in teams:
        idx_of_team[t] = np.where(team_arr == t)[0]
    pos_arr = pos.to_numpy()
    gp = np.vectorize(GOAL_PTS.get)(pos_arr).astype(float)
    csp = np.vectorize(CS_PTS.get)(pos_arr).astype(float)
    is_gk = pos_arr == 1
    is_gd = pos_arr <= 2
    ps_all = mins.p_start.to_numpy()
    pc_all = mins.p_cameo.to_numpy()
    ms_all = mins.min_start.to_numpy()
    q60_all = mins.q60.to_numpy()
    a0_arr = a0.reindex(players.index).fillna(1.0).to_numpy()
    r = {s: rates[s].to_numpy() for s in [*RATE_STATS, "p_dc"]}
    next_cs = np.zeros(len(players))
    cal_a = pos.map(cfg.cal_app).astype(float).to_numpy()
    cal_r = pos.map(cfg.cal_ret).astype(float).to_numpy()
    comp = {c: np.zeros(len(players)) for c in ("app", "goals", "assists", "cs", "gc", "saves", "bonus", "dc", "yc")}
    for t, fixtures_t in team_fx.items():
        ii = idx_of_team.get(t)
        if ii is None or len(ii) == 0:
            continue
        for w, opp, home, lam_for, lam_against in fixtures_t:
            k = weeks.index(w)
            avail_k = 1.0 - (1.0 - a0_arr[ii]) * (cfg.future_recovery**k)
            ps = ps_all[ii] * avail_k
            pc = pc_all[ii] * avail_k
            ms = ms_all[ii]
            p60 = ps * q60_all[ii]
            p159 = ps * (1 - q60_all[ii]) + pc
            emin = ps * ms + pc * cfg.cameo_minutes
            f_att = (lam_for / (mu * att[t])) ** cfg.attack_exponent
            goals = r["xg"][ii] * emin / 90.0 * f_att
            assists = r["xa"][ii] * emin / 90.0 * f_att
            lam_c_start = lam_against * ms / 90.0
            p_cs = np.exp(-lam_c_start)
            cs = csp[ii] * p60 * p_cs
            gc = np.where(is_gd[ii], ps * _poisson_floor_mean(lam_c_start, 2), 0.0)
            opp_att = lam_against / (mu * dfn[t])
            saves_lam = r["saves"][ii] * ms / 90.0 * opp_att
            saves = np.where(is_gk[ii], ps * _poisson_floor_mean(saves_lam, 3), 0.0)
            cs_avg = np.exp(-mu * dfn[t] * ms / 90.0)
            bscale = np.where(
                is_gd[ii],
                (np.maximum(p_cs, 1e-6) / np.maximum(cs_avg, 1e-6)) ** cfg.bonus_exponent,
                f_att**cfg.bonus_exponent,
            )
            bonus = r["bonus"][ii] * emin / 90.0 * bscale
            dc = 2.0 * r["p_dc"][ii] * p60
            yc = r["yc"][ii] * emin / 90.0
            app = 2.0 * p60 + 1.0 * p159
            ret = gp[ii] * goals + 3.0 * assists + cs - gc + saves + bonus + dc - yc
            mean = cal_a[ii] * app + cal_r[ii] * ret
            var = cal_r[ii] ** 2 * (
                gp[ii] ** 2 * goals
                + 9.0 * assists
                + csp[ii] ** 2 * p60 * p_cs * (1 - p_cs)
                + 4.0 * p60 * (1 - p60)
                + 2.0 * bonus
                + 4.0 * dc * (1 - dc / 2.0)
                + np.where(is_gd[ii], ps * lam_c_start / 4.0, 0.0)
            )
            xp_cols[f"xp_{w}"][ii] += mean
            if w == weeks[0]:
                for cname, cval in (("app", app), ("goals", goals), ("assists", assists), ("cs", cs / np.maximum(csp[ii], 1e-9) * (csp[ii] > 0)),
                                    ("gc", gc), ("saves", saves), ("bonus", bonus), ("dc", dc / 2.0), ("yc", yc)):
                    comp[cname][ii] += cval
            var_cols[f"var_{w}"][ii] += var
            n_fx[w][ii] += 1
            if w == weeks[0]:
                next_cs[ii] = np.maximum(next_cs[ii], p_cs)

    table = pd.DataFrame(
        {
            "name": players.name,
            "code": players.code,
            "pos": pos,
            "team": players.team,
            "price": players.price,
            "p_start": mins.p_start * a0_arr,
            "p_cameo": mins.p_cameo * a0_arr,
            "p_play": (mins.p_start + mins.p_cameo) * a0_arr,
            "avail": a0_arr,
            "xg90": rates.xg,
            "xa90": rates.xa,
            "p_dc": rates.p_dc,
            "p_cs_next": next_cs,
            "season_minutes": agg.season_minutes,
        },
        index=players.index,
    )
    for col, arr in xp_cols.items():
        table[col] = arr
    for col, arr in var_cols.items():
        table[col] = arr
    for w in weeks:
        table[f"nfx_{w}"] = n_fx[w]
    for cname, arr in comp.items():
        table[f"c_{cname}"] = arr
    news = None
    if season.status is not None:
        news = season.status.set_index("element")["news"].reindex(table.index)
    return Projection(gw, weeks, table, team_fx, att, dfn, news)


@dataclass
class _Minutes:
    p_start: pd.Series
    p_cameo: pd.Series
    min_start: pd.Series
    q60: pd.Series


def _minutes_model(hist: pd.DataFrame, players: pd.DataFrame, prev: pd.DataFrame, cfg: ProjectionConfig) -> _Minutes:
    idx = players.index
    price = players.price.astype(float)
    pos = players.pos
    # Prior start share: last season's starts / 38 if we have them, else price based.
    prev_share = np.clip(prev.starts / 38.0, 0.0, 0.95)
    price_share = np.where(
        pos == 1,
        np.where(price >= 50, 0.75, 0.15),
        np.clip(0.10 + 0.13 * (price / 10.0 - 4.5), 0.05, 0.85),
    )
    p0 = np.where(prev.minutes > 450, 0.5 * prev_share + 0.5 * price_share, price_share)
    p0 = pd.Series(p0, index=idx)
    c0 = pd.Series(np.where(pos == 1, 0.01, 0.15), index=idx)
    if hist.empty:
        return _Minutes(p0, c0, pd.Series(86.0, index=idx), pd.Series(0.9, index=idx))
    h = hist[["element", "gw", "kickoff", "minutes", "starts"]].copy()
    h["age"] = h.groupby("element").cumcount(ascending=False)
    h = h[h.age < cfg.minutes_window]
    h["w"] = cfg.minutes_decay ** h["age"]
    started = (h.starts > 0) | (h.minutes >= 60)
    cameo = (~started) & (h.minutes > 0)
    sw = (h.w * started).groupby(h.element).sum()
    cw = (h.w * cameo).groupby(h.element).sum()
    tw = h.w.groupby(h.element).sum()
    m0 = cfg.minutes_prior_matches
    sw = sw.reindex(idx).fillna(0.0)
    cw = cw.reindex(idx).fillna(0.0)
    tw = tw.reindex(idx).fillna(0.0)
    p_start = (sw + m0 * p0) / (tw + m0)
    p_not = (1 - p_start).clip(lower=1e-6)
    p_cameo_raw = (cw + m0 * c0) / (tw + m0)
    p_cameo = np.minimum(p_cameo_raw, p_not)
    st = h[started]
    msum = (st.w * st.minutes).groupby(st.element).sum().reindex(idx).fillna(0.0)
    l60 = (st.w * (st.minutes >= 60)).groupby(st.element).sum().reindex(idx).fillna(0.0)
    min_start = ((msum + 2.0 * 86.0) / (sw + 2.0)).clip(55.0, 90.0)
    q60 = ((l60 + 2.0 * 0.9) / (sw + 2.0)).clip(0.3, 1.0)
    return _Minutes(p_start.clip(0, 1), pd.Series(p_cameo, index=idx).clip(0, 1), min_start, q60)
