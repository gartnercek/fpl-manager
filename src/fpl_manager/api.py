from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fpl_manager.config import ROOT

BASE_URL = "https://fantasy.premierleague.com/api"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class FPLUnavailable(RuntimeError):
    """FPL did not answer. Distinct from a bad squad or a solver bug."""


class FPLClient:
    """Read-only FPL JSON API with disk cache and retries."""

    def __init__(self, cache_dir: Path | None = None, session: requests.Session | None = None) -> None:
        day = date.today().isoformat()
        self.cache_dir = cache_dir or (ROOT / "data" / "cache" / day)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=1.2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _cache_path(self, name: str) -> Path:
        safe = name.strip("/").replace("/", "_")
        return self.cache_dir / f"{safe}.json"

    def get(self, path: str, *, use_cache: bool = True) -> Any:
        cache_path = self._cache_path(path)
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        url = f"{BASE_URL}/{path.lstrip('/')}"
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (429, 500, 502, 503, 504):
                raise FPLUnavailable(f"{path} (HTTP {status})") from exc
            raise
        except requests.RequestException as exc:
            raise FPLUnavailable(f"{path} ({exc.__class__.__name__})") from exc
        data = response.json()
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def bootstrap(self) -> dict[str, Any]:
        return self.get("bootstrap-static/")

    def fixtures(self) -> list[dict[str, Any]]:
        return self.get("fixtures/")

    def element_summary(self, player_id: int) -> dict[str, Any]:
        return self.get(f"element-summary/{player_id}/")

    def entry(self, team_id: int) -> dict[str, Any]:
        return self.get(f"entry/{team_id}/")

    def entry_history(self, team_id: int) -> dict[str, Any]:
        return self.get(f"entry/{team_id}/history/")

    def entry_transfers(self, team_id: int) -> list[dict[str, Any]]:
        return self.get(f"entry/{team_id}/transfers/")

    def entry_picks(self, team_id: int, event: int) -> dict[str, Any]:
        return self.get(f"entry/{team_id}/event/{event}/picks/")

    def event_live(self, event: int) -> dict[str, Any]:
        return self.get(f"event/{event}/live/")


def next_event(bootstrap: dict[str, Any]) -> dict[str, Any]:
    events = bootstrap["events"]
    for event in events:
        if event.get("is_next"):
            return event
    for event in events:
        if event.get("is_current") and not event.get("finished"):
            return event
    unfinished = [e for e in events if not e.get("finished")]
    if not unfinished:
        raise RuntimeError("No unfinished FPL gameweek found")
    return unfinished[0]


def deadline_utc(event: dict[str, Any]) -> datetime:
    raw = event["deadline_time"]
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
