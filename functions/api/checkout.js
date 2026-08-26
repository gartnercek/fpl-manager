import { json, stripeForm, publicOrigin } from "../_lib/stripe.js";

const TEAM_RE = /^[1-9][0-9]{0,9}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const INTEGRATION_ID = "fpl-letter-vnhqxlrw";

function paymentsOpen(env) {
  return env.PAYMENTS_OPEN === "1" || env.PAYMENTS_OPEN === "true";
}

function managedPayments(env) {
  return env.STRIPE_MANAGED_PAYMENTS !== "0" && env.STRIPE_MANAGED_PAYMENTS !== "false";
}

export async function onRequestGet({ env }) {
  const open = paymentsOpen(env);
  const hasStripeKey = Boolean(env.STRIPE_SECRET_KEY);
  const hasPrices = Boolean(env.STRIPE_PRICE_SEASON && env.STRIPE_PRICE_MONTHLY);
  return json({
    ok: true,
    configured: hasStripeKey && hasPrices && open,
    payments_open: open,
    has_stripe_key: hasStripeKey,
    has_prices: hasPrices,
    managed_payments: managedPayments(env),
    beta: !open,
  });
}

async function findOrCreateCustomer(secret, { email, name, teamId, plan, season }) {
  const listed = await stripeForm(
    secret,
    "/customers",
    new URLSearchParams({ email, limit: "1" }),
    "GET"
  );
  const existing = (listed.data || [])[0];
  const params = new URLSearchParams();
  params.set("email", email);
  if (name) params.set("name", name);
  params.set("metadata[team_id]", teamId);
  params.set("metadata[plan]", plan);
  params.set("metadata[season]", season);
  if (existing && existing.id) {
    await stripeForm(secret, "/customers/" + existing.id, params);
    return existing.id;
  }
  const created = await stripeForm(secret, "/customers", params, "POST", {
    "Idempotency-Key": "fpl-cust-" + teamId + "-" + email.toLowerCase().slice(0, 80),
  });
  return created.id;
}

export async function onRequestPost({ request, env }) {
  if (!paymentsOpen(env)) {
    return json(
      { error: "Payments are not open yet. Finish Stripe account details, then set PAYMENTS_OPEN." },
      503
    );
  }
  const secret = env.STRIPE_SECRET_KEY;
  const priceSeason = env.STRIPE_PRICE_SEASON;
  const priceMonthly = env.STRIPE_PRICE_MONTHLY;
  if (!secret || !priceSeason || !priceMonthly) {
    const missing = [];
    if (!secret) missing.push("STRIPE_SECRET_KEY");
    if (!priceSeason) missing.push("STRIPE_PRICE_SEASON");
    if (!priceMonthly) missing.push("STRIPE_PRICE_MONTHLY");
    return json(
      {
        error:
          "Checkout is not configured yet. Add " +
          missing.join(" and ") +
          " on the Cloudflare Worker.",
        missing,
      },
      503
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Send JSON." }, 400);
  }

  const email = String(body.email || "").trim();
  const teamId = String(body.team_id || "").trim();
  const plan = body.plan === "monthly" ? "monthly" : "season";
  const name = String(body.name || "").trim().slice(0, 80);
  const season = env.STRIPE_SEASON || "2026/27";

  if (!EMAIL_RE.test(email)) return json({ error: "Need a working email." }, 400);
  if (!TEAM_RE.test(teamId)) return json({ error: "Need a numeric FPL team ID." }, 400);
  if (!body.consent) return json({ error: "Tick the privacy box." }, 400);

  let customerId;
  try {
    customerId = await findOrCreateCustomer(secret, {
      email,
      name,
      teamId,
      plan,
      season,
    });
  } catch (err) {
    return json({ error: err.message || "Could not create a Stripe customer." }, 502);
  }

  const origin = publicOrigin(request, env);
  const useManaged = managedPayments(env);
  const params = new URLSearchParams();
  const monthly = plan === "monthly";
  params.set("mode", monthly ? "subscription" : "payment");
  params.set("line_items[0][price]", monthly ? priceMonthly : priceSeason);
  params.set("line_items[0][quantity]", "1");
  params.set("customer", customerId);
  params.set("client_reference_id", teamId);
  params.set("success_url", origin + "/thanks.html?session_id={CHECKOUT_SESSION_ID}");
  params.set("cancel_url", origin + "/subscribe.html?cancelled=1");
  params.set("allow_promotion_codes", "true");
  params.set("billing_address_collection", "required");
  params.set("managed_payments[enabled]", useManaged ? "true" : "false");
  params.set("integration_identifier", INTEGRATION_ID);
  params.set("metadata[team_id]", teamId);
  params.set("metadata[plan]", plan);
  params.set("metadata[season]", season);
  if (name) params.set("metadata[name]", name);
  params.set(
    "custom_text[submit][message]",
    "Unofficial FPL advice. You still make the clicks on the official site."
  );

  if (monthly) {
    params.set("subscription_data[metadata][team_id]", teamId);
    params.set("subscription_data[metadata][plan]", "monthly");
    params.set("subscription_data[metadata][season]", season);
  } else {
    params.set("payment_intent_data[metadata][team_id]", teamId);
    params.set("payment_intent_data[metadata][plan]", "season");
    params.set("payment_intent_data[metadata][season]", season);
    if (!useManaged) params.set("invoice_creation[enabled]", "true");
  }

  // Managed Payments collects name and address itself. customer_update is rejected
  // on those sessions. Do not enable Stripe Tax automatic_tax without a registration.
  if (
    !useManaged &&
    (env.STRIPE_AUTOMATIC_TAX === "1" || env.STRIPE_AUTOMATIC_TAX === "true")
  ) {
    params.set("automatic_tax[enabled]", "true");
    params.set("customer_update[address]", "auto");
    params.set("customer_update[name]", "auto");
  }

  try {
    const session = await stripeForm(secret, "/checkout/sessions", params, "POST", {
      "Idempotency-Key": crypto.randomUUID(),
    });
    if (!session.url) return json({ error: "Stripe did not return a checkout URL." }, 502);
    return json({ url: session.url });
  } catch (err) {
    return json({ error: err.message || "Stripe checkout failed." }, err.status === 400 ? 400 : 502);
  }
}
