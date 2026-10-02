"""The brokerage's settings (TOML file): margin, rounding, extra charges, weight limits and the reply signature."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

EQUIPMENT_LABELS = {"van": "53' dry van", "reefer": "Reefer", "flatbed": "Flatbed", "stepdeck": "Step deck",
                    "conestoga": "Conestoga", "power_only": "Power only", "box_truck": "Box truck"}

FIELD_LABELS = {"origin": "pickup city and state (or ZIP)", "destination": "delivery city and state (or ZIP)",
                "equipment": "equipment type", "pickup_date": "pickup date", "weight": "weight",
                "temperature": "temperature setting", "commodity": "commodity", "delivery_date": "delivery date"}

# Without these a lane can't be priced at all; the others are asked for next to the rate.
BLOCKING = ("origin", "destination", "equipment")


@dataclass
class BrokerConfig:
    name: str = "Your Brokerage"
    signature: str = ""
    target_margin: float = 0.15  # share of the customer's linehaul rate
    min_margin: float = 150.0  # dollars per load, at least
    round_to: int = 25  # round customer rates up to this many dollars
    quote_valid_days: int = 3
    lookback_days: int = 120  # lane history older than this is used only when nothing newer exists
    max_comparables: int = 5  # most recent matching loads to price from
    required: list[str] = field(default_factory=lambda: ["origin", "destination", "equipment", "pickup_date", "weight"])
    small_shipment_lbs: float = 10000  # at or under this, or under small_shipment_pallets, it's LTL-size
    small_shipment_pallets: int = 6
    spread_warning: float = 0.25  # flag when comparable loads differ by more than this share of their median
    customer_rate_warning: float = 0.10  # flag when the quote differs this much from the customer's last rate
    accessorials: dict[str, float] = field(default_factory=lambda: {
        "extra_stop": 100.0, "tarps": 125.0, "hazmat": 200.0, "team": 600.0, "liftgate": 75.0})
    max_weight_lbs: dict[str, float] = field(default_factory=lambda: {
        "van": 45000.0, "reefer": 43500.0, "flatbed": 48000.0, "stepdeck": 48000.0, "conestoga": 45000.0,
        "box_truck": 10000.0})


def load_broker(path: str | Path | None) -> BrokerConfig:
    cfg = BrokerConfig()
    if not path:
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    known = set(BrokerConfig.__dataclass_fields__)
    broker = data.get("broker") or {}
    unknown = sorted(set(broker) - known)
    if unknown:
        raise ValueError(f"{path}: unknown [broker] settings: {', '.join(unknown)}")
    for k, v in broker.items():
        setattr(cfg, k, v)
    if not 0 <= cfg.target_margin < 1:
        raise ValueError(f"{path}: target_margin is a share of the rate, e.g. 0.15 for 15%")
    bad = sorted(set(cfg.required) - set(FIELD_LABELS))
    if bad:
        raise ValueError(f"{path}: unknown required fields {bad}; choose from {sorted(FIELD_LABELS)}")
    for table in ("accessorials", "max_weight_lbs"):
        if table in data:
            getattr(cfg, table).update({k: float(v) for k, v in data[table].items()})
    return cfg
