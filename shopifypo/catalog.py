"""The merchant's catalog (Shopify's product export), wholesale price list and customers, and line matching in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentkit.checks import parse_money
from agentkit.tables import pick, read_table

from .shop import ShopConfig

FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "proton.me",
             "protonmail.com", "live.com", "msn.com", "me.com"}
UNIT_WORDS = {"ounce": "oz", "ounces": "oz", "inch": "in", "inches": "in", "blk": "black"}


def norm_sku(s: str) -> str:
    return re.sub(r"[\s\-_./]", "", (s or "").upper())


def norm_barcode(s: str) -> str:
    """UPC-A and EAN-13 of the same item compare equal: digits only, leading zeros dropped."""
    d = re.sub(r"\D", "", s or "")
    return d.lstrip("0") if 8 <= len(d) <= 14 else ""


def tokens(text: str) -> list[str]:
    """'Cedar & Sage 12oz' -> ['cedar', 'sage', '12', 'oz']: numbers split from units, unit words unified."""
    t = re.sub(r"(\d)([a-z])", r"\1 \2", (text or "").lower())
    return [UNIT_WORDS.get(w, w) for w in re.findall(r"[a-z]+|\d+(?:\.\d+)?", t) if w not in ("and", "the", "x")]


@dataclass
class Variant:
    sku: str
    handle: str
    title: str
    options: list[tuple[str, str]] = field(default_factory=list)  # (name, value); Shopify's "Default Title" dropped
    price: float | None = None  # retail price in Shopify
    barcode: str = ""
    inventory: int | None = None
    status: str = "active"
    row: int = 0

    @property
    def label(self) -> str:
        vals = " / ".join(v for _, v in self.options)
        return f"{self.title} - {vals}" if vals else self.title


def read_catalog(path: str | Path) -> tuple[list[Variant], list[str]]:
    """Variants from Shopify's product export (Products > Export). Title and options are filled down per handle."""
    out, problems = [], []
    rows = read_table(path)
    if rows and not any(k in rows[0] for k in ("variant sku", "sku")):
        raise ValueError(f"{Path(path).name}: no 'Variant SKU' column; export the catalog from Shopify (Products > "
                         "Export) or use columns SKU, Title, Price")
    last: dict[str, dict] = {}
    for i, r in enumerate(rows, 2):
        handle = pick(r, "handle") or pick(r, "title").lower()
        prev = last.setdefault(handle, {})
        for key in ("title", "option1 name", "option2 name", "option3 name", "status"):
            if r.get(key):
                prev[key] = r[key]
        sku = pick(r, "variant sku", "sku")
        price = parse_money(pick(r, "variant price", "price"))
        if not sku and price is None and not r.get("option1 value"):
            continue  # an extra image row
        options = []
        for n in (1, 2, 3):
            name, value = prev.get(f"option{n} name", ""), r.get(f"option{n} value", "")
            if value and value != "Default Title":
                options.append((name, value))
        inv = pick(r, "variant inventory qty", "inventory", "inventory qty", "on hand")
        v = Variant(sku=sku, handle=handle, title=prev.get("title", handle), options=options, price=price,
                    barcode=pick(r, "variant barcode", "barcode", "upc", "ean"),
                    inventory=int(float(inv)) if re.fullmatch(r"-?\d+(?:\.0+)?", inv or "") else None,
                    status=(prev.get("status") or "active").lower(), row=i)
        if not sku:
            problems.append(f"row {i}: {v.label} has no SKU, so it can only be matched by description")
        out.append(v)
    return out, problems


@dataclass
class PriceRow:
    sku: str
    price: float | None
    case_pack: int = 0
    min_qty: int = 0
    customer: str = ""  # empty = everyone; else a company name or email domain
    buyer_sku: str = ""


def _int(s: str) -> int:
    m = re.search(r"\d+", s or "")
    return int(m.group(0)) if m else 0


def read_prices(path: str | Path | None) -> list[PriceRow]:
    if not path:
        return []
    return [PriceRow(sku=pick(r, "sku", "variant sku", "item", "item number"),
                     price=parse_money(pick(r, "wholesale price", "price", "unit price", "wholesale")),
                     case_pack=_int(pick(r, "case pack", "pack", "case qty", "inner pack", "units per case")),
                     min_qty=_int(pick(r, "min qty", "moq", "minimum", "minimum qty")),
                     customer=pick(r, "customer", "account", "price list", "buyer").strip(),
                     buyer_sku=pick(r, "buyer sku", "customer sku", "their sku"))
            for r in read_table(path)]


@dataclass
class Customer:
    email: str
    company: str = ""
    name: str = ""
    tags: str = ""

    @property
    def domain(self) -> str:
        return self.email.split("@")[-1].lower() if "@" in self.email else ""


def read_customers(path: str | Path | None) -> list[Customer]:
    """Shopify's customer export (Customers > Export), or any CSV with Email and Company."""
    if not path:
        return []
    return [Customer(email=pick(r, "email").lower(),
                     company=pick(r, "company", "default address company", "address company", "billing company"),
                     name=" ".join(x for x in (pick(r, "first name"), pick(r, "last name")) if x), tags=pick(r, "tags"))
            for r in read_table(path) if pick(r, "email")]


