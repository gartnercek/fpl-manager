"""Create FPL Manager test products and prices. Reads STRIPE_SECRET_KEY from the environment or .env."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEV_VARS = ROOT / ".dev.vars"


def load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def stripe(secret: str, method: str, path: str, fields: dict[str, str] | None = None) -> dict:
    url = "https://api.stripe.com/v1" + path
    data = None
    headers = {
        "Authorization": "Bearer " + secret,
        "Stripe-Version": "2026-07-29.dahlia",
    }
    if method == "GET" and fields:
        url += "?" + urllib.parse.urlencode(fields)
    elif fields:
        data = urllib.parse.urlencode(fields).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Stripe {exc.code} {path}: {body[:800]}") from exc


def find_product(secret: str, role: str) -> dict | None:
    payload = stripe(secret, "GET", "/products", {"limit": "100", "active": "true"})
    for product in payload.get("data") or []:
        md = product.get("metadata") or {}
        if md.get("role") == role:
            return product
    return None


SITE_ORIGIN = "https://fpl-manager.com"
SUPPORT_EMAIL = "support@fpl-manager.com"


def stamp_product(secret: str, product_id: str, *, name: str, description: str, role: str) -> dict:
    return stripe(
        secret,
        "POST",
        "/products/" + product_id,
        {
            "name": name,
            "description": description,
            "tax_code": "txcd_10103000",
            "url": SITE_ORIGIN + "/subscribe.html",
            "statement_descriptor": "FPL MANAGER",
            "metadata[role]": role,
            "metadata[season]": "2026/27",
        },
    )


def ensure_product(secret: str, *, role: str, name: str, description: str) -> dict:
    existing = find_product(secret, role)
    if existing:
        return stamp_product(secret, existing["id"], name=name, description=description, role=role)
    return stripe(
        secret,
        "POST",
        "/products",
        {
            "name": name,
            "description": description,
            "tax_code": "txcd_10103000",
            "url": SITE_ORIGIN + "/subscribe.html",
            "statement_descriptor": "FPL MANAGER",
            "metadata[role]": role,
            "metadata[season]": "2026/27",
        },
    )


def apply_public_branding(secret: str) -> None:
    payload = stripe(secret, "GET", "/billing_portal/configurations", {"limit": "20"})
    updated = 0
    for cfg in payload.get("data") or []:
        stripe(
            secret,
            "POST",
            "/billing_portal/configurations/" + cfg["id"],
            {
                "business_profile[headline]": "FPL Manager",
                "business_profile[privacy_policy_url]": SITE_ORIGIN + "/legal.html",
                "business_profile[terms_of_service_url]": SITE_ORIGIN + "/legal.html",
                "default_return_url": SITE_ORIGIN + "/thanks.html",
            },
        )
        updated += 1
    print(f"updated {updated} customer portal configuration(s)")
    if updated:
        return
    stripe(
        secret,
        "POST",
        "/billing_portal/configurations",
        {
            "business_profile[headline]": "FPL Manager",
            "business_profile[privacy_policy_url]": SITE_ORIGIN + "/legal.html",
            "business_profile[terms_of_service_url]": SITE_ORIGIN + "/legal.html",
            "features[customer_update][enabled]": "true",
            "features[customer_update][allowed_updates][0]": "email",
            "features[invoice_history][enabled]": "true",
            "features[payment_method_update][enabled]": "true",
            "features[subscription_cancel][enabled]": "true",
            "features[subscription_cancel][mode]": "at_period_end",
            "default_return_url": SITE_ORIGIN + "/thanks.html",
        },
    )
    print("customer portal configuration created")


def ensure_price(secret: str, product_id: str, *, unit_amount: int, recurring: bool) -> dict:
    payload = stripe(secret, "GET", "/prices", {"product": product_id, "limit": "20", "active": "true"})
    for price in payload.get("data") or []:
        if int(price.get("unit_amount") or 0) != unit_amount:
            continue
        rec = price.get("recurring")
        if recurring and rec and rec.get("interval") == "month":
            return price
        if not recurring and not rec:
            return price
    fields = {
        "product": product_id,
        "currency": "eur",
        "unit_amount": str(unit_amount),
        "tax_behavior": "inclusive",
    }
    if recurring:
        fields["recurring[interval]"] = "month"
    return stripe(secret, "POST", "/prices", fields)


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    lines: list[str] = []
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else ""
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    file_env = load_dotenv(ENV_PATH)
    secret = (os.environ.get("STRIPE_SECRET_KEY") or file_env.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret.startswith("sk_test_") and not secret.startswith("rk_test_"):
        raise SystemExit("Need a test-mode key (sk_test_ / rk_test_) in STRIPE_SECRET_KEY or .env")
    account = stripe(secret, "GET", "/account")
    print(f"account {account.get('id')}  {account.get('business_profile', {}).get('name') or account.get('settings', {}).get('dashboard', {}).get('display_name') or ''}  livemode={account.get('livemode')}")

    season_prod = ensure_product(
        secret,
        role="season",
        name="FPL Manager season 2026/27",
        description="Weekly unofficial FPL letter for the 2026/27 season. You apply transfers yourself.",
    )
    month_prod = ensure_product(
        secret,
        role="monthly",
        name="FPL Manager monthly",
        description="Weekly unofficial FPL letter, billed monthly. Cancel from the Stripe customer portal.",
    )
    season_price = ensure_price(secret, season_prod["id"], unit_amount=5000, recurring=False)
    month_price = ensure_price(secret, month_prod["id"], unit_amount=1000, recurring=True)
    print(f"season product {season_prod['id']}  price {season_price['id']}")
    print(f"monthly product {month_prod['id']}  price {month_price['id']}")

    try:
        apply_public_branding(secret)
    except SystemExit as exc:
        print(f"branding skipped: {exc}")

    updates = {
        "STRIPE_PRICE_SEASON": season_price["id"],
        "STRIPE_PRICE_MONTHLY": month_price["id"],
        "STRIPE_SEASON": "2026/27",
        "STRIPE_AUTOMATIC_TAX": file_env.get("STRIPE_AUTOMATIC_TAX") or "0",
        "SITE_ORIGIN": "https://fpl-manager.com",
    }
    upsert_env(ENV_PATH, updates)
    upsert_env(DEV_VARS, {"STRIPE_SECRET_KEY": secret, **updates})
    print(f"wrote price ids to {ENV_PATH.name} and {DEV_VARS.name} (gitignored)")


if __name__ == "__main__":
    main()
