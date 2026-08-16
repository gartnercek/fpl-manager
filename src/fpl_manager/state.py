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
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = path or STATE_PATH
    if not p.exists():
        return default_state()
    data = json.loads(p.read_text(encoding="utf-8"))
    merged = default_state()
    merged.update(data)
    return merged


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
