from __future__ import annotations

from typing import Any

import requests

from fpl_manager.api import FPLClient
from fpl_manager.planner import CHIPS, half_of
from fpl_manager.simulator import sell_price

CHIP_ALIASES = {"wildcard": "wildcard", "freehit": "freehit", "bboost": "bboost", "3xc": "3xc"}


def free_transfers_from_history(
    history: list[dict[str, Any]],
    chips: list[dict[str, Any]],
    next_gw: int,
    topups: dict[int, int] | None = None,
) -> int:
    """Free transfers available for `next_gw` under 2024+ rules.

    GW1 changes are free. Each later GW adds 1 (max 5). A Wildcard/Free Hit week keeps the
    bank but adds none. Paying a hit resets to 1 for the following GW.
    """
    chip_at = {int(c["event"]): str(c.get("name") or "") for c in chips or [] if c.get("event")}
    rows = {int(r["event"]): r for r in history or []}
    if not rows:
        return 1
    first = min(rows)
    ft = 1
    for gw in range(first + 1, next_gw + 1):
        prev = rows.get(gw - 1)
        if topups and gw in topups:
            ft = max(ft, int(topups[gw]))
        if prev is None or gw - 1 == first:
            ft = 1 if gw - 1 == first else ft
            continue
        chip = chip_at.get(gw - 1, "")
        made = int(prev.get("event_transfers") or 0)
        if chip in ("wildcard", "freehit"):
            ft = min(5, ft)
            continue
        if made > ft:
            ft = 1
        else:
            ft = min(5, ft - made + 1)
    return max(1, min(5, ft))


def chips_left_from_history(chips: list[dict[str, Any]]) -> dict[tuple[str, int], bool]:
    left = {(c, h): True for c in CHIPS for h in (1, 2)}
    for rec in chips or []:
        name = CHIP_ALIASES.get(str(rec.get("name") or "").lower())
        event = rec.get("event")
        if name and event:
            left[(name, half_of(int(event)))] = False
    return left


def load_entry_state(
    client: FPLClient,
    team_id: int,
    event_id: int,
    now_costs: dict[int, int],
    topups: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Public squad snapshot. Missing endpoints are treated as 'no squad yet'."""
    entry: dict[str, Any] = {}
    try:
        entry = client.entry(team_id)
    except requests.HTTPError:
        entry = {}

    picks: list[dict[str, Any]] = []
    picks_gw = None
    for gw in (event_id, event_id - 1, 1):
        if gw < 1:
            continue
        try:
            payload = client.entry_picks(team_id, gw)
            picks = list(payload.get("picks") or [])
            if picks:
                picks_gw = gw
                break
        except requests.HTTPError:
            continue

    transfers: list[dict[str, Any]] = []
    try:
        transfers = list(client.entry_transfers(team_id) or [])
    except requests.HTTPError:
        transfers = []

    purchase: dict[int, int] = {}
    pending = 0
    for row in sorted(transfers, key=lambda r: r.get("time") or ""):
        if picks_gw is not None and int(row.get("event") or 0) > picks_gw:
            pending += 1  # already made for the upcoming GW; picks do not show it yet
            continue
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

    history: dict[str, Any] = {}
    try:
        history = client.entry_history(team_id)
    except requests.HTTPError:
        history = {}
    chip_records = list(history.get("chips") or [])
    chips_used = [c.get("name") or c.get("chip") for c in chip_records]
    ft = free_transfers_from_history(list(history.get("current") or []), chip_records, event_id, topups) if history else None

    return {
        "entry": entry,
        "current_ids": set(current_ids),
        "sell_prices": sells,
        "bank": int(bank or 0),
        "chips_used": chips_used,
        "chip_records": chip_records,
        "chips_left": chips_left_from_history(chip_records),
        "free_transfers": ft,
        "pending_transfers": pending,
        "picks": picks,
    }
