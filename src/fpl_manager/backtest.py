from __future__ import annotations

import argparse
import sys

from fpl_manager.api import FPLClient, next_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl_manager.backtest")
    parser.add_argument("--season", default="current")
    parser.add_argument("--from-gw", type=int, default=2)
    args = parser.parse_args(argv)

    client = FPLClient()
    bootstrap = client.bootstrap()
    finished = [e for e in bootstrap["events"] if e.get("finished") and int(e["id"]) >= args.from_gw]
    if not finished:
        print(
            f"Backtest ({args.season}): no finished gameweeks >= GW{args.from_gw}. "
            "Re-run after GW2 is complete. This is expected before the season starts."
        )
        return 0

    print(f"Finished gameweeks: {[e['id'] for e in finished]}")
    print("Historical bootstrap snapshots are not stored yet; scoring uses live points only.")
    total_live = 0
    for event in finished:
        live = client.event_live(int(event["id"]))
        elements = {int(row["id"]): row for row in live.get("elements") or []}
        gw_sum = 0
        for row in elements.values():
            stats = row.get("stats") or {}
            gw_sum += int(stats.get("total_points") or 0)
        print(f"GW{event['id']}: {len(elements)} player rows, raw points mass {gw_sum}")
        total_live += gw_sum
    print("Do-nothing vs model comparison needs saved weekly plans in data/predictions/; none replayed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
