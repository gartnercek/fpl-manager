from fpl_manager.report import send_email
from fpl_manager.state import default_state, migrate_operator_state, team_row
from fpl_manager.subscribers import (
    merge_subscribers,
    parse_subscribers_json,
    subscriber_from_session,
    subscriber_from_subscription,
    team_id_from_session,
)


def test_team_id_from_metadata_and_reference():
    assert team_id_from_session({"metadata": {"team_id": "4846735"}}) == 4846735
    assert team_id_from_session({"client_reference_id": "99"}) == 99


def test_team_id_from_custom_field():
    session = {
        "custom_fields": [
            {
                "key": "fplteamid",
                "label": {"custom": "FPL team ID"},
                "text": {"value": "4846735"},
            }
        ]
    }
    assert team_id_from_session(session) == 4846735
    numeric = {
        "custom_fields": [
            {
                "key": "team",
                "numeric": {"value": 12},
            }
        ]
    }
    assert team_id_from_session(numeric) == 12


def test_subscription_needs_email_and_team():
    assert subscriber_from_subscription({"status": "active", "metadata": {"team_id": "1"}}) is None
    row = subscriber_from_subscription(
        {
            "id": "sub_1",
            "status": "active",
            "metadata": {"team_id": "5", "plan": "monthly"},
            "customer": {"email": "a@b.co", "metadata": {}},
        }
    )
    assert row is not None
    assert row.team_id == 5
    assert row.email == "a@b.co"
    assert row.plan == "monthly"


def test_canceled_subscription_ignored():
    assert (
        subscriber_from_subscription(
            {
                "status": "canceled",
                "metadata": {"team_id": "5"},
                "customer": {"email": "a@b.co"},
            }
        )
        is None
    )


def test_season_session_only_when_paid_payment_mode():
    base = {
        "id": "cs_1",
        "status": "complete",
        "payment_status": "paid",
        "mode": "payment",
        "metadata": {"team_id": "7", "plan": "season", "season": "2026/27"},
        "customer_email": "s@x.co",
    }
    row = subscriber_from_session(base, "2026/27")
    assert row is not None and row.plan == "season"
    assert subscriber_from_session({**base, "mode": "subscription"}, "2026/27") is None
    assert subscriber_from_session({**base, "metadata": {**base["metadata"], "season": "2025/26"}}, "2026/27") is None


def test_monthly_checkout_session_does_not_keep_access_after_cancel():
    """Cancelled monthly subs drop off the subscription list; leftover sessions must not revive them."""
    subscriptions = []
    sessions = [
        {
            "id": "cs_old",
            "status": "complete",
            "payment_status": "paid",
            "mode": "subscription",
            "metadata": {"team_id": "7", "plan": "monthly"},
            "customer_email": "s@x.co",
        }
    ]
    assert merge_subscribers(subscriptions, sessions, season="2026/27") == {}


def test_subscription_wins_over_season_session():
    found = merge_subscribers(
        [
            {
                "id": "sub_1",
                "status": "active",
                "metadata": {"team_id": "7", "plan": "monthly"},
                "customer": {"email": "m@x.co"},
            }
        ],
        [
            {
                "id": "cs_1",
                "status": "complete",
                "payment_status": "paid",
                "mode": "payment",
                "metadata": {"team_id": "7", "plan": "season", "season": "2026/27"},
                "customer_email": "s@x.co",
            }
        ],
        season="2026/27",
    )
    assert found[7].plan == "monthly"
    assert found[7].email == "m@x.co"


def test_parse_subscribers_json():
    rows = parse_subscribers_json(
        '[{"team_id": 3, "email": "c@d.co", "plan": "season"}]'
    )
    assert rows[0].team_id == 3
    assert rows[0].source == "env"


def test_parse_beta_payload():
    from fpl_manager.subscribers import parse_beta_payload

    rows = parse_beta_payload(
        {
            "signups": [
                {"team_id": "9", "email": "b@x.co", "plan": "season"},
                {"team_id": 0, "email": "skip@x.co"},
            ]
        }
    )
    assert len(rows) == 1
    assert rows[0].team_id == 9
    assert rows[0].source == "beta"
    assert rows[0].plan == "season"


def test_migrate_operator_once():
    state = default_state()
    state["last_emailed_gw"] = 2
    state["last_fingerprint"] = "fp"
    migrate_operator_state(state, 4846735)
    assert state["by_team"]["4846735"]["last_emailed_gw"] == 2
    state["by_team"]["4846735"]["last_emailed_gw"] = 9
    migrate_operator_state(state, 4846735)
    assert state["by_team"]["4846735"]["last_emailed_gw"] == 9


def test_team_row_starts_empty():
    state = default_state()
    row = team_row(state, 11)
    assert row["last_emailed_gw"] is None
    row["last_emailed_gw"] = 4
    assert team_row(state, 11)["last_emailed_gw"] == 4


def test_send_email_requires_recipient(monkeypatch):
    monkeypatch.delenv("EMAIL_TO", raising=False)
    monkeypatch.setenv("SMTP_USER", "from@x.co")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    try:
        send_email("s", "t", "<p>h</p>")
        raise AssertionError("should have failed")
    except RuntimeError as exc:
        assert "recipient" in str(exc).lower()
