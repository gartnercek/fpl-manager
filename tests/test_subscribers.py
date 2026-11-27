from fpl_manager.report import send_email
from fpl_manager.state import default_state, migrate_operator_state, team_row
from fpl_manager.subscribers import load_subscribers, parse_subscribers_json


def test_parse_subscribers_json():
    rows = parse_subscribers_json(
        '[{"team_id": 3, "email": "c@d.co", "plan": "season"}]'
    )
    assert rows[0].team_id == 3
    assert rows[0].source == "env"


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


def test_operator_and_json_subscribers(monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "me@x.co")
    monkeypatch.setenv("SUBSCRIBERS_JSON", '[{"team_id": 5, "email": "friend@x.co"}]')
    monkeypatch.setenv("BILLING_BLOCKLIST", "")
    rows = {r.team_id: r for r in load_subscribers({"team_id": 4846735})}
    assert rows[4846735].plan == "operator"
    assert rows[5].email == "friend@x.co"
