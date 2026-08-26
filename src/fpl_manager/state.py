from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fpl_manager.config import ROOT

STATE_PATH = ROOT / "state" / "state.json"


def default_state() -> dict[str, Any]:
    return {
        "last_emailed_gw": None,
        "last_fingerprint": None,
        "free_transfers": 1,
        "by_team": {},
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = path or STATE_PATH
    if not p.exists():
        return default_state()
    data = json.loads(p.read_text(encoding="utf-8"))
    merged = default_state()
    merged.update(data)
    if not isinstance(merged.get("by_team"), dict):
        merged["by_team"] = {}
    return merged


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def migrate_operator_state(state: dict[str, Any], operator_team_id: int | None) -> None:
    if not operator_team_id:
        return
    by = state.setdefault("by_team", {})
    key = str(int(operator_team_id))
    if key in by:
        return
    if state.get("last_emailed_gw") is None:
        return
    by[key] = {
        "last_emailed_gw": state.get("last_emailed_gw"),
        "last_fingerprint": state.get("last_fingerprint"),
    }


def team_row(state: dict[str, Any], team_id: int) -> dict[str, Any]:
    by = state.setdefault("by_team", {})
    key = str(int(team_id))
    if key not in by:
        by[key] = {"last_emailed_gw": None, "last_fingerprint": None}
    return by[key]
