from types import SimpleNamespace

from fpl_manager.report import HTML_TEMPLATE, TEXT_TEMPLATE


def _ctx():
    chips = SimpleNamespace(
        triple_captain=SimpleNamespace(reason="HOLD triple_captain"),
        bench_boost=SimpleNamespace(reason="HOLD bench_boost"),
        wildcard_note=None,
        free_hit_note=None,
    )
    plan = SimpleNamespace(in_ids=[], mode="initial", xi_mu=70.0, xi_sd=12.0)
    return {
        "gw": 1,
        "deadline_local": "Fri 21 Aug 19:30 CEST",
        "hours_left": 100,
        "actions": ["Set this 15", "Bench order: 1. Woodman, 2. Maatsen"],
        "plan": plan,
        "transfers": [],
        "xi_rows": ["  MID (C) Fernandes"],
        "bench_rows": ["  1 GKP Woodman", "  2 DEF Maatsen"],
        "xi": [{"pos": "MID", "flag": " (C)", "name": "Fernandes", "mu": 7.0, "sd": 4.0, "price": 12.0}],
        "bench": [
            {"slot": 1, "pos": "GKP", "name": "Woodman", "mu": 3.6, "sd": 1.5, "price": 4.0},
            {"slot": 2, "pos": "DEF", "name": "Maatsen", "mu": 4.9, "sd": 2.0, "price": 4.5},
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


def test_text_email_includes_bench_slots():
    text = TEXT_TEMPLATE.render(**_ctx())
    assert "BENCH (auto-sub order" in text
    assert "Woodman" in text
