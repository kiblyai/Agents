"""Prices a lane from the brokerage's own load history. Code only: the model never sets a price."""

from __future__ import annotations

import csv
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .broker import BrokerConfig
from .extract import (Flag, Shipment, canonical_equipment, city_key, norm_state, norm_zip, parse_date, place_ok,
                      zip_state)

# Column names accepted in the lane-history CSV (a TMS export), first match wins
LANE_COLUMNS = {
    "date": ["date", "ship_date", "pickup_date", "load_date"],
    "customer": ["customer", "shipper", "customer_name"],
    "origin_city": ["origin_city", "pickup_city", "o_city", "from_city"],
    "origin_state": ["origin_state", "pickup_state", "o_state", "from_state"],
    "origin_zip": ["origin_zip", "pickup_zip", "o_zip", "from_zip"],
    "dest_city": ["dest_city", "destination_city", "delivery_city", "d_city", "to_city"],
    "dest_state": ["dest_state", "destination_state", "delivery_state", "d_state", "to_state"],
    "dest_zip": ["dest_zip", "destination_zip", "delivery_zip", "d_zip", "to_zip"],
    "equipment": ["equipment", "equipment_type", "trailer", "trailer_type"],
    "miles": ["miles", "distance"],
    "carrier_pay": ["carrier_pay", "carrier_rate", "carrier_cost", "buy", "buy_rate", "cost"],
    "customer_rate": ["customer_rate", "customer_pay", "sell", "sell_rate", "revenue", "rate"],
}
GENERIC_NAME_WORDS = {"inc", "llc", "co", "corp", "company", "the", "and", "of", "ltd", "group", "logistics",
                      "supply", "foods", "usa", "us"}


@dataclass
class LaneLoad:
    row: int  # line in the CSV
    day: date | None
    customer: str
    origin: dict
    destination: dict
    equipment: str
    miles: float | None
    carrier_pay: float
    customer_rate: float | None

    def lane_text(self) -> str:
        def p(x):
            return " ".join(v for v in (f"{x['city']}, {x['state']}".strip(", "), x["zip"]) if v)
        return f"{p(self.origin)} -> {p(self.destination)}"


