"""Who gets a letter: your own team (EMAIL_TO + team_id) plus any teams in SUBSCRIBERS_JSON."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Subscriber:
    team_id: int
    email: str
    plan: str
    source: str


def _positive_int(value: Any) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def parse_subscribers_json(raw: str) -> list[Subscriber]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("SUBSCRIBERS_JSON must be a JSON list")
    out: list[Subscriber] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        team_id = _positive_int(item.get("team_id"))
        email = str(item.get("email") or "").strip()
        plan = str(item.get("plan") or "friend")
        if team_id and "@" in email:
            out.append(Subscriber(team_id=team_id, email=email, plan=plan, source="env"))
    return out


def blocked_team_ids() -> set[int]:
    raw = os.environ.get("BILLING_BLOCKLIST") or ""
    out: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        n = _positive_int(part)
        if n:
            out.add(n)
    return out


def load_subscribers(cfg: dict[str, Any]) -> list[Subscriber]:
    found: dict[int, Subscriber] = {}
    raw = (os.environ.get("SUBSCRIBERS_JSON") or "").strip()
    if raw:
        for row in parse_subscribers_json(raw):
            found[row.team_id] = row
    operator_email = (os.environ.get("EMAIL_TO") or "").strip()
    operator_team = int(cfg.get("team_id") or 0)
    if operator_email and operator_team and operator_team not in found:
        found[operator_team] = Subscriber(
            team_id=operator_team,
            email=operator_email,
            plan="operator",
            source="env",
        )
    blocked = blocked_team_ids()
    return [row for row in found.values() if row.team_id not in blocked]
