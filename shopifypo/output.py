"""Writes the review list, a workbook of orders and lines, draft-order files and the PO log."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from agentkit.tables import Sheet, write_csv, write_xlsx

from .check import Order

REVIEW_COLUMNS = ["order", "email", "po_number", "buyer", "line", "sku", "must_fix", "check"]
LOG_COLUMNS = ["key", "po_number", "buyer", "file", "date", "status"]


def status(o: Order) -> str:
    if o.error:
        return o.error
    if o.kind == "other":
        return "not a PO"
    return "ready" if o.ready else "to fix"


def buyer(o: Order) -> str:
    return (o.customer.company if o.customer and o.customer.company else o.buyer_company) or o.buyer_email


def review_rows(orders: list[Order]) -> list[dict]:
    rows = []
    for o in orders:
        base = {"email": o.email.file, "po_number": o.po_number, "buyer": buyer(o)}
        if o.error:
            rows.append({**base, "order": 1, "line": "", "sku": "", "must_fix": "yes", "check": o.error})
            continue
        for f in o.flags:
            rows.append({**base, "order": 1 if f.hold else 2 if f.code != "not_po" else 3, "line": "", "sku": "",
                         "must_fix": "yes" if f.hold else "", "check": f.text})
        for ln in o.lines:
            for f in ln.flags:
                rows.append({**base, "order": 1 if f.hold else 2, "line": ln.n, "sku": ln.sku,
                             "must_fix": "yes" if f.hold else "", "check": f.text})
    rows.sort(key=lambda r: r["order"])
    return rows


def draft_order(o: Order, tags: list[str]) -> dict:
    """What `push` sends to Shopify for a ready order (SKUs are looked up there)."""
    return {"file": o.email.file, "po_number": o.po_number, "email": o.customer.email if o.customer else o.buyer_email,
            "company": buyer(o), "ship_to": o.ship_to if o.ship_to.get("address1") else {},
            "requested_ship_date": o.requested_ship_date, "payment_terms": o.payment_terms, "tags": tags,
            "note": f"PO {o.po_number} from {o.email.file}" + (f"; ship by {o.requested_ship_date}"
                                                              if o.requested_ship_date else "")
                    + (f"; terms {o.payment_terms}" if o.payment_terms else ""),
            "lines": [{"sku": ln.sku, "title": ln.title, "quantity": ln.units, "unit_price": ln.unit_price}
                      for ln in o.lines],
            "expected_subtotal": o.expected_subtotal}


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "order"


def _as_dict(o: Order) -> dict:
    return {"file": o.email.file, "kind": o.kind, "po_number": o.po_number, "buyer_company": o.buyer_company,
            "buyer_email": o.buyer_email, "customer": o.customer.email if o.customer else "",
            "customer_how": o.customer_how, "ship_to": o.ship_to, "requested_ship_date": o.requested_ship_date,
            "payment_terms": o.payment_terms, "po_subtotal": o.po_subtotal, "expected_subtotal": o.expected_subtotal,
            "ready": o.ready, "status": status(o), "error": o.error,
            "flags": [asdict(f) for f in o.flags],
            "lines": [asdict(ln) for ln in o.lines]}


def write_outputs(out: Path, orders: list[Order], tags: list[str], seen: dict[str, str]) -> dict:
    review = review_rows(orders)
    write_csv(out / "review.csv", review, REVIEW_COLUMNS)
    drafts = out / "draft_orders"
    drafts.mkdir(parents=True, exist_ok=True)
    for old in drafts.glob("*.json"):
        old.unlink()
    ready = [o for o in orders if o.ready]
    for o in ready:
        (drafts / f"{_slug(o.po_number or o.email.file)}.json").write_text(json.dumps(draft_order(o, tags), indent=1))
    (out / "orders.json").write_text(json.dumps({"orders": [_as_dict(o) for o in orders],
                                                 "drafts": [draft_order(o, tags) for o in ready]}, indent=1))
    log = [{"key": k, "po_number": k.split("|")[-1], "buyer": k.split("|")[0], "file": v.split(", ")[0],
            "date": v.split(", ")[-1], "status": "entered"} for k, v in seen.items()]
    write_csv(out / "po_log.csv", log, LOG_COLUMNS)

    def checks(flags) -> str:
        return "\n".join(("FIX: " if f.hold else "") + f.text for f in flags)

    o_rows, o_yellow = [], set()
    for i, o in enumerate(orders):
        o_rows.append([o.email.file, o.po_number, buyer(o), o.customer.email if o.customer else "", status(o),
                       len(o.lines), o.po_subtotal, o.expected_subtotal, o.requested_ship_date, o.payment_terms,
                       ", ".join(x for x in (o.ship_to.get("city"), o.ship_to.get("state")) if x), checks(o.flags)])
        if not o.ready and o.kind != "other":
            o_yellow.add(i)
    l_rows, l_yellow = [], set()
    for o in orders:
        for ln in o.lines:
            l_rows.append([o.email.file, o.po_number, ln.n, ln.description, ln.sku, ln.title, ln.how, ln.quantity,
                           ln.unit, ln.units, ln.po_price, ln.expected_price, ln.price_source, ln.unit_price,
                           (ln.unit_price * ln.units) if ln.unit_price is not None and ln.units is not None else None,
                           checks(ln.flags)])
            if any(f.hold for f in ln.flags):
                l_yellow.add(len(l_rows) - 1)
    money = '"$"#,##0.00'
    write_xlsx(out / "orders.xlsx", [
        Sheet("Orders", ["Email", "PO", "Buyer", "Shopify customer", "Status", "Lines", "PO subtotal",
                         "Our subtotal", "Ship by", "Terms", "Ship to", "Checks"], o_rows, o_yellow,
              {1: 26, 3: 24, 4: 28, 12: 60}, {7: money, 8: money}),
        Sheet("Lines", ["Email", "PO", "Line", "As written", "SKU", "Product", "Matched by", "Qty", "Unit", "Units",
                        "PO price", "Expected price", "Price from", "Draft price", "Draft line total", "Checks"],
              l_rows, l_yellow, {1: 26, 4: 32, 6: 32, 13: 22, 16: 60}, {11: money, 12: money, 14: money, 15: money}),
        Sheet("Review", [c.replace("_", " ").capitalize() for c in REVIEW_COLUMNS],
              [[r[c] for c in REVIEW_COLUMNS] for r in review], set(), {2: 26, 4: 24, 8: 80}),
    ])
    return {"ready": len(ready), "review_items": len(review)}


def summary(orders: list[Order], usage, runner, catalog_size: int, catalog_problems: list[str]) -> dict:
    return {
        "emails": len(orders), "purchase_orders": sum(1 for o in orders if o.kind == "purchase_order"),
        "ready": sum(1 for o in orders if o.ready), "to_fix": sum(1 for o in orders if not o.ready and o.kind != "other"),
        "not_po": sum(1 for o in orders if o.kind == "other"), "not_processed": sum(1 for o in orders if o.error),
        "lines": sum(len(o.lines) for o in orders), "catalog_variants": catalog_size,
        "catalog_problems": catalog_problems, "stopped_reason": runner.stopped_reason,
        "llm_requests": usage.requests, "llm_retries": usage.retries, "llm_repairs": usage.repairs,
        "llm_failed_attempts": usage.failed_attempts, "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens, "cost_usd": round(usage.cost_usd, 6),
        "models_used": usage.models, "from_cache": runner.from_cache,
    }
