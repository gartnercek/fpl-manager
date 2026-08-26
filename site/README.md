# Public site (`site/`)

Canonical host: **https://fpl-manager.com**

Static HTML plus Cloudflare Pages Functions at the **repo root** (`functions/`). Checkout, webhooks, and the customer portal must run on this domain — GitHub Pages cannot take cards.

## 0. If the Cloudflare build runs Hugo or `npx wrangler deploy`

This is **not** a Hugo site and **not** a Python app. The public files are already in `site/`.

In the project → **Settings** → **Build**. Do **not** type the word `empty` into any box — Cloudflare will try to run a program named `empty` and fail.

| Setting | Value |
|---|---|
| Framework preset | None |
| Build command | leave the box **blank**. If it refuses to save, use `true` |
| Deploy command | `npx wrangler deploy` |
| Build output directory | `site` |
| Root directory | leave **blank** |

If Cloudflare still runs `pip install .`, add env var `SKIP_DEPENDENCY_INSTALL` = `true` (the Python package is for the weekly mailer, not the website).

Then **Retry deployment**.

## 1. DNS and deploy

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** → connect this GitHub repo.
2. Framework preset: None. **Build command:** empty. **Build output directory:** `site`. **Root directory:** empty.
3. After the first deploy, **Custom domains** → add `fpl-manager.com` and `www.fpl-manager.com`. Point the registrar nameservers at Cloudflare if the domain is not already on this account.
4. Email: **Email Routing** → catch-all or `support@fpl-manager.com` → your inbox. The public site already uses that address.
5. Google Search Console → add `https://fpl-manager.com/` → submit `https://fpl-manager.com/sitemap.xml`.

Local HTML only:

```bash
.venv\Scripts\python.exe -m http.server 8765 --directory site
```

Checkout locally (Node + test keys in `.dev.vars`):

```bash
npx wrangler pages dev site
```

## 2. Stripe (test, then live)

Run `python scripts/bootstrap_stripe.py` with a **test** secret. It creates season (€50) and monthly (€10) Prices with tax code `txcd_10103000` (SaaS, personal use), sets the account website and payout statement descriptor to FPL Manager, and points the Customer Portal at `https://fpl-manager.com/thanks.html`.

Dashboard (test, then repeat in live):

1. [Customer portal](https://dashboard.stripe.com/settings/billing/portal) — confirm return URL `https://fpl-manager.com/thanks.html`, privacy and terms `https://fpl-manager.com/legal.html`.
2. [Branding](https://dashboard.stripe.com/settings/branding) — public business URL `https://fpl-manager.com`.
3. [Payouts](https://dashboard.stripe.com/balance/overview) — confirm a bank account is listed. Skip [settings/payouts](https://dashboard.stripe.com/settings/payouts) if that page returns Method not allowed; the account already has payouts on and card descriptor `FPLMGR`.
4. [Managed Payments](https://dashboard.stripe.com/settings/managed-payments) — enable after business details and VAT are in. Checkout sets `managed_payments[enabled]=true`.
5. [Revenue recovery](https://dashboard.stripe.com/settings/billing/automatic) — Smart Retries and failed-payment emails on.
6. Developers → **Webhooks** → `https://fpl-manager.com/api/stripe-webhook`  
   Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
7. Prefer a [restricted key](https://docs.stripe.com/keys/restricted-api-keys) (`rk_…`). Live keys only after a test payment on this domain works.

Live catalog (FPL_Manager):

| Plan | Price ID | Amount |
|---|---|---|
| Season 2026/27 | `price_1U8lpoAoVac84U9IFNdW9zDc` | €50 one-time, tax inclusive |
| Monthly | `price_1U8lpoAoVac84U9IgSs9qRmz` | €10 / month, tax inclusive |

Those IDs are also in `wrangler.toml` `[vars]`, including `PAYMENTS_OPEN=1`. Worker **secrets** (not in git) are only the keys:

| Variable | Value |
|---|---|
| `STRIPE_SECRET_KEY` | live restricted key `rk_live_…` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` from the webhook endpoint |

They live in Cloudflare: **Workers & Pages → fpl-manager2 → Settings → Variables and Secrets**. They are not in the Stripe Dashboard.

Do not enable `STRIPE_AUTOMATIC_TAX=1` while Managed Payments is on. Set `STRIPE_MANAGED_PAYMENTS=0` only to turn that off.

Opening `https://fpl-manager.com/api/stripe-webhook` in a browser used to show “Method not allowed” because Stripe **POSTs** events there. A GET now returns a short JSON ok. Add the endpoint in Dashboard → Developers → Webhooks; do not test it by loading the URL as a page.

GitHub Actions secret `STRIPE_SECRET_KEY` (restricted read is enough) so the weekly job can list who paid.

## 3. What the form does

The form posts `{ email, team_id, plan, name, consent }` to `/api/checkout`. Stripe Checkout collects the card (Managed Payments). Success returns to `https://fpl-manager.com/thanks.html?session_id=…`. **Manage billing** opens the Customer Portal.

Do **not** set `PAYMENTS_OPEN=0` unless you need to close cards. Checkout is gated on that var.

`/api/beta` remains for the existing free-list export. The weekly job lists beta signups, then active Stripe subscriptions plus paid season Checkout Sessions for `2026/27`, then emails each public FPL team ID.
