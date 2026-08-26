from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Any

from fpl_manager.config import load_config
from fpl_manager.pipeline import SquadError, build_predictions, letter_for_team, run_pipeline
from fpl_manager.report import send_email, smtp_connection
from fpl_manager.schedule import decide_run
from fpl_manager.state import load_state, migrate_operator_state, save_state, team_row
from fpl_manager.subscribers import load_subscribers


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpl-manager")
    parser.add_argument("--fetch-summaries", action="store_true")
    parser.add_argument("--no-email", action="store_true", help="Print the weekly report to stdout.")
    parser.add_argument("--email", action="store_true", help="Send email if the deadline window says so.")
    parser.add_argument("--force", action="store_true", help="Send/print even outside the 60h window.")
    parser.add_argument("--optimize", action="store_true", help="Deprecated: always runs. Kept for compatibility.")
    parser.add_argument(
        "--list-subscribers",
        action="store_true",
        help="Print who would be emailed (Stripe + env) and exit.",
    )
    return parser


def _window_open(event: dict[str, Any], cfg: dict[str, Any], *, force: bool) -> bool:
    dummy = {"last_emailed_gw": None, "last_fingerprint": None}
    return decide_run(event, dummy, cfg, force=force) != "skip"


def _operator_inbox() -> str:
    return (os.environ.get("EMAIL_OPERATOR") or os.environ.get("EMAIL_TO") or "").strip()


def _notify_operator(subject: str, body: str) -> None:
    to_addr = _operator_inbox()
    if not to_addr:
        return
    try:
        send_email(subject, body, f"<pre>{body}</pre>", to_addr=to_addr)
    except Exception as exc:
        print(f"operator notify failed: {exc}", file=sys.stderr)


def _run_one_print(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    result = run_pipeline(cfg, fetch_summaries=args.fetch_summaries)
    print(_ascii(result["text"]))
    return 0


def _run_mail(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    try:
        shared = build_predictions(cfg, fetch_summaries=args.fetch_summaries)
    except Exception:
        _notify_operator("FPL manager FAILED", traceback.format_exc())
        raise

    event = shared["event"]
    gw = int(event["id"])
    if not _window_open(event, cfg, force=args.force):
        print(f"skip: outside email window (GW{gw})")
        return 0

    subscribers = load_subscribers(cfg)
    if not subscribers:
        print("no subscribers (set STRIPE_SECRET_KEY, SUBSCRIBERS_JSON, or EMAIL_TO + team_id)")
        return 1

    state = load_state()
    migrate_operator_state(state, int(cfg.get("team_id") or 0) or None)
    free_transfers = int(state.get("free_transfers") or 1)
    sent = skip = errors = paid_sent = 0
    lines: list[str] = []

    try:
        with smtp_connection() as smtp:
            for sub in subscribers:
                require_squad = sub.plan != "operator"
                try:
                    letter = letter_for_team(
                        cfg,
                        shared,
                        sub.team_id,
                        free_transfers=free_transfers,
                        require_squad=require_squad,
                    )
                    row = team_row(state, sub.team_id)
                    mode = decide_run(
                        event,
                        row,
                        cfg,
                        force=args.force,
                        fingerprint=letter["fp"],
                    )
                    if mode == "skip":
                        skip += 1
                        print(f"skip team {sub.team_id} ({sub.plan})")
                        continue
                    send_email(
                        letter["subject"],
                        letter["text"],
                        letter["html"],
                        to_addr=sub.email,
                        smtp=smtp,
                    )
                    row["last_emailed_gw"] = gw
                    row["last_fingerprint"] = letter["fp"]
                    if sub.plan == "operator":
                        state["last_emailed_gw"] = gw
                        state["last_fingerprint"] = letter["fp"]
                    else:
                        paid_sent += 1
                    sent += 1
                    print(f"emailed team {sub.team_id} ({mode})")
                except SquadError as exc:
                    errors += 1
                    msg = str(exc)
                    lines.append(msg)
                    print(msg, file=sys.stderr)
                except Exception:
                    errors += 1
                    detail = traceback.format_exc()
                    lines.append(f"team {sub.team_id}:\n{detail}")
                    print(f"error team {sub.team_id}", file=sys.stderr)
                    print(detail, file=sys.stderr)
    except Exception:
        _notify_operator("FPL manager FAILED", traceback.format_exc())
        raise

    save_state(state)
    summary = f"GW{gw}: sent {sent}, skip {skip}, error {errors}"
    print(summary)
    if lines:
        summary = summary + "\n\n" + "\n\n".join(lines)
    if errors or paid_sent:
        _notify_operator("FPL manager run", summary)
    return 1 if errors and sent == 0 else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    if args.list_subscribers:
        for row in load_subscribers(cfg):
            print(f"{row.team_id}\t{row.plan}\t{row.source}\t{row.email}")
        return 0
    send = bool(args.email) and not args.no_email
    if send:
        return _run_mail(cfg, args)
    try:
        return _run_one_print(cfg, args)
    except Exception:
        if send:
            _notify_operator("FPL manager FAILED", traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main())
