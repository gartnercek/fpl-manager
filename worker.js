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

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.hostname === "www.fpl-manager.com") {
      url.hostname = "fpl-manager.com";
      return Response.redirect(url.toString(), 301);
    }

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
