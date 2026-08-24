from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

GAMMA = "https://gamma-api.polymarket.com"
# GET /sports -> sport=epl. Public Gamma; no trading key.
EPL_SERIES_ID = "10188"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_STRIP = re.compile(r"\b(fc|afc|cfc|football club)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_ALIASES = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "man united": "man utd",
    "tottenham hotspur": "tottenham",
    "spurs": "tottenham",
    "wolverhampton wanderers": "wolves",
    "nottingham forest": "forest",
    "nottm forest": "forest",
    "brighton and hove albion": "brighton",
    "west ham united": "west ham",
    "newcastle united": "newcastle",
    "leeds united": "leeds",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    "aston villa": "villa",
    "crystal palace": "palace",
    "sheffield united": "sheffield",
    "sunderland afc": "sunderland",
    "coventry city": "coventry",
    "hull city": "hull",
    "brighton hove albion": "brighton",
}


def normalize_team(name: str) -> str:
    s = (name or "").lower().replace("&", " and ").replace("nott'm", "nottingham")
    s = _NON_ALNUM.sub(" ", s)
    s = _STRIP.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _ALIASES.get(s, s)


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def implied_under(over: float, under: float) -> float | None:
    """De-vig P(under). Screenshot cents can sum past 1; Gamma midpoints often already do."""
    if over < 0 or under < 0:
        return None
    total = over + under
    if total <= 0:
        return None
    return float(under / total)


def lambda_from_p_blank(p_blank: float) -> float:
    p = min(max(float(p_blank), 1e-6), 1.0 - 1e-6)
    return float(min(max(-math.log(p), 0.35), 3.2))


@dataclass(frozen=True)
class TeamTotal:
    """P(this side scores 0) from O/U 0.5, i.e. opponent clean sheet."""

    date: str
    home_name: str
    away_name: str
    side: str  # home | away
    p_blank: float
    liquidity: float


