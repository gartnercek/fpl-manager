from __future__ import annotations

import json
import os
import smtplib
from contextlib import contextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from jinja2 import Template

from fpl_manager.api import deadline_utc
from fpl_manager.planner import CHIPS, HALF_END, Plan, SquadState, best_lineup, half_of
from fpl_manager.projection import Projection

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
CHIP_NAME = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}

TEXT_TEMPLATE = Template(
    """\
FPL Manager - GW{{ gw }}
Deadline: {{ deadline_local }} ({{ hours_left }}h left)

ACTION
{% for line in actions %}* {{ line }}
{% endfor %}
TRANSFERS
{% if transfers %}{% for t in transfers %}Out {{ t.out_name }} ({{ t.pos }}, {{ '%.1f'|format(t.out_price) }}m)  ->  In {{ t.in_name }} ({{ '%.1f'|format(t.in_price) }}m)   xP this GW {{ '%+.1f'|format(t.gain1) }}, next {{ horizon }} GWs {{ '%+.1f'|format(t.gainh) }}
{% endfor %}{% else %}No transfers{% if plan_mode %} ({{ plan_mode }}){% endif %}.
{% endif %}{{ value_line }}

STARTING XI  (xP {{ '%.1f'|format(xi_xp) }} incl. captain)
{% for row in xi %}  {{ '%-3s'|format(row.pos) }}{{ '%-4s'|format(row.flag) }} {{ '%-20s'|format(row.name) }} {{ '%-16s'|format(row.fixture) }} xP {{ '%4.1f'|format(row.xp) }}  P(start) {{ '%3.0f'|format(row.p_start * 100) }}%  {{ horizon }}GW {{ '%5.1f'|format(row.xph) }}  {{ '%4.1f'|format(row.price) }}m
{% endfor %}
BENCH (FPL order: slot 1 = backup GK, 2-4 = outfield auto-sub priority)
{% for row in bench %}  {{ row.slot }} {{ '%-3s'|format(row.pos) }} {{ '%-20s'|format(row.name) }} {{ '%-16s'|format(row.fixture) }} xP {{ '%4.1f'|format(row.xp) }}  P(start) {{ '%3.0f'|format(row.p_start * 100) }}%
{% endfor %}
CHIPS
{% for line in chip_lines %}{{ line }}
{% endfor %}
PLAN FOR THE NEXT WEEKS (re-solved every GW; only this GW is advice)
{% for line in ahead %}{{ line }}
{% endfor %}
RISK
{% for note in risk_notes %}* {{ note }}
{% endfor %}
APPENDIX - top 8 by position (this GW xP / next {{ horizon }} GWs)
{% for line in appendix %}{{ line }}
{% endfor %}
Method: xG-fitted team ratings, recency-weighted minutes and per-90 rates with last season as a
prior, FPL scoring incl. DEFCON; a {{ horizon }}-GW transfer MIP with free-transfer banking; chips by
optimal stopping inside each half. Sell prices from public transfer history. Backtested on 2025/26
and 2026/27 (python -m fpl_manager.backtest).
"""
)

