from __future__ import annotations

from typing import Any

import requests

from fpl_manager.api import FPLClient
from fpl_manager.optimize import sell_price


def load_entry_state(
    client: FPLClient,
    team_id: int,
    event_id: int,
    now_costs: dict[int, int],
) -> dict[str, Any]:
    """Public squad snapshot. Missing endpoints are treated as 'no squad yet'."""
    entry: dict[str, Any] = {}
    try:
        entry = client.entry(team_id)
    except requests.HTTPError:
        entry = {}

    picks: list[dict[str, Any]] = []
    for gw in (event_id, event_id - 1, 1):
        if gw < 1:
            continue
        try:
            payload = client.entry_picks(team_id, gw)
            picks = list(payload.get("picks") or [])
            if picks:
                break
        except requests.HTTPError:
            continue

    transfers: list[dict[str, Any]] = []
    try:
        transfers = list(client.entry_transfers(team_id) or [])
    except requests.HTTPError:
        transfers = []

    purchase: dict[int, int] = {}
    for row in sorted(transfers, key=lambda r: r.get("time") or ""):
        element_in = int(row["element_in"])
        purchase[element_in] = int(row.get("element_in_cost") or now_costs.get(element_in, 0))
        purchase.pop(int(row["element_out"]), None)

    current_ids = [int(p["element"]) for p in picks]
    for pid in current_ids:
        purchase.setdefault(pid, now_costs.get(pid, 0))

    sells = {pid: sell_price(purchase[pid], now_costs.get(pid, purchase[pid])) for pid in current_ids}
    bank = entry.get("last_deadline_bank")
    if bank is None and current_ids:
        bank = max(1000 - sum(purchase.get(pid, now_costs.get(pid, 0)) for pid in current_ids), 0)
    elif bank is None:
        bank = 0

    history = {}
    try:
        history = client.entry_history(team_id)
    except requests.HTTPError:
        history = {}
    chips_used = [c.get("name") or c.get("chip") for c in history.get("chips") or []]

    return {
        "entry": entry,
        "current_ids": set(current_ids),
        "sell_prices": sells,
        "bank": int(bank or 0),
        "chips_used": chips_used,
        "picks": picks,
    }
