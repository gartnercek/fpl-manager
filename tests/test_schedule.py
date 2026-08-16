from datetime import datetime, timedelta, timezone

from fpl_manager.schedule import decide_run, hours_until_deadline


def _event(hours_from_now: float) -> dict:
    deadline = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return {"id": 3, "deadline_time": deadline.strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_skip_when_deadline_far():
    assert decide_run(_event(80), {}, {}) == "skip"


def test_full_when_first_email_inside_window():
    assert decide_run(_event(40), {"last_emailed_gw": None}, {}) == "full"


def test_delta_when_already_emailed_and_close():
    state = {"last_emailed_gw": 3, "last_fingerprint": "abc"}
    assert decide_run(_event(10), state, {}, fingerprint="changed") == "delta"
    assert decide_run(_event(10), state, {}, fingerprint="abc") == "skip"


def test_hours_until_deadline_positive():
    assert hours_until_deadline(_event(5)) > 4
