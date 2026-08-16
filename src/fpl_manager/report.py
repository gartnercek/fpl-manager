from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from jinja2 import Template

from fpl_manager.api import deadline_utc
from fpl_manager.chips import ChipAdvice
from fpl_manager.models.aggregate import PlayerPrediction
from fpl_manager.optimize import Plan

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

TEXT_TEMPLATE = Template(
    """\
FPL Manager - GW{{ gw }}
Deadline: {{ deadline_local }} ({{ hours_left }}h left)

ACTION
{% for line in actions %}* {{ line }}
{% endfor %}
TRANSFERS
{% if plan.in_ids %}
{% for a, b in transfers %}Out {{ a }}  ->  In {{ b }}
{% endfor %}
{% else %}No transfers ({{ plan.mode }}).
{% endif %}
STARTING XI  (xP {{ '%.2f'|format(plan.xi_mu) }} +/- {{ '%.2f'|format(plan.xi_sd) }})
{% for row in xi_rows %}{{ row }}
{% endfor %}
BENCH (auto-sub order: slot 1 = backup GK, 2-4 = outfield priority)
{% for row in bench_rows %}{{ row }}
{% endfor %}
CHIPS
{{ chips.triple_captain.reason }}
{{ chips.bench_boost.reason }}
{% if chips.wildcard_note %}{{ chips.wildcard_note }}
{% endif %}{% if chips.free_hit_note %}{{ chips.free_hit_note }}
{% endif %}
RISK
{% for note in risk_notes %}* {{ note }}
{% endfor %}
APPENDIX - top 8 by position (next GW xP)
{% for line in appendix %}{{ line }}
{% endfor %}
Approximations: variance components independent; bonus = 0.3*(G+A); sell prices from public history;
team strength from FPL ratings; FT future value is a constant.
"""
)

HTML_TEMPLATE = Template(
    """\
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#111">
<h2>FPL Manager - GW{{ gw }}</h2>
<p>Deadline: <b>{{ deadline_local }}</b> ({{ hours_left }}h left)</p>
<h3>Action</h3>
<ul>{% for line in actions %}<li>{{ line }}</li>{% endfor %}</ul>
<h3>Starting XI</h3>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th></th><th>Player</th><th>xP</th><th>5GW</th><th>sd</th><th>Price</th></tr>
{% for p in xi %}
<tr><td>{{ p.pos }}{{ p.flag }}</td><td>{{ p.name }}</td><td>{{ '%.2f'|format(p.mu) }}</td><td>{{ '%.1f'|format(p.mu5) }}</td><td>{{ '%.2f'|format(p.sd) }}</td><td>{{ '%.1f'|format(p.price) }}</td></tr>
{% endfor %}
</table>
<p>XI xP with captain: {{ '%.2f'|format(plan.xi_mu) }} ± {{ '%.2f'|format(plan.xi_sd) }}</p>
<h3>Bench (auto-sub order)</h3>
<p>Put them in this order on the FPL bench. Slot 1 is usually the backup GK; slots 2-4 are outfield auto-sub priority.</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Slot</th><th></th><th>Player</th><th>xP</th><th>5GW</th><th>sd</th><th>Price</th></tr>
{% for p in bench %}
<tr><td>{{ p.slot }}</td><td>{{ p.pos }}</td><td>{{ p.name }}</td><td>{{ '%.2f'|format(p.mu) }}</td><td>{{ '%.1f'|format(p.mu5) }}</td><td>{{ '%.2f'|format(p.sd) }}</td><td>{{ '%.1f'|format(p.price) }}</td></tr>
{% endfor %}
</table>
<h3>Chips</h3>
<p>{{ chips.triple_captain.reason }}</p>
<p>{{ chips.bench_boost.reason }}</p>
{% if chips.wildcard_note %}<p>{{ chips.wildcard_note }}</p>{% endif %}
<h3>Risk</h3>
<ul>{% for note in risk_notes %}<li>{{ note }}</li>{% endfor %}</ul>
</body></html>
"""
)


def _flag(plan: Plan, pid: int) -> str:
    if pid == plan.captain:
        return " (C)"
    if pid == plan.vice:
        return " (V)"
    return ""


