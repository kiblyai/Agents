"""Reads each purchase-order email with the model into header and lines (the checks happen in check.py)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from agentkit.inbox import MAX_PART, Email

SYSTEM = """You read purchase orders that wholesale buyers email to {shop}, a brand that sells on Shopify, and turn each one into order data.

Rules:
- kind: "purchase_order" for a new order that lists items; "reorder" when they ask to repeat an earlier order without listing the items; "change" when they change or cancel an order already placed; "other" for anything else (questions, invoices, marketing).
- One line per item ordered, from the email body or the attached PO (PDF, Excel, CSV or Word). Copy SKUs, item numbers, UPC/EAN barcodes, descriptions, quantities, units and prices exactly as written.
- vendor_sku is the seller's SKU when the PO shows it; buyer_sku is the buyer's own item number; barcode is a UPC or EAN.
- quantity is a number; unit is "each" or "case" as written ("" if not stated). unit_price and line_total are numbers, or null when the PO doesn't show them.
- Never guess a SKU, price, quantity, date or address the PO doesn't show: use "" or null.
- po_number, dates (YYYY-MM-DD), payment terms and the ship-to address as written. Work out dates like "next Friday" from the date the email was sent.
- subtotal, shipping, tax and total are numbers when the PO shows them, else null.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"kind": "<purchase_order|reorder|change|other>", "po_number": "<...>", "buyer_company": "<...>", "buyer_name": "<...>", "buyer_email": "<...>", "ship_to": {"name": "<...>", "company": "<...>", "address1": "<...>", "address2": "<...>", "city": "<...>", "state": "<XX>", "zip": "<...>", "country": "<...>"}, "requested_ship_date": "<YYYY-MM-DD or ''>", "payment_terms": "<...>", "lines": [{"vendor_sku": "<...>", "buyer_sku": "<...>", "barcode": "<...>", "description": "<...>", "quantity": <number>, "unit": "<each|case|''>", "unit_price": <number or null>, "line_total": <number or null>}], "subtotal": <number or null>, "shipping": <number or null>, "tax": <number or null>, "total": <number or null>, "notes": "<...>"}"""


def system_prompt(shop: str) -> str:
    return SYSTEM.format(shop=shop)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_PART else text[:MAX_PART] + "\n[... cut ...]"


def build_prompt(e: Email) -> str:
    sent = e.sent.strftime("%A %Y-%m-%d %H:%M") if e.sent else "unknown"
    head = [f"Email file: {e.file}", f"Sent: {sent}", f"From: {e.sender_name} <{e.sender}>".replace(" <>", ""),
            f"Subject: {e.subject}"]
    blocks = ["\n".join(head), "### Newest message\n" + _clip(e.latest or "(empty)")]
    if e.earlier:
        blocks.append("### Earlier messages in the thread\n" + _clip(e.earlier))
    blocks += [f"### Attachment: {a.name}\n{_clip(a.text)}" for a in e.attachments]
    return "\n\n".join(blocks) + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    return str(v).strip()


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


class Address(BaseModel):
    name: str = ""
    company: str = ""
    address1: str = ""
    address2: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    country: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


class POLine(BaseModel):
    vendor_sku: str = ""
    buyer_sku: str = ""
    barcode: str = ""
    description: str = ""
    quantity: float | None = None
    unit: str = ""
    unit_price: float | None = None
    line_total: float | None = None

    @field_validator("vendor_sku", "buyer_sku", "barcode", "description", "unit", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def _n(cls, v):
        return _num(v)


class POReading(BaseModel):
    kind: str = "other"
    po_number: str = ""
    buyer_company: str = ""
    buyer_name: str = ""
    buyer_email: str = ""
    ship_to: Address = Field(default_factory=Address)
    requested_ship_date: str = ""
    payment_terms: str = ""
    lines: list[POLine] = Field(default_factory=list)
    subtotal: float | None = None
    shipping: float | None = None
    tax: float | None = None
    total: float | None = None
    notes: str = ""

    @field_validator("po_number", "buyer_company", "buyer_name", "buyer_email", "requested_ship_date",
                     "payment_terms", "notes", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = re.sub(r"[\s-]+", "_", _str(v).lower())
        v = {"po": "purchase_order", "order": "purchase_order", "new_order": "purchase_order",
             "repeat": "reorder", "cancel": "change", "cancellation": "change"}.get(v, v)
        return v if v in ("purchase_order", "reorder", "change", "other") else "other"

    @field_validator("ship_to", mode="before")
    @classmethod
    def _addr(cls, v):
        return v if isinstance(v, dict) else {"address1": _str(v)}

    @field_validator("lines", mode="before")
    @classmethod
    def _lines(cls, v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    @field_validator("subtotal", "shipping", "tax", "total", mode="before")
    @classmethod
    def _n(cls, v):
        return _num(v)
