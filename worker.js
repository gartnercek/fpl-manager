/**
 * Cloudflare Worker: Stripe routes under /api/*, static files from site/.
 * Pages Functions keep their onRequest* handlers; this file only dispatches.
 */
import {
  onRequestGet as checkoutGet,
  onRequestPost as checkoutPost,
} from "./functions/api/checkout.js";
import { onRequestPost as portalPost } from "./functions/api/portal.js";
import { onRequestPost as webhookPost } from "./functions/api/stripe-webhook.js";

function pagesCtx(request, env, ctx) {
  return {
    request,
    env,
    ctx,
    waitUntil: (promise) => ctx.waitUntil(promise),
  };
}

function methodNotAllowed() {
  return new Response("Method not allowed", { status: 405 });
}

function clientScheme(request, url) {
  const visitor = request.headers.get("cf-visitor");
  if (visitor) {
    try {
      const scheme = JSON.parse(visitor).scheme;
      if (scheme === "http" || scheme === "https") return scheme;
    } catch {
      /* ignore malformed cf-visitor */
    }
  }
  const forwarded = (request.headers.get("x-forwarded-proto") || "").split(",")[0].trim();
  if (forwarded === "http" || forwarded === "https") return forwarded;
  return url.protocol === "https:" ? "https" : "http";
}

function canonicalHttpsUrl(request) {
  const url = new URL(request.url);
  const host = url.hostname === "www.fpl-manager.com" ? "fpl-manager.com" : url.hostname;
  const httpsUrl = new URL(url.toString());
  httpsUrl.protocol = "https:";
  httpsUrl.hostname = host;
  const alreadyCanonical = clientScheme(request, url) === "https" && host === url.hostname;
  if (alreadyCanonical) return null;
  return httpsUrl.toString();
}

export default {
  async fetch(request, env, ctx) {
    const canonical = canonicalHttpsUrl(request);
    if (canonical) {
      const status = request.method === "GET" || request.method === "HEAD" ? 301 : 308;
      return Response.redirect(canonical, status);
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const c = pagesCtx(request, env, ctx);

    if (path === "/api/checkout") {
      if (request.method === "GET") return checkoutGet(c);
      if (request.method === "POST") return checkoutPost(c);
      return methodNotAllowed();
    }
    if (path === "/api/portal") {
      if (request.method === "POST") return portalPost(c);
      return methodNotAllowed();
    }
    if (path === "/api/stripe-webhook") {
      if (request.method === "POST") return webhookPost(c);
      return methodNotAllowed();
    }

    return env.ASSETS.fetch(request);
  },
};