def render_report(
    *,
    event: dict,
    plan: Plan,
    preds: list[PlayerPrediction],
    chips: ChipAdvice,
    timezone: str,
) -> tuple[str, str, str]:
    tz = ZoneInfo(timezone)
    deadline = deadline_utc(event).astimezone(tz)
    now = datetime.now(tz)
    hours_left = max(int((deadline - now).total_seconds() // 3600), 0)
    by_id = {p.player_id: p for p in preds}
    cap = by_id[plan.captain]
    actions = [
        f"Captain {cap.web_name} (vice {by_id[plan.vice].web_name})",
        f"XI xP {plan.xi_mu:.1f}  cost {plan.cost / 10:.1f}m  bank {plan.bank / 10:.1f}m",
        chips.triple_captain.reason.split(":")[0],
        chips.bench_boost.reason.split(":")[0],
    ]
    if plan.notes:
        actions.insert(1, plan.notes[0])
    if plan.in_ids:
        actions.insert(0, f"Transfers ({plan.transfers}, hit -{plan.hit_cost}): {plan.out_ids} -> {plan.in_ids}")
    else:
        actions.insert(0, "No transfers required" if plan.mode != "initial" else "Set this 15 before the GW1 deadline")

    n_def = sum(1 for pid in plan.starters if by_id[pid].position == 2)
    n_mid = sum(1 for pid in plan.starters if by_id[pid].position == 3)
    n_fwd = sum(1 for pid in plan.starters if by_id[pid].position == 4)
    actions.append(f"Formation {n_def}-{n_mid}-{n_fwd}")

    xi = []
    xi_rows = []
    for pid in sorted(plan.starters, key=lambda i: (by_id[i].position, -by_id[i].mu)):
        p = by_id[pid]
        flag = _flag(plan, pid)
        label = p.web_name
        if p.roles:
            label = f"{label} [{'/'.join(p.roles)}]"
        xi.append(
            {
                "pos": POS[p.position],
                "flag": flag,
                "name": label,
                "mu": p.mu,
                "sd": p.sd,
                "price": p.now_cost / 10,
                "mu5": p.mu_horizon.get(5, p.mu),
            }
        )
        xi_rows.append(
            f"  {POS[p.position]}{flag:4} {label:<22} {p.mu:5.2f}  5GW {p.mu_horizon.get(5, p.mu):5.1f} +/- {p.sd:4.2f}  {p.now_cost / 10:4.1f}m"
        )

    bench = []
    bench_rows = []
    for slot, pid in enumerate(plan.bench, start=1):
        p = by_id[pid]
        blabel = p.web_name
        if p.roles:
            blabel = f"{blabel} [{'/'.join(p.roles)}]"
        bench.append(
            {
                "slot": slot,
                "pos": POS[p.position],
                "name": blabel,
                "mu": p.mu,
                "sd": p.sd,
                "price": p.now_cost / 10,
                "mu5": p.mu_horizon.get(5, p.mu),
            }
        )
        bench_rows.append(
            f"  {slot} {POS[p.position]:<3} {blabel:<22} {p.mu:5.2f} +/- {p.sd:4.2f}  {p.now_cost / 10:4.1f}m"
        )
    bench_names = ", ".join(f"{b['slot']}. {b['name']}" for b in bench)
    if bench_names:
        actions.append(f"Bench order: {bench_names}")

    transfers = list(zip([by_id[i].web_name for i in plan.out_ids], [by_id[i].web_name for i in plan.in_ids]))
    risk_notes = []
    for pid in plan.starters:
        p = by_id[pid]
        if p.minutes.p_play < 0.75:
            risk_notes.append(f"{p.web_name}: P(play)={p.minutes.p_play:.2f} {p.news}".strip())
        if p.status not in ("a",):
            risk_notes.append(f"{p.web_name}: status={p.status} {p.news}".strip())
    if not risk_notes:
        risk_notes.append("No flagged starters (P(play) >= 0.75).")

    appendix = []
    for pos in (1, 2, 3, 4):
        group = sorted([p for p in preds if p.position == pos], key=lambda p: p.mu, reverse=True)[:8]
        appendix.append(
            POS[pos]
            + ": "
            + ", ".join(
                f"{p.web_name}{' [' + '/'.join(p.roles) + ']' if p.roles else ''} {p.mu:.1f}"
                for p in group
            )
        )

    ctx = {
        "gw": event["id"],
        "deadline_local": deadline.strftime("%a %d %b %H:%M %Z"),
        "hours_left": hours_left,
        "actions": actions,
        "plan": plan,
        "transfers": transfers,
        "xi_rows": xi_rows,
        "bench_rows": bench_rows,
        "xi": xi,
        "bench": bench,
        "chips": chips,
        "risk_notes": risk_notes,
        "appendix": appendix,
    }
    text = TEXT_TEMPLATE.render(**ctx)
    html = HTML_TEMPLATE.render(**ctx)
    subject = f"FPL GW{event['id']}: {cap.web_name} (C), xP {plan.xi_mu:.1f}"
    return subject, text, html


def fingerprint(plan: Plan, chips: ChipAdvice) -> str:
    payload = {
        "in": sorted(plan.in_ids),
        "out": sorted(plan.out_ids),
        "c": plan.captain,
        "v": plan.vice,
        "xi": sorted(plan.starters),
        "tc": chips.triple_captain.play,
        "bb": chips.bench_boost.play,
    }
    return json.dumps(payload, sort_keys=True)


def send_email(subject: str, text: str, html: str) -> None:
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("EMAIL_TO")
    if not (user and password and to_addr):
        raise RuntimeError("SMTP_USER, SMTP_PASSWORD, and EMAIL_TO must be set to send email")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(user, [to_addr], msg.as_string())
