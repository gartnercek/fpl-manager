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

Run `python scripts/bootstrap_stripe.py` with a **test** secret. It creates season (€79) and monthly (€19) Prices, sets the account website and payout statement descriptor to FPL Manager, and points the Customer Portal at `https://fpl-manager.com/thanks.html`.

Dashboard (test, then repeat in live):

1. [Customer portal](https://dashboard.stripe.com/settings/billing/portal) — confirm return URL `https://fpl-manager.com/thanks.html`, privacy and terms `https://fpl-manager.com/legal.html`.
2. [Branding](https://dashboard.stripe.com/settings/branding) — public business URL `https://fpl-manager.com`.
3. [Payouts](https://dashboard.stripe.com/settings/payouts) — bank account and statement descriptor `FPLMANAGER`.
4. [Revenue recovery](https://dashboard.stripe.com/settings/billing/automatic) — Smart Retries and failed-payment emails on.
5. Developers → **Webhooks** → `https://fpl-manager.com/api/stripe-webhook`  
   Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
6. Prefer a [restricted key](https://docs.stripe.com/keys/restricted-api-keys) (`rk_…`). Live keys only after a test payment on this domain works.

Cloudflare Pages → **Settings** → **Environment variables** (Production):

| Variable | Value |
|---|---|
| `STRIPE_SECRET_KEY` | `rk_test_…` then live after a test payment |
| `STRIPE_PRICE_SEASON` | `price_…` one-time €79 |
| `STRIPE_PRICE_MONTHLY` | `price_…` recurring €19 |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` |
| `STRIPE_SEASON` | `2026/27` |
| `STRIPE_AUTOMATIC_TAX` | `0` unless Stripe Tax is registered |
| `SITE_ORIGIN` | `https://fpl-manager.com` |

GitHub Actions secret `STRIPE_SECRET_KEY` (restricted read is enough) so the weekly job can list who paid.

Do not enable `STRIPE_AUTOMATIC_TAX=1` until an active tax registration exists. See [collect taxes for recurring payments](https://docs.stripe.com/billing/taxes/collect-taxes).

## 3. What the form does

The form posts `{ email, team_id, plan }` to `/api/checkout`. Stripe Checkout collects the card. Success returns to `https://fpl-manager.com/thanks.html?session_id=…`. **Manage billing** opens the Customer Portal.

The weekly job lists active Stripe subscriptions plus paid season Checkout Sessions for `2026/27`, then emails each public FPL team ID.
