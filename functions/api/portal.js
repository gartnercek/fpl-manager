import { json, stripeForm, publicOrigin } from "../_lib/stripe.js";

export async function onRequestPost({ request, env }) {
  const secret = env.STRIPE_SECRET_KEY;
  if (!secret) return json({ error: "Portal is not configured." }, 503);
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Send JSON." }, 400);
  }
  const sessionId = String(body.session_id || "").trim();
  if (!sessionId.startsWith("cs_")) {
    return json({ error: "Need a Checkout session id from the success URL." }, 400);
  }
  let session;
  try {
    session = await stripeForm(secret, "/checkout/sessions/" + encodeURIComponent(sessionId), null, "GET");
  } catch (err) {
    return json({ error: err.message || "Could not load that session." }, 400);
  }
  if (session.payment_status === "unpaid") {
    return json({ error: "Payment is not complete yet." }, 400);
  }
  const customer =
    typeof session.customer === "string"
      ? session.customer
      : session.customer && session.customer.id;
  if (!customer) {
    return json({ error: "This checkout has no Stripe customer yet." }, 400);
  }
  const origin = publicOrigin(request, env);
  const params = new URLSearchParams();
  params.set("customer", customer);
  params.set("return_url", origin + "/thanks.html?session_id=" + encodeURIComponent(sessionId));
  try {
    const portal = await stripeForm(secret, "/billing_portal/sessions", params);
    if (!portal.url) return json({ error: "Stripe did not return a portal URL." }, 502);
    return json({ url: portal.url });
  } catch (err) {
    return json({ error: err.message || "Could not open the billing portal." }, 502);
  }
}
