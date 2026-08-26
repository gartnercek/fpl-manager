from __future__ import annotations

from typing import Any

from fpl_manager.api import FPLClient, next_event
from fpl_manager.chips import advise_chips
from fpl_manager.fixtures import index_fixtures, upcoming_events
from fpl_manager.models.aggregate import predict_all, predictions_frame, save_predictions
from fpl_manager.optimize import run_optimize
from fpl_manager.report import fingerprint, render_report
from fpl_manager.squad import load_entry_state


class SquadError(RuntimeError):
    pass


def load_histories(client: FPLClient, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for pid in player_ids:
        try:
            summary = client.element_summary(pid)
        except Exception:
            continue
        out[pid] = list(summary.get("history") or [])
    return out


def build_predictions(cfg: dict[str, Any], *, fetch_summaries: bool = False) -> dict[str, Any]:
    client = FPLClient()
    bootstrap = client.bootstrap()
    fixtures = client.fixtures()
    event = next_event(bootstrap)
    event_id = int(event["id"])
    events = upcoming_events(event_id, int(cfg.get("horizon_gws") or 6))
    by_team = index_fixtures(fixtures)
    histories = None
    if fetch_summaries or cfg.get("fetch_element_summaries"):
        ids = [int(p["id"]) for p in bootstrap["elements"] if not p.get("removed")]
        histories = load_histories(client, ids)
    preds = predict_all(bootstrap, by_team, events, cfg, histories)
    team_names = {int(t["id"]): t["short_name"] for t in bootstrap["teams"]}
    frame = predictions_frame(preds, team_names)
    save_predictions(frame, event_id)
    return {
        "client": client,
        "bootstrap": bootstrap,
        "event": event,
        "preds": preds,
        "frame": frame,
        "team_names": team_names,
        "events": events,
        "by_team": by_team,
    }


def letter_for_team(
    cfg: dict[str, Any],
    shared: dict[str, Any],
    team_id: int,
    *,
    free_transfers: int = 1,
    require_squad: bool = False,
) -> dict[str, Any]:
    bootstrap = shared["bootstrap"]
    event = shared["event"]
    preds = shared["preds"]
    client = shared["client"]
    event_id = int(event["id"])
    current_ids = None
    bank = None
    sells = None
    chips_used: list[str] = []
    chip_records: list = []
    if team_id:
        now_costs = {int(p["id"]): int(p["now_cost"]) for p in bootstrap["elements"]}
        squad = load_entry_state(client, team_id, event_id, now_costs)
        current_ids = squad["current_ids"] or None
        bank = squad["bank"]
        sells = squad["sell_prices"]
        chips_used = [str(c) for c in squad["chips_used"] if c]
        chip_records = list(squad.get("chip_records") or [])
        if require_squad and not current_ids:
            raise SquadError(f"No public squad for team {team_id}")

    plan = run_optimize(
        cfg,
        bootstrap,
        event,
        preds,
        current_ids=current_ids,
        bank=bank,
        sell_prices=sells,
        free_transfers=free_transfers,
        chip_records=chip_records,
    )
    chips = advise_chips(
        preds,
        plan,
        shared["events"],
        shared["by_team"],
        cfg,
        chips_used=chips_used,
    )
    timezone = str((cfg.get("email") or {}).get("timezone") or "Europe/Ljubljana")
    subject, text, html = render_report(
        event=event,
        plan=plan,
        preds=preds,
        chips=chips,
        timezone=timezone,
        team_names=shared["team_names"],
    )
    return {
        "plan": plan,
        "chips": chips,
        "subject": subject,
        "text": text,
        "html": html,
        "fp": fingerprint(plan, chips),
    }


def run_pipeline(cfg: dict[str, Any], *, fetch_summaries: bool = False) -> dict[str, Any]:
    shared = build_predictions(cfg, fetch_summaries=fetch_summaries)
    letter = letter_for_team(cfg, shared, int(cfg.get("team_id") or 0))
    return {**shared, **letter}
