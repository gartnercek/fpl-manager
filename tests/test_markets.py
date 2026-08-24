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


def test_build_fixture_lambdas_uses_liquid_team_totals(monkeypatch):
    from fpl_manager.fixtures import Matchup
    from fpl_manager.markets import TeamTotal, lambda_from_p_blank as lam_blank
    from fpl_manager.models.aggregate import build_fixture_lambdas

    monkeypatch.setattr(
        "fpl_manager.models.aggregate.fetch_team_totals",
        lambda: [
            TeamTotal("2026-08-31", "Aston Villa FC", "Arsenal FC", "home", 0.42, 2000.0),
            TeamTotal("2026-08-31", "Aston Villa FC", "Arsenal FC", "away", 0.14, 2000.0),
        ],
    )
    stub = {
        "strength_overall_home": 3,
        "strength_overall_away": 3,
        "strength_attack_home": 0,
        "strength_attack_away": 0,
        "strength_defence_home": 0,
        "strength_defence_away": 0,
    }
    teams = {1: {"id": 1, "short_name": "ARS", **stub}, 2: {"id": 2, "short_name": "AVL", **stub}}
    kickoff = "2026-08-31T19:00:00Z"
    by_team = {
        2: {3: [Matchup(event=3, opponent=1, is_home=True, difficulty=3, kickoff=kickoff)]},
        1: {3: [Matchup(event=3, opponent=2, is_home=False, difficulty=3, kickoff=kickoff)]},
    }
    cfg = {"markets": {"enabled": True, "blend": 1.0, "min_liquidity": 80, "full_liquidity": 800}}
    out = build_fixture_lambdas(
        teams,
        {1: 1.0, 2: 1.2},
        3.5,
        by_team,
        [3],
        [{"id": 1, "name": "Arsenal", "short_name": "ARS"}, {"id": 2, "name": "Aston Villa", "short_name": "AVL"}],
        cfg,
    )
    lam_h, lam_a = out[(3, 2, 1)]
    assert abs(lam_h - lam_blank(0.42)) < 1e-9
    assert abs(lam_a - lam_blank(0.14)) < 1e-9