HTML_TEMPLATE = Template(
    """\
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#111">
<h2>FPL Manager - GW{{ gw }}</h2>
<p>Deadline: <b>{{ deadline_local }}</b> ({{ hours_left }}h left)</p>
<h3>Action</h3>
<ul>{% for line in actions %}<li>{{ line }}</li>{% endfor %}</ul>
<h3>Transfers</h3>
{% if transfers %}
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Out</th><th>In</th><th>Pos</th><th>Sell</th><th>Buy</th><th>xP this GW</th><th>xP next {{ horizon }} GWs</th></tr>
{% for t in transfers %}
<tr><td>{{ t.out_name }}</td><td><b>{{ t.in_name }}</b></td><td>{{ t.pos }}</td><td>{{ '%.1f'|format(t.out_price) }}m</td><td>{{ '%.1f'|format(t.in_price) }}m</td><td>{{ '%+.1f'|format(t.gain1) }}</td><td>{{ '%+.1f'|format(t.gainh) }}</td></tr>
{% endfor %}
</table>
{% else %}
<p>No transfers{% if plan_mode %} ({{ plan_mode }}){% endif %}.</p>
{% endif %}
<p>{{ value_line }}</p>
<h3>Starting XI</h3>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th></th><th>Player</th><th>Fixture</th><th>xP</th><th>P(start)</th><th>{{ horizon }}GW xP</th><th>Price</th></tr>
{% for p in xi %}
<tr><td>{{ p.pos }}{{ p.flag }}</td><td>{{ p.name }}</td><td>{{ p.fixture }}</td><td>{{ '%.1f'|format(p.xp) }}</td><td>{{ '%.0f'|format(p.p_start * 100) }}%</td><td>{{ '%.1f'|format(p.xph) }}</td><td>{{ '%.1f'|format(p.price) }}</td></tr>
{% endfor %}
</table>
<p>XI xP incl. captain: {{ '%.1f'|format(xi_xp) }}</p>
<h3>Bench (auto-sub order)</h3>
<p>Put them in this order on the FPL bench. Slot 1 is the backup GK; slots 2-4 are outfield auto-sub priority.</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Slot</th><th></th><th>Player</th><th>Fixture</th><th>xP</th><th>P(start)</th></tr>
{% for p in bench %}
<tr><td>{{ p.slot }}</td><td>{{ p.pos }}</td><td>{{ p.name }}</td><td>{{ p.fixture }}</td><td>{{ '%.1f'|format(p.xp) }}</td><td>{{ '%.0f'|format(p.p_start * 100) }}%</td></tr>
{% endfor %}
</table>
<h3>Chips</h3>
<ul>{% for line in chip_lines %}<li>{{ line }}</li>{% endfor %}</ul>
<h3>Plan for the next weeks</h3>
<p style="color:#555">Re-solved every gameweek; only this gameweek is advice.</p>
<ul>{% for line in ahead %}<li>{{ line }}</li>{% endfor %}</ul>
<h3>Risk</h3>
<ul>{% for note in risk_notes %}<li>{{ note }}</li>{% endfor %}</ul>
<h3>Top 8 by position</h3>
<ul>{% for line in appendix %}<li>{{ line }}</li>{% endfor %}</ul>
<p style="color:#555;font-size:90%">xG-fitted team ratings, recency-weighted minutes and per-90 rates with last season as a prior,
FPL scoring incl. DEFCON. {{ horizon }}-GW transfer MIP with free-transfer banking; chips by optimal stopping inside each half.</p>
</body></html>
"""
)


def _fixture_label(proj: Projection, team: int, gw: int, team_names: dict[int, str]) -> str:
    bits = []
    for w, opp, home, _lf, _la in proj.fixtures.get(int(team), []):
        if w == gw:
            bits.append(f"{'H' if home else 'A'} {team_names.get(opp, opp)}")
    return ", ".join(bits) or "BLANK"


def pair_transfers(out_ids: list[int], in_ids: list[int], proj: Projection, gw: int, weeks: list[int],
                   state: SquadState) -> list[dict[str, Any]]:
    """Pair each outgoing player with a same-position incoming player."""
    t = proj.table
    remaining = list(in_ids)
    pairs: list[dict[str, Any]] = []

    def xp_sum(pid: int) -> float:
        return float(sum(t.at[pid, f"xp_{w}"] for w in weeks if f"xp_{w}" in t))

    for oid in sorted(out_ids, key=lambda i: (int(t.at[i, "pos"]), -int(t.at[i, "price"]))):
        pos = int(t.at[oid, "pos"])
        same = [i for i in remaining if int(t.at[i, "pos"]) == pos]
        pick = (same or remaining or [None])[0]
        if pick is not None:
            remaining.remove(pick)
        pairs.append(
            {
                "out_name": str(t.at[oid, "name"]),
                "in_name": str(t.at[pick, "name"]) if pick is not None else "-",
                "pos": POS[pos],
                "out_price": state.sell.get(oid, int(t.at[oid, "price"])) / 10,
                "in_price": (int(t.at[pick, "price"]) / 10) if pick is not None else 0.0,
                "gain1": (float(t.at[pick, f"xp_{gw}"]) if pick is not None else 0.0) - float(t.at[oid, f"xp_{gw}"]),
                "gainh": (xp_sum(pick) if pick is not None else 0.0) - xp_sum(oid),
            }
        )
    for iid in remaining:
        pairs.append(
            {
                "out_name": "-",
                "in_name": str(t.at[iid, "name"]),
                "pos": POS[int(t.at[iid, "pos"])],
                "out_price": 0.0,
                "in_price": int(t.at[iid, "price"]) / 10,
                "gain1": float(t.at[iid, f"xp_{gw}"]),
                "gainh": xp_sum(iid),
            }
        )
    return pairs


def _keep_value(proj: Projection, squad: list[int], weeks: list[int]) -> tuple[float, float]:
    """Expected points of the current 15 with no transfers: this GW, and over `weeks`."""
    t = proj.table
    pos = t.pos
    first = 0.0
    total = 0.0
    for i, w in enumerate(weeks):
        xp_w = {p: float(t.at[p, f"xp_{w}"]) for p in squad}
        xi, cap, _ = best_lineup(squad, xp_w, pos)
        val = sum(xp_w[p] for p in xi) + xp_w[cap]
        total += val
        if i == 0:
            first = val
    return first, total