def _money(s: str) -> float | None:
    s = (s or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def load_lanes(path: str | Path) -> tuple[list[LaneLoad], list[str]]:
    """Past loads from a CSV, and a note for every row that couldn't be used."""
    loads, problems = [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {re.sub(r"[\s.-]+", "_", (h or "").strip().lower()): h for h in reader.fieldnames or []}
        col = {k: next((headers[a] for a in names if a in headers), None) for k, names in LANE_COLUMNS.items()}
        need = [k for k in ("carrier_pay", "equipment") if not col[k]]
        if need or not (col["origin_city"] or col["origin_zip"]) or not (col["dest_city"] or col["dest_zip"]):
            raise ValueError(f"{path}: needs columns for origin, destination, equipment and carrier pay "
                             f"(e.g. {', '.join(n[0] for n in LANE_COLUMNS.values())})")
        for i, row in enumerate(reader, 2):
            def get(k):
                return (row.get(col[k]) or "").strip() if col[k] else ""

            o = {"city": get("origin_city"), "state": norm_state(get("origin_state")), "zip": norm_zip(get("origin_zip"))}
            d = {"city": get("dest_city"), "state": norm_state(get("dest_state")), "zip": norm_zip(get("dest_zip"))}
            eq = canonical_equipment(get("equipment"))
            pay = _money(get("carrier_pay"))
            if not (place_ok(o) and place_ok(d)):
                problems.append(f"row {i}: no origin or destination")
            elif not eq:
                problems.append(f"row {i}: unknown equipment '{get('equipment')}'")
            elif not pay:
                problems.append(f"row {i}: no carrier pay")
            else:
                loads.append(LaneLoad(i, parse_date(get("date")) if get("date") else None, get("customer"), o, d, eq,
                                      _money(get("miles")), pay, _money(get("customer_rate"))))
    return loads, problems


@dataclass
class Quote:
    basis: str = ""  # "same cities", "nearby ZIPs", "same states (not priced)", or "" when there's no history
    comparables: list[LaneLoad] = field(default_factory=list)
    buy: float | None = None  # median carrier pay of the comparable loads
    linehaul: float | None = None  # suggested customer linehaul rate
    extras: list[tuple[str, float]] = field(default_factory=list)  # extra charges, e.g. ("tarps", 125.0)
    total: float | None = None  # what the customer pays per truckload: the tender's rate, or linehaul + extras
    margin: float | None = None  # share of the customer's linehaul left after carrier pay

    @property
    def priced(self) -> bool:
        return self.total is not None


def _same_city(a: dict, b: dict) -> bool:
    return bool(a["city"] and b.get("city")) and city_key(a["city"]) == city_key(b["city"]) and a["state"] == b.get("state")


def _same_zip3(a: dict, b: dict) -> bool:
    return len(a["zip"]) == 5 and len(b.get("zip", "")) == 5 and a["zip"][:3] == b["zip"][:3]


def _state(p: dict) -> str:
    return p.get("state") or zip_state(p.get("zip", ""))


def same_customer(a: str, b: str) -> bool:
    def toks(s):
        return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in GENERIC_NAME_WORDS and len(w) > 1}
    return bool(toks(a) & toks(b))


def round_up(x: float, step: int) -> float:
    return math.ceil(x / step - 1e-9) * step if step > 0 else round(x, 2)


def price(s: Shipment, loads: list[LaneLoad], cfg: BrokerConfig, asof: date) -> Quote:
    """Suggested rate for one lane from comparable past loads; adds its own flags to the shipment."""
    q = Quote()
    if not (place_ok(s.origin) and place_ok(s.destination) and s.equipment):
        return q  # the reply asks for what's missing
    if any(f.code == "small_shipment" for f in s.flags):
        return q
    pool = [x for x in loads if x.equipment == s.equipment and (x.day is None or x.day <= asof)]
    recent = [x for x in pool if x.day is None or (asof - x.day).days <= cfg.lookback_days]
    tiers = [
        ("same cities", lambda x: _same_city(x.origin, s.origin) and _same_city(x.destination, s.destination)),
        ("nearby ZIPs", lambda x: _same_zip3(x.origin, s.origin) and _same_zip3(x.destination, s.destination)),
        ("same states (not priced)", lambda x: _state(x.origin) == _state(s.origin) != ""
         and _state(x.destination) == _state(s.destination) != ""),
    ]
    found, stale = [], False
    for basis, match in tiers:
        found = [x for x in recent if match(x)]
        if not found:
            found = [x for x in pool if match(x)]
            stale = bool(found)
        if found:
            q.basis = basis
            break
    label = s.equipment.replace("_", " ")
    if not found:
        s.flags.append(Flag("no_history", f"no past {label} loads on this lane or between these states: price by hand"))
        return q
    found.sort(key=lambda x: (x.day or date.min, x.row), reverse=True)
    q.comparables = found[:cfg.max_comparables]
    pays = [x.carrier_pay for x in q.comparables]
    if q.basis.startswith("same states"):
        s.flags.append(Flag("state_history_only", f"no past loads on this lane, only {_state(s.origin)} to "
                                                  f"{_state(s.destination)} ({len(pays)} loads, carrier pay "
                                                  f"${min(pays):,.0f}-${max(pays):,.0f}): price by hand"))
        return q

    q.buy = statistics.median(pays)
    q.linehaul = round_up(max(q.buy / (1 - cfg.target_margin), q.buy + cfg.min_margin), cfg.round_to)
    for key in s.charged:
        if key in cfg.accessorials:
            n = len(s.stops) if key == "extra_stop" else 1
            q.extras.append((key, cfg.accessorials[key] * n))
    q.total = q.linehaul + sum(v for _, v in q.extras)
    q.margin = (q.linehaul - q.buy) / q.linehaul

    if q.basis == "nearby ZIPs":
        near = q.comparables[0]
        s.flags.append(Flag("nearby_zips", f"priced from loads between nearby ZIP areas ({near.lane_text()})"))
    if stale:
        newest = q.comparables[0].day
        s.flags.append(Flag("stale_history", f"newest matching load is from {newest}, over {cfg.lookback_days} days ago"))
    if len(pays) < 2:
        s.flags.append(Flag("few_loads", "priced from only 1 past load"))
    elif (max(pays) - min(pays)) / q.buy > cfg.spread_warning:
        s.flags.append(Flag("wide_spread", f"past carrier pay varies widely (${min(pays):,.0f}-${max(pays):,.0f})"))
    last = next((x for x in q.comparables if x.customer_rate and s.customer and same_customer(x.customer, s.customer)), None)
    if last and abs(q.linehaul - last.customer_rate) / last.customer_rate > cfg.customer_rate_warning:
        s.flags.append(Flag("customer_rate", f"this customer paid ${last.customer_rate:,.0f} on {last.day} for this "
                                             f"lane; the suggested linehaul is ${q.linehaul:,.0f}"))
    if s.rate is not None:
        if s.kind == "tender":
            q.total = s.rate  # the customer set the rate
            q.margin = (s.rate - q.buy) / s.rate
        if s.rate - q.buy < cfg.min_margin:
            s.flags.append(Flag("low_margin", f"the customer's rate ${s.rate:,.0f} leaves ${s.rate - q.buy:,.0f} over "
                                              f"recent carrier pay of ${q.buy:,.0f}"))
    elif s.kind == "tender":
        s.flags.append(Flag("tender_rate", "tender without a rate: agree the rate before accepting"))
    return q
