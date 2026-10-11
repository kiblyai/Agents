"""Checks each PO in code: lines matched to the catalog, numbers found in the PO, prices, case packs and totals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from agentkit.checks import find_dates, has_amount, parse_date, relative_dates
from agentkit.inbox import Email
from agentkit.places import norm_state, norm_zip, zip_state

from .catalog import Catalog, Customer, find_customer, norm_barcode, norm_sku
from .extract import POLine, POReading
from .shop import ShopConfig

CASE_WORDS = {"case", "cases", "cs", "ctn", "carton", "cartons", "box", "boxes"}


@dataclass
class Flag:
    code: str
    hold: bool  # True: a person must act before the draft order goes in
    text: str


@dataclass
class OrderLine:
    n: int
    description: str = ""
    vendor_sku: str = ""
    buyer_sku: str = ""
    barcode: str = ""
    sku: str = ""  # the catalog SKU it was matched to
    title: str = ""
    how: str = ""
    quantity: float | None = None  # as ordered
    unit: str = ""
    units: int | None = None  # in the catalog's units (cases converted)
    po_price: float | None = None  # the PO's price per catalog unit
    expected_price: float | None = None
    price_source: str = ""
    unit_price: float | None = None  # the price the draft order uses
    line_total: float | None = None  # as written on the PO
    candidates: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)


@dataclass
class Order:
    email: Email
    kind: str = ""
    po_number: str = ""
    buyer_company: str = ""
    buyer_name: str = ""
    buyer_email: str = ""
    customer: Customer | None = None
    customer_how: str = ""
    ship_to: dict = field(default_factory=dict)
    requested_ship_date: str = ""
    payment_terms: str = ""
    notes: str = ""
    lines: list[OrderLine] = field(default_factory=list)
    po_subtotal: float | None = None
    shipping: float | None = None
    tax: float | None = None
    po_total: float | None = None
    expected_subtotal: float | None = None
    flags: list[Flag] = field(default_factory=list)
    error: str = ""

    def all_flags(self) -> list[Flag]:
        return self.flags + [f for ln in self.lines for f in ln.flags]

    @property
    def ready(self) -> bool:
        return (not self.error and self.kind == "purchase_order" and bool(self.lines)
                and not any(f.hold for f in self.all_flags()))

    @property
    def key(self) -> str:
        who = self.customer.email if self.customer else (self.buyer_email or self.email.sender).split("@")[-1]
        return f"{who.lower()}|{norm_sku(self.po_number)}"


def _in_text(value: str, text: str) -> bool:
    v = norm_sku(value)
    return bool(v) and v in norm_sku(text)


def _barcode_in(value: str, text: str) -> bool:
    b = norm_barcode(value)
    return bool(b) and any(norm_barcode(run) == b for run in re.findall(r"\d{8,14}", text))


def _money(x: float | None) -> str:
    return "" if x is None else f"${x:,.2f}"


def check_line(n: int, x: POLine, text: str, catalog: Catalog, own_rows, cfg: ShopConfig) -> OrderLine:
    ln = OrderLine(n=n, description=x.description, quantity=x.quantity, unit=x.unit.lower())
    flags = ln.flags
    for attr, check in (("vendor_sku", _in_text), ("buyer_sku", _in_text), ("barcode", _barcode_in)):
        value = getattr(x, attr)
        if value and not check(value, text):
            flags.append(Flag("sku_not_in_text", False, f"{attr.replace('_', ' ')} '{value}' is not in the PO; ignored"))
            value = ""
        setattr(ln, attr, value)
    if x.quantity is None or not has_amount(x.quantity, text):
        flags.append(Flag("qty_not_in_text", True, f"quantity {x.quantity if x.quantity is not None else '(none)'} "
                                                   "is not in the PO"))
    po_price = x.unit_price
    if po_price is not None and not has_amount(po_price, text):
        flags.append(Flag("price_not_in_text", False, f"price {_money(po_price)} is not in the PO; ignored"))
        po_price = None
    line_total = x.line_total
    if line_total is not None and not has_amount(line_total, text):
        flags.append(Flag("line_total_not_in_text", False, f"line total {_money(line_total)} is not in the PO; ignored"))
        line_total = None
    ln.line_total = line_total

    m = catalog.match(ln.vendor_sku, ln.buyer_sku, ln.barcode, x.description, own_rows)
    if m.variant is None:
        ln.candidates = [f"{v.sku} {v.label}" for v in m.candidates]
        if len(m.candidates) > 1:
            flags.append(Flag("ambiguous", True, f"'{x.description or ln.vendor_sku}' could be "
                                                 + " or ".join(ln.candidates) + ": pick one"))
        else:
            flags.append(Flag("unmatched", True, f"'{x.description or ln.vendor_sku or ln.buyer_sku}' is not in the "
                                                 "catalog" + (f" (closest: {ln.candidates[0]})" if ln.candidates else "")))
        return ln
    v = m.variant
    ln.sku, ln.title, ln.how = v.sku, v.label, m.how
    if m.how == "description":
        flags.append(Flag("by_description", False, f"matched '{x.description}' to {v.sku} {v.label} by description"))
    if v.status != "active":
        flags.append(Flag("archived", True, f"{v.sku} is {v.status} in Shopify"))
    expected, source, case_pack, min_qty = catalog.expected(v, own_rows, cfg)
    ln.expected_price, ln.price_source = expected, source

    qty = x.quantity
    if qty is not None and qty != int(qty):
        flags.append(Flag("not_integer", True, f"quantity {qty:g} is not a whole number"))
    if qty is not None and ln.unit in CASE_WORDS:
        if case_pack:
            ln.units = int(qty) * case_pack
            if po_price is not None:
                po_price = round(po_price / case_pack, 4)
            flags.append(Flag("case_converted", False, f"{qty:g} {ln.unit} x {case_pack} = {ln.units} units"))
        else:
            flags.append(Flag("case_pack_unknown", True, f"ordered in {ln.unit} but the case pack of {v.sku} is unknown"))
    elif qty is not None:
        ln.units = int(qty)
    ln.po_price = po_price

    # price
    if po_price is not None and expected is not None:
        if abs(po_price - expected) > max(0.005, cfg.price_tolerance * expected):
            flags.append(Flag("price_mismatch", True, f"PO price {_money(po_price)} vs {_money(expected)} ({source})"))
        ln.unit_price = expected
    elif expected is not None:
        ln.unit_price = expected
        flags.append(Flag("no_po_price", False, f"no price on the PO line; using {_money(expected)} ({source})"))
    elif po_price is not None:
        ln.unit_price = po_price
        flags.append(Flag("no_list_price", False, f"{v.sku} has no wholesale price set; using the PO's {_money(po_price)}"))
    else:
        flags.append(Flag("no_price", True, f"no price for {v.sku}: not on the PO and no wholesale price set"))

    # the PO's own arithmetic
    if x.unit_price is not None and qty is not None and line_total is not None and po_price is not None:
        if abs(qty * x.unit_price - line_total) > 0.01:
            flags.append(Flag("line_math", True, f"line total {_money(line_total)} is not {qty:g} x "
                                                 f"{_money(x.unit_price)} = {_money(qty * x.unit_price)}"))
    if ln.units is not None and ln.unit not in CASE_WORDS:
        if case_pack and ln.units % case_pack:
            flags.append(Flag("case_pack", True, f"{ln.units} is not a multiple of the case pack ({case_pack})"))
        if min_qty and ln.units < min_qty:
            flags.append(Flag("below_min", True, f"{ln.units} is below the minimum of {min_qty}"))
    if ln.units is not None and v.inventory is not None and ln.units > v.inventory:
        flags.append(Flag("short_stock", False, f"only {v.inventory} of {v.sku} in stock for {ln.units} ordered"))
    return ln


def _check_ship_to(r: POReading, text: str, order: Order) -> None:
    a = r.ship_to
    state, z = norm_state(a.state), norm_zip(a.zip)
    order.ship_to = {"name": a.name, "company": a.company, "address1": a.address1, "address2": a.address2,
                     "city": a.city, "state": state or a.state, "zip": z or a.zip, "country": a.country or "US"}
    if not (a.address1 and a.city):
        order.flags.append(Flag("ship_to_missing", False, "no ship-to address on the PO: the draft uses the customer's "
                                                          "default address"))
        return
    low = re.sub(r"\s+", " ", text.lower())
    missing = [f"{k} '{v}'" for k, v in (("street", a.address1), ("city", a.city), ("ZIP", z)) if v and
               not all(w in low for w in re.findall(r"[a-z0-9]+", v.lower()))]
    if missing:
        order.flags.append(Flag("ship_to_check", True, "ship-to " + ", ".join(missing) + " is not in the PO"))
    zs = zip_state(z)
    if state and zs and zs != state:
        order.flags.append(Flag("zip_state", True, f"ship-to ZIP {z} is in {zs}, not {state}"))


def check_po(r: POReading, e: Email, catalog: Catalog, customers: list[Customer], cfg: ShopConfig,
             seen: dict[str, str]) -> Order:
    text = e.all_text()
    sent = e.day or date.today()
    o = Order(email=e, kind=r.kind, po_number=r.po_number.strip(), buyer_company=r.buyer_company,
              buyer_name=r.buyer_name, payment_terms=r.payment_terms, notes=r.notes, po_subtotal=r.subtotal,
              shipping=r.shipping, tax=r.tax, po_total=r.total)
    flags = o.flags
    if r.kind == "other":
        flags.append(Flag("not_po", False, "not a purchase order: nothing entered"))
        return o
    if r.kind == "reorder" and not r.lines:
        flags.append(Flag("reorder", True, "asks to repeat an earlier order without listing the items: find their last "
                                           "order and confirm it with them"))
    if r.kind == "change":
        flags.append(Flag("change", True, "changes or cancels an order already placed: update it by hand"))

    o.buyer_email = r.buyer_email.lower() if r.buyer_email and r.buyer_email.lower() in text.lower() else e.sender.lower()
    o.customer, o.customer_how = find_customer(o.buyer_email, r.buyer_company, customers)
    if o.customer is None:
        flags.append(Flag("new_customer", True, f"new wholesale buyer ({r.buyer_company or o.buyer_email}): set up the "
                                                "customer and their terms in Shopify first"))
    if o.po_number and not _in_text(o.po_number, text):
        flags.append(Flag("po_number_not_in_text", True, f"PO number '{o.po_number}' is not in the email"))
        o.po_number = ""
    if not o.po_number and cfg.require_po_number and r.kind == "purchase_order":
        flags.append(Flag("po_number_missing", True, "no PO number: ask the buyer for one"))
    if o.po_number:
        prior = seen.get(o.key)
        if prior:
            flags.append(Flag("duplicate", True, f"PO {o.po_number} was already received ({prior}): don't enter it twice"))

    _check_ship_to(r, text, o)
    if r.requested_ship_date:
        d = parse_date(r.requested_ship_date, sent)
        if d is None or d not in find_dates(text, sent) | relative_dates(text, sent):
            flags.append(Flag("ship_date_not_in_text", False, f"ship date {r.requested_ship_date} is not in the PO; "
                                                              "ignored"))
        else:
            o.requested_ship_date = d.isoformat()
            if d < sent + timedelta(days=cfg.min_ship_days):
                flags.append(Flag("ship_date_soon", False, f"wants it shipped by {d.isoformat()}, "
                                                           f"{(d - sent).days} days after the PO"))

    own_rows = catalog.customer_rows(o.customer, o.buyer_email)
    o.lines = [check_line(i, x, text, catalog, own_rows, cfg) for i, x in enumerate(r.lines, 1)]
    if r.kind == "purchase_order" and not o.lines:
        flags.append(Flag("no_lines", True, "no items found in the PO"))

    # totals
    if o.po_subtotal is not None and not has_amount(o.po_subtotal, text):
        flags.append(Flag("subtotal_not_in_text", False, f"subtotal {_money(o.po_subtotal)} is not in the PO; ignored"))
        o.po_subtotal = None
    if o.po_subtotal is not None and o.lines:
        if all(x.line_total is not None for x in r.lines):
            po_sum = sum(x.line_total for x in r.lines)
        elif all(x.unit_price is not None and x.quantity is not None for x in r.lines):
            po_sum = sum(x.unit_price * x.quantity for x in r.lines)
        else:
            po_sum = None
        if po_sum is not None and abs(po_sum - o.po_subtotal) > 0.01:
            flags.append(Flag("subtotal_mismatch", True, f"PO subtotal {_money(o.po_subtotal)} is not the sum of its "
                                                         f"lines ({_money(po_sum)})"))
    if o.po_subtotal is not None and o.po_total is not None and has_amount(o.po_total, text):
        calc = o.po_subtotal + (o.shipping or 0) + (o.tax or 0)
        if abs(calc - o.po_total) > 0.01:
            flags.append(Flag("total_mismatch", False, f"PO total {_money(o.po_total)} is not subtotal + shipping + tax "
                                                       f"({_money(calc)})"))
    priced = [ln for ln in o.lines if ln.unit_price is not None and ln.units is not None]
    o.expected_subtotal = round(sum(ln.unit_price * ln.units for ln in priced), 2) if priced else None
    return o
