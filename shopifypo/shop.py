"""The merchant's settings (TOML file): wholesale pricing, tolerances and the tags put on draft orders."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ShopConfig:
    name: str = "Your Shop"
    currency: str = "USD"
    wholesale_discount: float = 0.0  # wholesale = retail x (1 - this) for SKUs missing from the price list; 0 = off
    price_tolerance: float = 0.01  # flag PO prices that differ from the expected price by more than this share
    require_po_number: bool = True
    min_ship_days: int = 2  # flag requested ship dates sooner than this many days after the email
    tags: list[str] = field(default_factory=lambda: ["wholesale", "po-agent"])
    shop_domain: str = ""  # your-store.myshopify.com, for `push`
    api_version: str = "2025-07"


def load_shop(path: str | Path | None) -> ShopConfig:
    cfg = ShopConfig()
    if not path:
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    shop = data.get("shop") or {}
    unknown = sorted(set(shop) - set(ShopConfig.__dataclass_fields__))
    if unknown:
        raise ValueError(f"{path}: unknown [shop] settings: {', '.join(unknown)}")
    for k, v in shop.items():
        setattr(cfg, k, v)
    if not 0 <= cfg.wholesale_discount < 1:
        raise ValueError(f"{path}: wholesale_discount is a share of the retail price, e.g. 0.5 for 50% off")
    return cfg
