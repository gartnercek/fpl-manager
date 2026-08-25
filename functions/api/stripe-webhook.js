import { json, stripeForm, verifyStripeSignature } from "../_lib/stripe.js";

function sessionPaid(session) {
  return Boolean(session && session.payment_status && session.payment_status !== "unpaid");
}

function customerId(obj) {
  const customer = obj && obj.customer;
  return typeof customer === "string" ? customer : customer && customer.id;
}

async function stampCustomer(secret, session) {
  if (!sessionPaid(session)) return;
  const teamId = (session.metadata && session.metadata.team_id) || session.client_reference_id;
  const plan = (session.metadata && session.metadata.plan) || "";
  const season = (session.metadata && session.metadata.season) || "";
  const customer = customerId(session);
  if (!customer || !teamId) return;
  const params = new URLSearchParams();
  params.set("metadata[team_id]", String(teamId));
  if (plan) params.set("metadata[plan]", String(plan));
  if (season) params.set("metadata[season]", String(season));
  await stripeForm(secret, "/customers/" + customer, params);
}

async function stampFromSubscription(secret, sub) {
  const customer = customerId(sub);
  const md = (sub && sub.metadata) || {};
  const teamId = md.team_id;
  if (!customer || !teamId) return;
  const params = new URLSearchParams();
  params.set("metadata[team_id]", String(teamId));
  if (md.plan) params.set("metadata[plan]", String(md.plan));
  if (md.season) params.set("metadata[season]", String(md.season));
  await stripeForm(secret, "/customers/" + customer, params);
}

export async function onRequestPost({ request, env }) {
  const secret = env.STRIPE_SECRET_KEY;
  const hookSecret = env.STRIPE_WEBHOOK_SECRET;
  if (!secret || !hookSecret) {
    return json({ error: "Webhook is not configured." }, 503);
  }
  const payload = await request.text();
  const header = request.headers.get("stripe-signature") || "";
  const ok = await verifyStripeSignature(payload, header, hookSecret);
  if (!ok) return json({ error: "Bad signature." }, 400);

  let event;
  try {
    event = JSON.parse(payload);
  } catch {
    return json({ error: "Bad JSON." }, 400);
  }

  try {
    if (
      event.type === "checkout.session.completed" ||
      event.type === "checkout.session.async_payment_succeeded"
    ) {
      const session = event.data && event.data.object ? event.data.object : {};
      await stampCustomer(secret, session);
    } else if (
      event.type === "customer.subscription.created" ||
      event.type === "customer.subscription.updated"
    ) {
      const sub = event.data && event.data.object ? event.data.object : {};
      await stampFromSubscription(secret, sub);
    }
    // invoice.paid, invoice.payment_failed, customer.subscription.deleted,
    // checkout.session.async_payment_failed: acknowledged. Weekly mailer
    // re-reads Stripe for who is still entitled.
  } catch (err) {
    return json({ error: err.message || "Could not update customer." }, 502);
  }

  return json({ received: true });
}