def company_key(s: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in
             ("inc", "llc", "ltd", "co", "company", "corp", "the", "and")]
    return " ".join(words)


def find_customer(email: str, company: str, customers: list[Customer]) -> tuple[Customer | None, str]:
    """The Shopify customer a PO is from: same email, else same company domain, else same company name."""
    email = (email or "").lower()
    for c in customers:
        if email and c.email == email:
            return c, "email"
    domain = email.split("@")[-1] if "@" in email else ""
    if domain and domain not in FREE_MAIL:
        same = [c for c in customers if c.domain == domain]
        if len(same) == 1:
            return same[0], "email domain"
    key = company_key(company)
    if key:
        same = [c for c in customers if company_key(c.company) == key]
        if len(same) == 1:
            return same[0], "company name"
    return None, ""


@dataclass
class Match:
    variant: Variant | None
    how: str = ""  # "SKU", "buyer's SKU", "barcode", "description"
    candidates: list[Variant] = field(default_factory=list)  # when nothing (or more than one thing) fits


class Catalog:
    def __init__(self, variants: list[Variant], prices: list[PriceRow] | None = None):
        self.variants = variants
        self.prices = prices or []
        self.by_sku = {norm_sku(v.sku): v for v in variants if v.sku}
        self.by_barcode = {norm_barcode(v.barcode): v for v in variants if norm_barcode(v.barcode)}
        titles = {v.handle: set(tokens(v.title)) for v in variants}
        counts: dict[str, int] = {}
        for ts in titles.values():
            for t in ts:
                counts[t] = counts.get(t, 0) + 1
        # words in most product titles ("soy", "candle") say little about which product a line means
        self.generic = {t for t, n in counts.items() if len(titles) >= 3 and n / len(titles) >= 0.5}

    def customer_rows(self, customer: Customer | None, buyer_email: str) -> list[PriceRow]:
        keys = {company_key(customer.company), customer.domain} if customer else set()
        if "@" in (buyer_email or ""):
            keys.add(buyer_email.split("@")[-1].lower())
        keys.discard("")
        return [p for p in self.prices if p.customer and (company_key(p.customer) in keys or p.customer.lower() in keys)]

    def match(self, vendor_sku: str, buyer_sku: str, barcode: str, description: str, own_rows: list[PriceRow]) -> Match:
        if vendor_sku and norm_sku(vendor_sku) in self.by_sku:
            return Match(self.by_sku[norm_sku(vendor_sku)], "SKU")
        if buyer_sku:
            for rows in (own_rows, [p for p in self.prices if not p.customer]):
                hit = next((p for p in rows if p.buyer_sku and norm_sku(p.buyer_sku) == norm_sku(buyer_sku)), None)
                if hit and norm_sku(hit.sku) in self.by_sku:
                    return Match(self.by_sku[norm_sku(hit.sku)], "buyer's SKU")
            if norm_sku(buyer_sku) in self.by_sku:  # some buyers reuse the vendor's SKU as their item number
                return Match(self.by_sku[norm_sku(buyer_sku)], "SKU")
        for code in (barcode, buyer_sku, vendor_sku):
            if norm_barcode(code) in self.by_barcode:
                return Match(self.by_barcode[norm_barcode(code)], "barcode")
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_.]{2,}", description or ""):
            if norm_sku(word) in self.by_sku and re.search(r"\d", word):
                return Match(self.by_sku[norm_sku(word)], "SKU in description")
        return self._by_description(description)

    def _by_description(self, description: str) -> Match:
        desc = set(tokens(description))
        if not desc:
            return Match(None)
        scored = []
        for v in self.variants:
            title = set(tokens(v.title)) - self.generic or set(tokens(v.title))
            cover = len(title & desc) / len(title)
            if cover < 0.6:
                continue
            opts_ok = all(set(tokens(val)) <= desc for _, val in v.options)
            scored.append((cover, opts_ok, v))
        full = [x for x in scored if x[1]]
        if full:
            best = max(c for c, _, _ in full)
            top = [v for c, _, v in full if c == best]
            if len(top) == 1:
                return Match(top[0], "description")
            return Match(None, candidates=top)
        return Match(None, candidates=[v for _, _, v in sorted(scored, key=lambda x: -x[0])][:4])

    def expected(self, v: Variant, own_rows: list[PriceRow], cfg: ShopConfig) -> tuple[float | None, str, int, int]:
        """(expected unit price, where it came from, case pack, minimum quantity) for a variant and buyer."""
        own = next((p for p in own_rows if norm_sku(p.sku) == norm_sku(v.sku)), None)
        general = next((p for p in self.prices if not p.customer and norm_sku(p.sku) == norm_sku(v.sku)), None)
        case_pack = (own and own.case_pack) or (general and general.case_pack) or 0
        min_qty = (own and own.min_qty) or (general and general.min_qty) or 0
        if own and own.price is not None:
            return own.price, "this buyer's price list", case_pack, min_qty
        if general and general.price is not None:
            return general.price, "price list", case_pack, min_qty
        if cfg.wholesale_discount and v.price is not None:
            return (round(v.price * (1 - cfg.wholesale_discount), 2),
                    f"retail ${v.price:,.2f} less {cfg.wholesale_discount:.0%}", case_pack, min_qty)
        return None, "", case_pack, min_qty
