from fpl_manager.markets import (
    extract_team_totals,
    fetch_epl_events,
    implied_under,
    lambda_from_p_blank,
    lookup_blanks,
    map_fpl_teams,
    market_weight,
    normalize_team,
)


def test_normalize_maps_polymarket_and_fpl_names():
    pairs = [
        ("Arsenal FC", "Arsenal"),
        ("Aston Villa FC", "Aston Villa"),
        ("AFC Bournemouth", "Bournemouth"),
        ("Brighton & Hove Albion FC", "Brighton"),
        ("Manchester City FC", "Man City"),
        ("Manchester United FC", "Man Utd"),
        ("Nott'm Forest", "Nottingham Forest FC"),
        ("Tottenham Hotspur FC", "Spurs"),
        ("Wolverhampton Wanderers FC", "Wolves"),
        ("Hull City AFC", "Hull City"),
        ("Sunderland AFC", "Sunderland"),
        ("Coventry City FC", "Coventry City"),
        ("Ipswich Town FC", "Ipswich Town"),
        ("Leeds United FC", "Leeds"),
        ("Crystal Palace FC", "Crystal Palace"),
    ]
    for poly, fpl in pairs:
        assert normalize_team(poly) == normalize_team(fpl), (poly, fpl)


def test_screenshot_cents_are_devigged():
    # Villa O 63c / U 47c as on the Polymarket UI (sums past 1).
    assert abs(implied_under(0.63, 0.47) - 0.47 / 1.10) < 1e-9


def test_midpoint_prices_need_no_devig():
    assert implied_under(0.645, 0.355) == 0.355


def test_extract_palace_city_team_totals():
    event = {
        "slug": "epl-cry-mac-2026-08-28-more-markets",
        "title": "Crystal Palace FC vs. Manchester City FC - More Markets",
        "markets": [
            {
                "sportsMarketType": "soccer_team_totals",
                "slug": "epl-cry-mac-2026-08-28-team-total-home-0pt5",
                "outcomes": '["Over", "Under"]',
                "outcomePrices": '["0.645", "0.355"]',
                "liquidityNum": 3176.5,
            },
            {
                "sportsMarketType": "soccer_team_totals",
                "slug": "epl-cry-mac-2026-08-28-team-total-away-0pt5",
                "outcomes": '["Over", "Under"]',
                "outcomePrices": '["0.855", "0.145"]',
                "liquidityNum": 501.1,
            },
            {
                "sportsMarketType": "soccer_first_half_team_totals",
                "slug": "epl-cry-mac-2026-08-28-first-half-team-total-home-0pt5",
                "outcomes": '["Over", "Under"]',
                "outcomePrices": '["0.35", "0.65"]',
                "liquidityNum": 1740.0,
            },
        ],
    }
    rows = extract_team_totals(event)
    assert len(rows) == 2
    home = next(r for r in rows if r.side == "home")
    away = next(r for r in rows if r.side == "away")
    assert home.p_blank == 0.355
    assert away.p_blank == 0.145
    assert home.date == "2026-08-28"
    # City U0.5 = Palace CS
    assert lambda_from_p_blank(away.p_blank) > lambda_from_p_blank(home.p_blank)


def test_lookup_matches_fpl_ids_on_kickoff_date():
    teams = [
        {"id": 12, "name": "Crystal Palace", "short_name": "CRY"},
        {"id": 13, "name": "Man City", "short_name": "MCI"},
    ]
    names = map_fpl_teams(teams)
    totals = extract_team_totals(
        {
            "slug": "epl-cry-mac-2026-08-28-more-markets",
            "title": "Crystal Palace FC vs. Manchester City FC - More Markets",
            "markets": [
                {
                    "sportsMarketType": "soccer_team_totals",
                    "slug": "epl-cry-mac-2026-08-28-team-total-home-0pt5",
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": [0.6, 0.4],
                    "liquidityNum": 200,
                },
                {
                    "sportsMarketType": "soccer_team_totals",
                    "slug": "epl-cry-mac-2026-08-28-team-total-away-0pt5",
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": [0.8, 0.2],
                    "liquidityNum": 200,
                },
            ],
        }
    )
    home, away = lookup_blanks(totals, 12, 13, "2026-08-28T19:00:00Z", names)
    assert home is not None and away is not None
    assert home.p_blank == 0.4
    assert away.p_blank == 0.2


def test_thin_book_gets_zero_market_weight():
    assert market_weight(12.0, blend=0.7, min_liquidity=80.0, full_liquidity=800.0) == 0.0
    w = market_weight(800.0, blend=0.7, min_liquidity=80.0, full_liquidity=800.0)
    assert abs(w - 0.7) < 1e-9


def test_lookup_falls_back_when_kickoff_date_mismatches():
    teams = [
        {"id": 2, "name": "Aston Villa", "short_name": "AVL"},
        {"id": 1, "name": "Arsenal", "short_name": "ARS"},
    ]
    names = map_fpl_teams(teams)
    totals = extract_team_totals(
        {
            "slug": "epl-ast-ars-2026-08-31-more-markets",
            "title": "ignore",
            "eventDate": "2026-08-31",
            "teams": [
                {"name": "Aston Villa FC", "ordering": "home"},
                {"name": "Arsenal FC", "ordering": "away"},
            ],
            "markets": [
                {
                    "sportsMarketType": "soccer_team_totals",
                    "slug": "epl-ast-ars-2026-08-31-team-total-home-0pt5",
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": [0.58, 0.42],
                    "liquidityNum": 2112,
                },
                {
                    "sportsMarketType": "soccer_team_totals",
                    "slug": "epl-ast-ars-2026-08-31-team-total-away-0pt5",
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": [0.86, 0.14],
                    "liquidityNum": 515,
                },
            ],
        }
    )
    home, away = lookup_blanks(totals, 2, 1, "2026-08-30T19:00:00Z", names)
    assert home is not None and away is not None
    assert abs(home.p_blank - 0.42) < 1e-9
    assert abs(away.p_blank - 0.14) < 1e-9


def test_fetch_filters_epl_series():
    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Sess:
        def __init__(self):
            self.params = None

        def get(self, url, params=None, timeout=None):
            self.params = params
            return _Resp([])

    sess = _Sess()
    fetch_epl_events(sess, series_id="10188", max_pages=1)
    assert sess.params["series_id"] == "10188"
    assert sess.params["active"] == "true"
    assert sess.params["closed"] == "false"


def test_market_team_totals_move_projection_clean_sheets():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from synth import make_season

    from fpl_manager.markets import blend_lambda
    from fpl_manager.projection import project

    s = make_season(played=3)
    fx = s.fixtures[s.fixtures.event == 4].iloc[0]
    key = (4, int(fx.team_h), int(fx.team_a))
    base = project(s, 4, 1)
    # Market: the away side almost never scores (P(blank)=0.8) -> home clean sheet much likelier.
    market = {key: ([None, (lambda_from_p_blank(0.8), 1.0)], blend_lambda)}
    moved = project(s, 4, 1, market=market)
    home_def = base.table[(base.table.team == key[1]) & (base.table.pos == 2)].index
    assert (moved.table.loc[home_def, "p_cs_next"] > base.table.loc[home_def, "p_cs_next"] + 0.1).all()
    assert (moved.table.loc[home_def, "xp_4"] > base.table.loc[home_def, "xp_4"]).all()
