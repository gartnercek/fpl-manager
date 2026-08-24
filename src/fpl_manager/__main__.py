from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from fpl_manager.api import FPLClient, next_event
from fpl_manager.chips import advise_chips
from fpl_manager.config import load_config
from fpl_manager.fixtures import index_fixtures, upcoming_events
from fpl_manager.models.aggregate import predict_all, predictions_frame, save_predictions
from fpl_manager.optimize import run_optimize
from fpl_manager.report import fingerprint, render_report, send_email
from fpl_manager.schedule import decide_run
from fpl_manager.squad import load_entry_state
from fpl_manager.state import load_state, save_state


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def load_histories(client: FPLClient, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for pid in player_ids:
        try:
            summary = client.element_summary(pid)
        except Exception:
            continue
        out[pid] = list(summary.get("history") or [])
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpl-manager")
    parser.add_argument("--fetch-summaries", action="store_true")
    parser.add_argument("--no-email", action="store_true", help="Print the weekly report to stdout.")
    parser.add_argument("--email", action="store_true", help="Send email if the deadline window says so.")
    parser.add_argument("--force", action="store_true", help="Send/print even outside the 60h window.")
    parser.add_argument("--optimize", action="store_true", help="Deprecated: always runs. Kept for compatibility.")
    return parser


def run_pipeline(cfg: dict[str, Any], *, fetch_summaries: bool = False):
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

    team_id = int(cfg.get("team_id") or 0)
    current_ids = None
    bank = None
    sells = None
    chips_used: list[str] = []
    chip_records: list = []
    free_transfers = int(load_state().get("free_transfers") or 1)
    if team_id:
        now_costs = {int(p["id"]): int(p["now_cost"]) for p in bootstrap["elements"]}
        squad = load_entry_state(client, team_id, event_id, now_costs)
        current_ids = squad["current_ids"] or None
        bank = squad["bank"]
        sells = squad["sell_prices"]
        chips_used = [str(c) for c in squad["chips_used"] if c]
        chip_records = list(squad.get("chip_records") or [])

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
        events,
        by_team,
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
        team_names=team_names,
    )
    return {
        "client": client,
        "bootstrap": bootstrap,
        "event": event,
        "preds": preds,
        "frame": frame,
        "plan": plan,
        "chips": chips,
        "subject": subject,
        "text": text,
        "html": html,
        "fp": fingerprint(plan, chips),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    send = bool(args.email) and not args.no_email
    try:
        result = run_pipeline(cfg, fetch_summaries=args.fetch_summaries)
    except Exception:
        if send:
            try:
                send_email(
                    "FPL manager FAILED",
                    traceback.format_exc(),
                    f"<pre>{traceback.format_exc()}</pre>",
                )
            except Exception:
                pass
        raise

    state = load_state()
    mode = decide_run(result["event"], state, cfg, force=args.force, fingerprint=result["fp"])
    if send and mode == "skip":
        print(f"skip: outside email window or unchanged (GW{result['event']['id']})")
        return 0

    print(_ascii(result["text"]))

    if send:
        send_email(result["subject"], result["text"], result["html"])
        state["last_emailed_gw"] = int(result["event"]["id"])
        state["last_fingerprint"] = result["fp"]
        save_state(state)
        print(f"emailed ({mode}) GW{result['event']['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