def render_report(
    *,
    event: dict,
    plan: Plan,
    proj: Projection,
    state: SquadState,
    timezone: str,
    team_names: dict[int, str] | None = None,
    pending_transfers: int = 0,
) -> tuple[str, str, str]:
    names = team_names or {}
    tz = ZoneInfo(timezone)
    deadline = deadline_utc(event).astimezone(tz)
    now = datetime.now(tz)
    hours_left = max(int((deadline - now).total_seconds() // 3600), 0)
    gw = int(event["id"])
    t = proj.table
    wp = plan.first
    weeks = [w.gw for w in plan.weeks]
    horizon = len(weeks)
    initial = not state.squad

    def nm(pid: int) -> str:
        return str(t.at[pid, "name"])

    def xp(pid: int, w: int = gw) -> float:
        col = f"xp_{w}"
        return float(t.at[pid, col]) if col in t else 0.0

    transfers = [] if initial or wp.chip == "freehit" else pair_transfers(wp.transfers_out, wp.transfers_in, proj, gw, weeks, state)
    actions: list[str] = []
    if initial:
        actions.append("Pick this 15 before the GW1 deadline (changes are free until then).")
    if wp.chip:
        extra = f" on {nm(wp.captain)}" if wp.chip == "3xc" else ""
        reverts = " (squad reverts next GW)" if wp.chip == "freehit" else ""
        actions.append(f"PLAY {CHIP_NAME[wp.chip].upper()}{extra}{reverts}")
    if wp.chip == "freehit":
        actions.append("Free Hit squad: " + ", ".join(nm(p) for p in sorted(wp.squad, key=lambda p: (int(t.at[p, 'pos']), -xp(p)))))
    elif transfers:
        swaps = ", ".join(f"{x['out_name']} -> {x['in_name']}" for x in transfers)
        hit = f", hit -{4 * wp.hits}" if wp.hits else ""
        label = "Wildcard changes" if wp.chip == "wildcard" else "Transfers"
        actions.append(f"{label} ({len(transfers)}{hit}): {swaps}")
    elif not initial:
        actions.append(f"No transfers: roll the free transfer ({wp.ft_after} available next GW).")
    actions.append(f"Captain {nm(wp.captain)} ({_fixture_label(proj, t.at[wp.captain, 'team'], gw, names)}), vice {nm(wp.vice)}")
    n = {k: sum(1 for p in wp.starters if int(t.at[p, "pos"]) == k) for k in (2, 3, 4)}
    actions.append(f"Formation {n[2]}-{n[3]}-{n[4]}")
    actions.append("Bench order: " + ", ".join(f"{i}. {nm(p)}" for i, p in enumerate(wp.bench, start=1)))
    if not initial:
        actions.append(f"Free transfers: {state.ft} available this GW, {wp.ft_after} after this plan. Bank {state.bank / 10:.1f}m.")
    if pending_transfers:
        actions.append(
            f"Note: you already made {pending_transfers} transfer(s) for this GW; this letter is relative to "
            "your squad at the last deadline."
        )

    value_line = ""
    if not initial:
        keep1, keeph = _keep_value(proj, state.squad, weeks)
        plan1 = wp.xp
        planh = sum(w.xp for w in plan.weeks)
        value_line = (
            f"Plan xP this GW {plan1:.1f} vs {keep1:.1f} with no moves; next {horizon} GWs "
            f"{planh:.1f} vs {keeph:.1f} (incl. hits, chips and later planned moves)."
        )

    xi_rows = []
    for pid in sorted(wp.starters, key=lambda p: (int(t.at[p, "pos"]), -xp(p))):
        flag = " (C)" if pid == wp.captain else " (V)" if pid == wp.vice else ""
        xi_rows.append(_row(t, pid, gw, weeks, proj, names, flag=flag))
    bench_rows = []
    for slot, pid in enumerate(wp.bench, start=1):
        row = _row(t, pid, gw, weeks, proj, names)
        row["slot"] = slot
        bench_rows.append(row)
    mult = 3 if wp.chip == "3xc" else 2
    xi_xp = sum(xp(p) for p in wp.starters) + (mult - 1) * xp(wp.captain)

    chip_lines = [n for n in plan.notes if n.startswith(("PLAY", "HOLD")) or "chips left" in n]
    for h in (1, 2):
        left = [CHIP_NAME[c] for c in CHIPS if state.chips_left.get((c, h), False)]
        if h == half_of(gw) or (h == 2 and gw <= HALF_END):
            label = "GW1-19, expire after GW19" if h == 1 else "GW20-38"
            if h == 1 and gw > HALF_END:
                continue
            chip_lines.append(f"Chips left ({label}): {', '.join(left) if left else 'none'}")
    if not chip_lines:
        chip_lines.append("No chip decisions this GW.")

    ahead = []
    for later in plan.weeks[1:]:
        bits = []
        if later.chip:
            bits.append(f"play {CHIP_NAME[later.chip]}")
        if later.transfers_in:
            ins = ", ".join(nm(p) for p in later.transfers_in)
            outs = ", ".join(nm(p) for p in later.transfers_out)
            bits.append(f"{outs} -> {ins}" + (f" (hit -{4 * later.hits})" if later.hits else ""))
        if not bits:
            bits.append("no moves")
        bits.append(f"C {nm(later.captain)}, xP {later.xp:.1f}")
        ahead.append(f"GW{later.gw}: " + "; ".join(bits))

    risk = []
    for pid in wp.starters:
        ps = float(t.at[pid, "p_start"])
        av = float(t.at[pid, "avail"])
        news = ""
        if proj.news is not None and pid in proj.news.index:
            news = str(proj.news.at[pid] or "")
        if ps < 0.75 or av < 1.0:
            risk.append(f"{nm(pid)}: P(start) {ps:.0%}" + (f", {news}" if news else ""))
    if not risk:
        risk.append("No flagged starters (all P(start) >= 75%).")

    appendix = []
    for k in (1, 2, 3, 4):
        grp = t[t.pos == k].sort_values(f"xp_{gw}", ascending=False).head(8)
        appendix.append(
            POS[k]
            + ": "
            + ", ".join(
                f"{r.name} {getattr(r, f'xp_{gw}'):.1f}/{sum(getattr(r, f'xp_{w}') for w in weeks):.0f}"
                for r in grp.itertuples()
            )
        )

    ctx = {
        "gw": gw,
        "deadline_local": deadline.strftime("%a %d %b %H:%M %Z"),
        "hours_left": hours_left,
        "actions": actions,
        "transfers": transfers,
        "plan_mode": "initial squad" if initial else ("Free Hit" if wp.chip == "freehit" else ""),
        "value_line": value_line,
        "xi": xi_rows,
        "bench": bench_rows,
        "xi_xp": xi_xp,
        "chip_lines": chip_lines,
        "ahead": ahead,
        "risk_notes": risk,
        "appendix": appendix,
        "horizon": horizon,
    }
    text = TEXT_TEMPLATE.render(**ctx)
    html = HTML_TEMPLATE.render(**ctx)
    prefix = f"{CHIP_NAME[wp.chip].upper()}, " if wp.chip else ""
    moves = ""
    if transfers and wp.chip != "wildcard":
        moves = ", ".join(f"{x['out_name']}->{x['in_name']}" for x in transfers) + ", "
    subject = f"FPL GW{gw}: {prefix}{moves}{nm(wp.captain)} (C), xP {xi_xp:.1f}"
    return subject, text, html


def _row(t, pid: int, gw: int, weeks: list[int], proj: Projection, names: dict[int, str], flag: str = "") -> dict[str, Any]:
    return {
        "pos": POS[int(t.at[pid, "pos"])],
        "flag": flag,
        "name": f"{t.at[pid, 'name']} ({names.get(int(t.at[pid, 'team']), '')})",
        "fixture": _fixture_label(proj, t.at[pid, "team"], gw, names),
        "xp": float(t.at[pid, f"xp_{gw}"]),
        "xph": float(sum(t.at[pid, f"xp_{w}"] for w in weeks)),
        "p_start": float(t.at[pid, "p_start"]),
        "price": int(t.at[pid, "price"]) / 10,
    }


def fingerprint(plan: Plan) -> str:
    wp = plan.first
    payload = {
        "in": sorted(int(p) for p in wp.transfers_in),
        "out": sorted(int(p) for p in wp.transfers_out),
        "c": int(wp.captain),
        "v": int(wp.vice),
        "xi": sorted(int(p) for p in wp.starters),
        "chip": wp.chip,
    }
    return json.dumps(payload, sort_keys=True)


@contextmanager
def smtp_connection() -> Iterator[smtplib.SMTP]:
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (user and password):
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD must be set to send email")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        yield smtp


def send_email(
    subject: str,
    text: str,
    html: str,
    *,
    to_addr: str | None = None,
    smtp: smtplib.SMTP | None = None,
) -> None:
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = (to_addr or os.environ.get("EMAIL_TO") or "").strip()
    if not to_addr:
        raise RuntimeError("No recipient: pass to_addr or set EMAIL_TO")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user or to_addr
    msg["To"] = to_addr
    reply = (os.environ.get("EMAIL_REPLY_TO") or "").strip()
    if reply:
        msg["Reply-To"] = reply
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    payload = msg.as_string()
    if smtp is not None:
        from_addr = user or to_addr
        smtp.sendmail(from_addr, [to_addr], payload)
        return
    if not (user and password):
        raise RuntimeError("SMTP_USER, SMTP_PASSWORD, and a recipient must be set to send email")
    with smtp_connection() as connection:
        connection.sendmail(user, [to_addr], payload)
