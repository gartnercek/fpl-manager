/** Shared Stripe REST helpers for Pages Functions. Not a public route. */

export function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...extra,
    },
  });
}

const STRIPE_API_VERSION = "2026-07-29.dahlia";
export const DEFAULT_SITE_ORIGIN = "https://fpl-manager.com";

export function publicOrigin(request, env) {
  const configured = String((env && env.SITE_ORIGIN) || "")
    .trim()
    .replace(/\/$/, "");
  if (configured) return configured;
  try {
    return new URL(request.url).origin;
  } catch {
    return DEFAULT_SITE_ORIGIN;
  }
}

export async function stripeForm(secret, path, params, method = "POST", extraHeaders = {}) {
  const qs =
    method === "GET" && params
      ? "?" + (params instanceof URLSearchParams ? params.toString() : toForm(params).toString())
      : "";
  const body =
    method === "GET" || !params
      ? undefined
      : params instanceof URLSearchParams
        ? params
        : toForm(params);
  const res = await fetch("https://api.stripe.com/v1" + path + qs, {
    method,
    headers: {
      authorization: "Bearer " + secret,
      "content-type": "application/x-www-form-urlencoded",
      "Stripe-Version": STRIPE_API_VERSION,
      ...extraHeaders,
    },
    body,
  });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { error: { message: text.slice(0, 400) } };
  }
  if (!res.ok) {
    const msg = (data.error && data.error.message) || text.slice(0, 400);
    const err = new Error(msg);
    err.status = res.status;
    err.payload = data;
    throw err;
  }
  return data;
}

function toForm(obj) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(obj || {})) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  return params;
}

export async function verifyStripeSignature(payload, header, secret, nowMs = Date.now()) {
  if (!header || !secret) return false;
  const parts = {};
  for (const item of header.split(",")) {
    const idx = item.indexOf("=");
    if (idx === -1) continue;
    parts[item.slice(0, idx).trim()] = item.slice(idx + 1).trim();
  }
  const timestamp = Number(parts.t);
  if (!timestamp || Math.abs(nowMs / 1000 - timestamp) > 300) return false;
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, encoder.encode(`${timestamp}.${payload}`));
  const hex = [...new Uint8Array(sigBuf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return timingSafeEqual(hex, parts.v1 || "");
}

function timingSafeEqual(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return out === 0;
}