def _session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(total=3, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return sess


def _parse_more_markets_slug(slug: str) -> tuple[str, str, str] | None:
    # epl-cry-mac-2026-08-28-more-markets
    m = re.match(r"^epl-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})-more-markets$", slug or "")
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def resolve_epl_series_id(session: requests.Session) -> str:
    try:
        response = session.get(f"{GAMMA}/sports", timeout=25)
        response.raise_for_status()
        rows = response.json()
        if isinstance(rows, list):
            for row in rows:
                if str(row.get("sport") or "").lower() == "epl" and row.get("series"):
                    return str(row["series"])
    except (requests.RequestException, ValueError, TypeError):
        pass
    return EPL_SERIES_ID


def _event_side_names(event: dict[str, Any], home_abbr: str, away_abbr: str) -> tuple[str, str]:
    home_name = away_name = None
    for team in event.get("teams") or []:
        order = str(team.get("ordering") or "").lower()
        name = str(team.get("name") or team.get("alias") or "").strip()
        if not name:
            continue
        if order == "home":
            home_name = name
        elif order == "away":
            away_name = name
    if home_name and away_name:
        return home_name, away_name
    title = str(event.get("title") or "")
    names = re.split(r"\s+vs\.?\s+", title.replace(" - More Markets", ""), maxsplit=1)
    if len(names) == 2:
        return names[0].strip(), names[1].strip()
    return home_abbr, away_abbr


def _event_date(event: dict[str, Any], slug_day: str) -> str:
    raw = event.get("eventDate") or event.get("startTime")
    parsed = _kickoff_date(str(raw) if raw else None)
    return parsed or slug_day


def _market_kind(market: dict[str, Any]) -> str:
    kind = str(market.get("sportsMarketType") or "")
    if kind:
        return kind
    sports = market.get("sports")
    if isinstance(sports, dict):
        return str(sports.get("sportsMarketType") or sports.get("sports_market_type") or "")
    return ""


def _ou_prices(market: dict[str, Any]) -> tuple[float, float] | None:
    outcomes = [str(x).lower() for x in (parse_jsonish(market.get("outcomes")) or [])]
    prices = parse_jsonish(market.get("outcomePrices")) or []
    if len(outcomes) != 2 or len(prices) != 2:
        return None
    try:
        over = under = None
        for name, raw in zip(outcomes, prices):
            px = float(raw)
            if name.startswith("over"):
                over = px
            elif name.startswith("under"):
                under = px
        if over is None or under is None:
            return None
        return over, under
    except (TypeError, ValueError):
        return None


def extract_team_totals(event: dict[str, Any]) -> list[TeamTotal]:
    parsed = _parse_more_markets_slug(str(event.get("slug") or ""))
    if parsed is None:
        return []
    home_abbr, away_abbr, slug_day = parsed
    home_name, away_name = _event_side_names(event, home_abbr, away_abbr)
    day = _event_date(event, slug_day)
    out: list[TeamTotal] = []
    for market in event.get("markets") or []:
        kind = _market_kind(market)
        slug = str(market.get("slug") or "")
        if kind != "soccer_team_totals":
            continue
        if "0pt5" not in slug:
            continue
        if "first-half" in slug or "second-half" in slug:
            continue
        prices = _ou_prices(market)
        if prices is None:
            continue
        p_blank = implied_under(*prices)
        if p_blank is None:
            continue
        if "team-total-home" in slug:
            side = "home"
        elif "team-total-away" in slug:
            side = "away"
        else:
            continue
        liq = market.get("liquidityNum")
        try:
            liquidity = float(liq) if liq is not None else 0.0
        except (TypeError, ValueError):
            liquidity = 0.0
        out.append(
            TeamTotal(
                date=day,
                home_name=home_name,
                away_name=away_name,
                side=side,
                p_blank=p_blank,
                liquidity=liquidity,
            )
        )
    return out


def fetch_epl_events(
    session: requests.Session | None = None,
    *,
    limit: int = 100,
    max_pages: int = 12,
    series_id: str | None = None,
) -> list[dict[str, Any]]:
    sess = session or _session()
    sid = series_id or resolve_epl_series_id(sess)
    found: list[dict[str, Any]] = []
    offset = 0
    for _ in range(max_pages):
        response = sess.get(
            f"{GAMMA}/events",
            params={
                "series_id": sid,
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "startTime",
                "ascending": "true",
            },
            timeout=25,
        )
        response.raise_for_status()
        page = response.json()
        rows = page if isinstance(page, list) else []
        if not rows:
            break
        for event in rows:
            slug = str(event.get("slug") or "")
            if slug.startswith("epl-") and slug.endswith("-more-markets"):
                found.append(event)
        offset += len(rows)
        if len(rows) < limit:
            break
    return found


def fetch_team_totals(session: requests.Session | None = None) -> list[TeamTotal]:
    totals: list[TeamTotal] = []
    for event in fetch_epl_events(session):
        totals.extend(extract_team_totals(event))
    return totals


def map_fpl_teams(bootstrap_teams: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for team in bootstrap_teams:
        tid = int(team["id"])
        for key in ("name", "short_name", "pulse_id"):
            raw = team.get(key)
            if raw is None or key == "pulse_id":
                continue
            out[normalize_team(str(raw))] = tid
    return out


def _kickoff_date(kickoff: str | None) -> str | None:
    if not kickoff:
        return None
    raw = kickoff.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return kickoff[:10] if len(kickoff) >= 10 else None


@dataclass
class FixtureLambdas:
    lam_home: float
    lam_away: float
    p_blank_home: float | None = None
    p_blank_away: float | None = None
    source: str = "fpl"


def blend_lambda(fpl_lam: float, market_lam: float | None, weight: float) -> float:
    if market_lam is None or weight <= 0:
        return fpl_lam
    w = min(max(float(weight), 0.0), 1.0)
    return (1.0 - w) * fpl_lam + w * market_lam


def market_weight(liquidity: float, *, blend: float, min_liquidity: float, full_liquidity: float) -> float:
    if liquidity < min_liquidity:
        return 0.0
    span = max(full_liquidity - min_liquidity, 1.0)
    frac = min(max((liquidity - min_liquidity) / span, 0.0), 1.0)
    return float(blend) * frac


def lookup_blanks(
    totals: list[TeamTotal],
    home_id: int,
    away_id: int,
    kickoff: str | None,
    name_to_id: dict[str, int],
) -> tuple[TeamTotal | None, TeamTotal | None]:
    day = _kickoff_date(kickoff)
    home_hit: TeamTotal | None = None
    away_hit: TeamTotal | None = None
    home_fb: TeamTotal | None = None
    away_fb: TeamTotal | None = None
    for row in totals:
        hid = name_to_id.get(normalize_team(row.home_name))
        aid = name_to_id.get(normalize_team(row.away_name))
        if hid != home_id or aid != away_id:
            continue
        dated = bool(day) and row.date == day
        if row.side == "home":
            if dated:
                home_hit = row
            elif home_fb is None:
                home_fb = row
        elif row.side == "away":
            if dated:
                away_hit = row
            elif away_fb is None:
                away_fb = row
    return home_hit or home_fb, away_hit or away_fb
