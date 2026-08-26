from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Subscriber:
    team_id: int
    email: str
    plan: str
    source: str
    stripe_id: str = ""


def team_id_from_session(session: dict[str, Any]) -> int:
    md = session.get("metadata") or {}
    raw = md.get("team_id") or session.get("client_reference_id") or ""
    parsed = _positive_int(raw)
    if parsed:
        return parsed
    for field in session.get("custom_fields") or []:
        key = str(field.get("key") or "").lower()
        label = str(((field.get("label") or {}).get("custom")) or "").lower()
        if "team" not in key and "team" not in label and "fpl" not in key:
            continue
        if field.get("numeric") and field["numeric"].get("value") is not None:
            parsed = _positive_int(field["numeric"]["value"])
        else:
            parsed = _positive_int(((field.get("text") or {}).get("value")))
        if parsed:
            return parsed
    return 0


def _positive_int(value: Any) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _email_from_customer(customer: Any) -> str:
    if isinstance(customer, dict):
        return str(customer.get("email") or "").strip()
    return ""


def subscriber_from_subscription(sub: dict[str, Any]) -> Subscriber | None:
    if str(sub.get("status") or "") not in {"active", "trialing", "past_due"}:
        return None
    md = dict(sub.get("metadata") or {})
    customer = sub.get("customer")
    if isinstance(customer, dict):
        cmd = customer.get("metadata") or {}
        md = {**cmd, **md}
    team_id = _positive_int(md.get("team_id"))
    email = _email_from_customer(customer) or str(sub.get("email") or "").strip()
    if not team_id or "@" not in email:
        return None
    plan = str(md.get("plan") or "monthly")
    if plan not in {"monthly", "season"}:
        plan = "monthly"
    return Subscriber(
        team_id=team_id,
        email=email,
        plan=plan,
        source="stripe_subscription",
        stripe_id=str(sub.get("id") or ""),
    )


def subscriber_from_session(session: dict[str, Any], season: str) -> Subscriber | None:
    if session.get("status") != "complete" or session.get("payment_status") != "paid":
        return None
    if session.get("mode") != "payment":
        return None
    md = session.get("metadata") or {}
    if md.get("plan") == "monthly":
        return None
    listed_season = str(md.get("season") or season)
    if listed_season != season:
        return None
    team_id = team_id_from_session(session)
    details = session.get("customer_details") or {}
    email = str(session.get("customer_email") or details.get("email") or "").strip()
    if not team_id or "@" not in email:
        return None
    return Subscriber(
        team_id=team_id,
        email=email,
        plan="season",
        source="stripe_session",
        stripe_id=str(session.get("id") or ""),
    )


def merge_subscribers(
    subscriptions: Iterable[dict[str, Any]],
    sessions: Iterable[dict[str, Any]],
    *,
    season: str,
) -> dict[int, Subscriber]:
    found: dict[int, Subscriber] = {}
    for sub in subscriptions:
        row = subscriber_from_subscription(sub)
        if row:
            found[row.team_id] = row
    for session in sessions:
        row = subscriber_from_session(session, season)
        if row and row.team_id not in found:
            found[row.team_id] = row
    return found


def parse_beta_payload(payload: Any) -> list[Subscriber]:
    if isinstance(payload, dict):
        rows = payload.get("signups")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("beta list must be a JSON list or {signups: [...]}")
    out: list[Subscriber] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        team_id = _positive_int(item.get("team_id"))
        email = str(item.get("email") or "").strip()
        plan = str(item.get("plan") or "beta")
        if plan not in {"monthly", "season", "beta"}:
            plan = "beta"
        if team_id and "@" in email:
            out.append(
                Subscriber(
                    team_id=team_id,
                    email=email,
                    plan=plan,
                    source="beta",
                )
            )
    return out


def fetch_beta_subscribers(url: str, secret: str) -> list[Subscriber]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + secret,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Beta list {exc.code}: {body[:400]}") from exc
    return parse_beta_payload(payload)


def parse_subscribers_json(raw: str) -> list[Subscriber]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("SUBSCRIBERS_JSON must be a JSON list")
    out: list[Subscriber] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        team_id = _positive_int(item.get("team_id"))
        email = str(item.get("email") or "").strip()
        plan = str(item.get("plan") or "season")
        if team_id and "@" in email:
            out.append(
                Subscriber(
                    team_id=team_id,
                    email=email,
                    plan=plan,
                    source="env",
                    stripe_id=str(item.get("stripe_id") or ""),
                )
            )
    return out


def blocked_team_ids() -> set[int]:
    raw = os.environ.get("BILLING_BLOCKLIST") or ""
    out: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        n = _positive_int(part)
        if n:
            out.add(n)
    return out


def stripe_get(secret: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    url = "https://api.stripe.com/v1" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + secret,
            "Stripe-Version": "2026-07-29.dahlia",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Stripe {exc.code} {path}: {body[:400]}") from exc


def stripe_paginate(secret: str, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    query = dict(params)
    query.setdefault("limit", "100")
    rows: list[dict[str, Any]] = []
    while True:
        payload = stripe_get(secret, path, query)
        chunk = list(payload.get("data") or [])
        rows.extend(chunk)
        if not payload.get("has_more") or not chunk:
            break
        query["starting_after"] = str(chunk[-1]["id"])
    return rows


def fetch_stripe_subscribers(secret: str, season: str) -> list[Subscriber]:
    subscriptions: list[dict[str, Any]] = []
    for status in ("active", "trialing", "past_due"):
        subscriptions.extend(
            stripe_paginate(
                secret,
                "/subscriptions",
                {"status": status, "limit": "100", "expand[]": "data.customer"},
            )
        )
    sessions = stripe_paginate(
        secret,
        "/checkout/sessions",
        {"limit": "100", "status": "complete"},
    )
    return list(merge_subscribers(subscriptions, sessions, season=season).values())


def load_subscribers(cfg: dict[str, Any]) -> list[Subscriber]:
    season = str((cfg.get("billing") or {}).get("season") or "2026/27")
    found: dict[int, Subscriber] = {}
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if secret:
        for row in fetch_stripe_subscribers(secret, season):
            found[row.team_id] = row
    beta_secret = (os.environ.get("BETA_EXPORT_SECRET") or "").strip()
    if beta_secret:
        beta_url = (os.environ.get("BETA_LIST_URL") or "https://fpl-manager.com/api/beta").strip()
        for row in fetch_beta_subscribers(beta_url, beta_secret):
            found.setdefault(row.team_id, row)
    raw = (os.environ.get("SUBSCRIBERS_JSON") or "").strip()
    if raw:
        for row in parse_subscribers_json(raw):
            found[row.team_id] = row
    operator_email = (os.environ.get("EMAIL_TO") or "").strip()
    operator_team = int(cfg.get("team_id") or 0)
    if operator_email and operator_team and operator_team not in found:
        found[operator_team] = Subscriber(
            team_id=operator_team,
            email=operator_email,
            plan="operator",
            source="env",
        )
    blocked = blocked_team_ids()
    return [row for row in found.values() if row.team_id not in blocked]
