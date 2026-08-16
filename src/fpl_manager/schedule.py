from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fpl_manager.api import deadline_utc


def hours_until_deadline(event: dict[str, Any], now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (deadline_utc(event) - now).total_seconds() / 3600.0


def decide_run(
    event: dict[str, Any],
    state: dict[str, Any],
    cfg: dict[str, Any],
    *,
    force: bool = False,
    fingerprint: str | None = None,
) -> str:
    """Return skip | full | delta."""
    if force:
        return "full"
    hours = hours_until_deadline(event)
    email_within = float((cfg.get("schedule") or {}).get("email_within_hours", 60))
    delta_within = float((cfg.get("schedule") or {}).get("delta_within_hours", 24))
    gw = int(event["id"])
    if hours > email_within or hours < -6:
        return "skip"
    last = state.get("last_emailed_gw")
    if last != gw:
        return "full"
    if hours <= delta_within:
        if fingerprint and fingerprint == state.get("last_fingerprint"):
            return "skip"
        return "delta"
    return "skip"
