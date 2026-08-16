from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Matchup:
    event: int
    opponent: int
    is_home: bool
    difficulty: int
    kickoff: str | None


def index_fixtures(fixtures: Iterable[dict[str, Any]]) -> dict[int, dict[int, list[Matchup]]]:
    """team_id -> event -> list of matchups (empty = blank, 2+ = double)."""
    by_team: dict[int, dict[int, list[Matchup]]] = defaultdict(lambda: defaultdict(list))
    for row in fixtures:
        event = row.get("event")
        if event is None:
            continue
        home, away = int(row["team_h"]), int(row["team_a"])
        by_team[home][event].append(
            Matchup(
                event=event,
                opponent=away,
                is_home=True,
                difficulty=int(row.get("team_h_difficulty") or 3),
                kickoff=row.get("kickoff_time"),
            )
        )
        by_team[away][event].append(
            Matchup(
                event=event,
                opponent=home,
                is_home=False,
                difficulty=int(row.get("team_a_difficulty") or 3),
                kickoff=row.get("kickoff_time"),
            )
        )
    return by_team


def upcoming_events(next_event_id: int, horizon: int = 6) -> list[int]:
    return list(range(next_event_id, next_event_id + horizon))
