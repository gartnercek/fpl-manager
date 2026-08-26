import { json } from "../_lib/stripe.js";

const TEAM_RE = /^[1-9][0-9]{0,9}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_SIGNUPS = 50;
const KEY_PREFIX = "signup:";

function paymentsOpen(env) {
  return env.PAYMENTS_OPEN === "1" || env.PAYMENTS_OPEN === "true";
}

function clientIp(request) {
  return (
    request.headers.get("cf-connecting-ip") ||
    (request.headers.get("x-forwarded-for") || "").split(",")[0].trim() ||
    "unknown"
  );
}

function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const aa = enc.encode(String(a || ""));
  const bb = enc.encode(String(b || ""));
  if (aa.byteLength !== bb.byteLength) return false;
  let out = 0;
  for (let i = 0; i < aa.byteLength; i++) out |= aa[i] ^ bb[i];
  return out === 0;
}

function authorizedExport(request, env) {
  const secret = String(env.BETA_EXPORT_SECRET || "").trim();
  if (!secret) return false;
  const header = request.headers.get("authorization") || "";
  const token = header.toLowerCase().startsWith("bearer ") ? header.slice(7).trim() : "";
  return timingSafeEqual(token, secret);
}

async function allSignups(kv) {
  const out = [];
  let cursor;
  do {
    const page = await kv.list({ prefix: KEY_PREFIX, cursor });
    for (const key of page.keys) {
      const raw = await kv.get(key.name);
      if (!raw) continue;
      try {
        out.push(JSON.parse(raw));
      } catch {
        /* skip bad rows */
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

export async function onRequestGet({ request, env }) {
  const open = paymentsOpen(env);
  const kvReady = Boolean(env.BETA_SIGNUPS);
  if (request.headers.get("authorization")) {
    if (!authorizedExport(request, env)) {
      return json({ error: "Forbidden." }, 401);
    }
    if (!kvReady) return json({ error: "Beta list is not configured." }, 503);
    const signups = await allSignups(env.BETA_SIGNUPS);
    return json({ ok: true, count: signups.length, cap: MAX_SIGNUPS, signups });
  }
  return json({
    ok: true,
    beta: !open,
    payments_open: open,
    configured: Boolean(env.STRIPE_SECRET_KEY && env.STRIPE_PRICE_SEASON && env.STRIPE_PRICE_MONTHLY && open),
    kv: kvReady,
  });
}

export async function onRequestPost({ request, env }) {
  if (paymentsOpen(env)) {
    return json({ error: "Paid checkout is open. Use /api/checkout." }, 409);
  }
  const kv = env.BETA_SIGNUPS;
  if (!kv) return json({ error: "Beta signups are not stored on this host yet." }, 503);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Send JSON." }, 400);
  }

  if (String(body.company || body._gotcha || "").trim()) {
    return json({ ok: true, dummy: true });
  }

  const email = String(body.email || "").trim().toLowerCase();
  const teamId = String(body.team_id || "").trim();
  const name = String(body.name || "").trim().slice(0, 80);
  const plan = body.plan === "monthly" ? "monthly" : "season";

  if (!EMAIL_RE.test(email)) return json({ error: "Need a working email." }, 400);
  if (!TEAM_RE.test(teamId)) return json({ error: "Need a numeric FPL team ID." }, 400);
  if (!body.consent) return json({ error: "Tick the privacy box." }, 400);

  const ip = clientIp(request);
  const hour = new Date().toISOString().slice(0, 13);
  const rlKey = "rl:" + ip + ":" + hour;
  const rlRaw = await kv.get(rlKey);
  const rlCount = Number(rlRaw || 0);
  if (rlCount >= 8) return json({ error: "Too many tries from this network. Wait an hour." }, 429);
  await kv.put(rlKey, String(rlCount + 1), { expirationTtl: 3600 });

  const key = KEY_PREFIX + email;
  const existing = await kv.get(key);
  if (!existing) {
    const listed = await kv.list({ prefix: KEY_PREFIX });
    if ((listed.keys || []).length >= MAX_SIGNUPS) {
      return json(
        { error: "The free beta is full. Email support@fpl-manager.com if you want the waitlist." },
        409
      );
    }
  }

  const row = {
    email,
    team_id: teamId,
    name,
    plan,
    updated: new Date().toISOString(),
    created: existing ? undefined : new Date().toISOString(),
  };
  if (existing) {
    try {
      const prev = JSON.parse(existing);
      row.created = prev.created || row.updated;
    } catch {
      row.created = row.updated;
    }
  } else {
    row.created = row.updated;
  }

  await kv.put(key, JSON.stringify(row));
  return json({ ok: true, email, team_id: teamId, updated: Boolean(existing) });
}
