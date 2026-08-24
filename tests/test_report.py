from types import SimpleNamespace

from fpl_manager.fixtures import Matchup
from fpl_manager.models.aggregate import PlayerPrediction
from fpl_manager.models.minutes import MinutesEstimate
from fpl_manager.optimize import Plan
from fpl_manager.report import HTML_TEMPLATE, TEXT_TEMPLATE, pair_transfers, render_report


def _ctx():
    chips = SimpleNamespace(
        triple_captain=SimpleNamespace(reason="HOLD triple_captain"),
        bench_boost=SimpleNamespace(reason="HOLD bench_boost"),
        wildcard_note=None,
        free_hit_note=None,
    )
    plan = SimpleNamespace(in_ids=[], mode="initial", xi_mu=70.0, xi_sd=12.0, hit_cost=0)
    return {
        "gw": 1,
        "deadline_local": "Fri 21 Aug 19:30 CEST",
        "hours_left": 100,
        "actions": ["Set this 15", "Bench order: 1. Woodman, 2. Maatsen"],
        "plan": plan,
        "transfers": [
            {
                "out_name": "O'Reilly",
                "in_name": "Murillo",
                "pos": "DEF",
                "out_price": 5.0,
                "in_price": 5.5,
            }
        ],
        "xi_rows": ["  MID (C) Fernandes"],
        "bench_rows": ["  1 GKP Woodman", "  2 DEF Maatsen"],
        "xi": [
            {
                "pos": "MID",
                "flag": " (C)",
                "name": "Fernandes",
                "fixture": "H vs IPS (FDR 2)",
                "mu": 7.0,
                "mu5": 32.0,
                "sd": 4.0,
                "price": 12.0,
            }
        ],
        "bench": [
            {
                "slot": 1,
                "pos": "GKP",
                "name": "Woodman",
                "fixture": "H vs NFO (FDR 3)",
                "mu": 3.6,
                "mu5": 16.0,
                "sd": 1.5,
                "price": 4.0,
            },
            {
                "slot": 2,
                "pos": "DEF",
                "name": "Maatsen",
                "fixture": "H vs IPS (FDR 2)",
                "mu": 4.9,
                "mu5": 20.0,
                "sd": 2.0,
                "price": 4.5,
            },
        ],
        "chips": chips,
        "risk_notes": ["none"],
        "appendix": ["MID: Fernandes 7.0"],
    }


def test_html_email_includes_bench_order():
    html = HTML_TEMPLATE.render(**_ctx())
    assert "Bench (auto-sub order)" in html
    assert "Woodman" in html
    assert "Maatsen" in html


def test_html_email_includes_named_transfers():
    html = HTML_TEMPLATE.render(**_ctx())
    assert "Transfers" in html
    assert "O'Reilly" in html
    assert "Murillo" in html
    assert "H vs IPS (FDR 2)" in html


def test_text_email_includes_bench_slots():
    text = TEXT_TEMPLATE.render(**_ctx())
    assert "BENCH (auto-sub order" in text
    assert "Woodman" in text
    assert "O'Reilly" in text
    assert "Murillo" in text


def test_pair_transfers_matches_same_position():
    by_id = {
        1: SimpleNamespace(web_name="O'Reilly", position=2, now_cost=50),
        2: SimpleNamespace(web_name="Lavia", position=3, now_cost=45),
        3: SimpleNamespace(web_name="Murillo", position=2, now_cost=55),
        4: SimpleNamespace(web_name="Ndiaye", position=3, now_cost=60),
    }
    pairs = pair_transfers([1, 2], [4, 3], by_id)
    by_out = {p["out_name"]: p["in_name"] for p in pairs}
    assert by_out["O'Reilly"] == "Murillo"
    assert by_out["Lavia"] == "Ndiaye"


def _pred(pid, name, pos, mu=4.0):
    return PlayerPrediction(
        player_id=pid,
        web_name=name,
        team_id=1,
        position=pos,
        now_cost=50,
        status="a",
        news="",
        ep_next=mu,
        minutes=MinutesEstimate(0.9, 0.05, 80, 0.9, 0.05, 0.95),
        xg90=0.1,
        xa90=0.1,
        mu=mu,
        sigma2=4.0,
        mu_horizon={5: mu * 5},
        fixtures=[Matchup(event=2, opponent=2, is_home=True, difficulty=2, kickoff=None)],
    )


def test_render_report_action_uses_names_not_ids():
    preds = [
        _pred(1, "Raya", 1),
        _pred(2, "Guehi", 2, 6.0),
        _pred(3, "Gabriel", 2),
        _pred(4, "Murillo", 2),
        _pred(5, "Shaw", 2),
        _pred(6, "Szoboszlai", 3, 5.5),
        _pred(7, "Fernandes", 3),
        _pred(8, "Mbeumo", 3),
        _pred(9, "Ndiaye", 3),
        _pred(10, "Pedro", 4),
        _pred(11, "DCL", 4),
        _pred(12, "Woodman", 1, 1.0),
        _pred(13, "Cherki", 3, 3.0),
        _pred(14, "Calafiori", 2, 3.2),
        _pred(15, "Mateta", 4, 2.0),
        _pred(16, "O'Reilly", 2, 3.0),
        _pred(17, "Lavia", 3, 1.0),
    ]
    plan = Plan(
        mode="transfers",
        transfers=2,
        hit_cost=4,
        owned=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
        starters=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        bench=[12, 13, 14, 15],
        captain=2,
        vice=6,
        xi_mu=50.0,
        xi_sd=10.0,
        objective=50.0,
        horizon3=140.0,
        cost=1000,
        bank=0,
        in_ids=[4, 9],
        out_ids=[16, 17],
    )
    chips = SimpleNamespace(
        triple_captain=SimpleNamespace(reason="HOLD triple_captain: later", play=False),
        bench_boost=SimpleNamespace(reason="HOLD bench_boost: later", play=False),
        wildcard_note=None,
        free_hit_note=None,
    )
    event = {"id": 2, "deadline_time": "2026-08-28T18:30:00Z"}
    _subject, text, html = render_report(
        event=event,
        plan=plan,
        preds=preds,
        chips=chips,
        timezone="Europe/Ljubljana",
        team_names={2: "IPS"},
    )
    assert "[16, 17]" not in text
    assert "O'Reilly -> Murillo" in text
    assert "Lavia" in html and "Ndiaye" in html
    assert "Make these substitutions" in html
    assert "H vs IPS (FDR 2)" in html
    assert "Captain Guehi" in text
    assert "Polymarket" in html
    assert "Polymarket" in text
