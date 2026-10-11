"""Creates draft orders in Shopify (Admin GraphQL API) for the orders a run marked ready. Dry run unless asked."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

VARIANT_QUERY = """query($q: String!) {
  productVariants(first: 2, query: $q) { edges { node { id sku price product { title status } } } }
}"""

CREATE_MUTATION = """mutation($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name subtotalPriceSet { shopMoney { amount } } }
    userErrors { field message }
  }
}"""


class ShopifyError(RuntimeError):
    pass


class Shopify:
    """A small Admin API client: a custom app's access token with read_products and write_draft_orders."""

    def __init__(self, shop_domain: str, token: str, api_version: str, client: httpx.Client | None = None):
        self.url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self.client = client or httpx.Client(timeout=30.0)
        self.headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    def graphql(self, query: str, variables: dict) -> dict:
        r = self.client.post(self.url, json={"query": query, "variables": variables}, headers=self.headers)
        if r.status_code != 200:
            raise ShopifyError(f"Shopify answered {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("errors"):
            raise ShopifyError(f"Shopify error: {str(body['errors'])[:300]}")
        return body["data"]

    def variant(self, sku: str) -> dict | None:
        edges = self.graphql(VARIANT_QUERY, {"q": f'sku:"{sku}"'})["productVariants"]["edges"]
        exact = [e["node"] for e in edges if (e["node"].get("sku") or "").upper() == sku.upper()]
        return exact[0] if len(exact) == 1 else None

    def create_draft(self, draft_input: dict) -> dict:
        res = self.graphql(CREATE_MUTATION, {"input": draft_input})["draftOrderCreate"]
        if res.get("userErrors"):
            raise ShopifyError("; ".join(f"{e.get('field')}: {e.get('message')}" for e in res["userErrors"]))
        return res["draftOrder"]


@dataclass
class Plan:
    po_number: str
    file: str
    draft_input: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    expected_subtotal: float | None = None
    created: str = ""
    shopify_subtotal: float | None = None


def _address(ship: dict) -> dict:
    if not ship:
        return {}
    first, _, last = (ship.get("name") or "").partition(" ")
    out = {"firstName": first, "lastName": last, "company": ship.get("company", ""), "address1": ship["address1"],
           "address2": ship.get("address2", ""), "city": ship.get("city", ""), "provinceCode": ship.get("state", ""),
           "zip": ship.get("zip", ""), "countryCode": "US" if ship.get("country", "US") in ("US", "USA", "United States")
           else ship.get("country", "")}
    return {k: v for k, v in out.items() if v}


def plan_draft(d: dict, shop: Shopify) -> Plan:
    """Looks up each SKU in Shopify and builds the draft order. A wholesale price below the store price becomes a
    percentage discount on the line; a price above it can't be set this way, so that order is skipped."""
    p = Plan(po_number=d["po_number"], file=d["file"], expected_subtotal=d.get("expected_subtotal"))
    items = []
    for ln in d["lines"]:
        v = shop.variant(ln["sku"])
        if v is None:
            p.problems.append(f"{ln['sku']} not found in Shopify (or not unique)")
            continue
        item = {"variantId": v["id"], "quantity": ln["quantity"]}
        store = float(v["price"])
        want = ln["unit_price"]
        if want is not None and want < store - 0.005:
            pct = round(100 * (1 - want / store), 4)
            item["appliedDiscount"] = {"valueType": "PERCENTAGE", "value": pct, "title": "Wholesale price",
                                       "description": f"${want:,.2f} each"}
        elif want is not None and want > store + 0.005:
            p.problems.append(f"{ln['sku']}: wholesale ${want:,.2f} is above the store price ${store:,.2f}")
        items.append(item)
    p.draft_input = {k: v for k, v in {
        "email": d["email"], "poNumber": d["po_number"], "note": d["note"], "tags": d["tags"],
        "shippingAddress": _address(d.get("ship_to") or {}), "lineItems": items}.items() if v}
    return p


def push(drafts: list[dict], shop: Shopify, yes: bool) -> list[Plan]:
    plans = []
    for d in drafts:
        p = plan_draft(d, shop)
        if yes and not p.problems:
            created = shop.create_draft(p.draft_input)
            p.created = created["name"]
            amount = ((created.get("subtotalPriceSet") or {}).get("shopMoney") or {}).get("amount")
            p.shopify_subtotal = float(amount) if amount is not None else None
            if p.shopify_subtotal is not None and p.expected_subtotal is not None and \
                    abs(p.shopify_subtotal - p.expected_subtotal) > 0.05:
                p.problems.append(f"Shopify's subtotal ${p.shopify_subtotal:,.2f} differs from the expected "
                                  f"${p.expected_subtotal:,.2f}: check draft {p.created}")
        plans.append(p)
    return plans


def token_from_env() -> str:
    return os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
