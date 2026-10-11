"""Cleanup settings (TOML file): what counts as stale, formats to standardize, and allowed industry labels."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# A common subset of HubSpot's industry labels; set `industries` in the settings file to your portal's own list.
INDUSTRIES = [
    "Accounting", "Architecture & Planning", "Automotive", "Banking", "Biotechnology", "Building Materials",
    "Civil Engineering", "Computer Software", "Construction", "Consumer Goods", "Consumer Services", "Design",
    "E-Learning", "Education Management", "Electrical/Electronic Manufacturing", "Environmental Services",
    "Events Services", "Financial Services", "Food & Beverages", "Food Production", "Health, Wellness and Fitness",
    "Hospital & Health Care", "Hospitality", "Human Resources", "Industrial Automation",
    "Information Technology and Services", "Insurance", "Internet", "Law Practice", "Legal Services",
    "Logistics and Supply Chain", "Machinery", "Management Consulting", "Manufacturing", "Marketing and Advertising",
    "Media Production", "Medical Devices", "Medical Practice", "Mechanical or Industrial Engineering",
    "Nonprofit Organization Management", "Real Estate", "Renewables & Environment", "Restaurants", "Retail",
    "Staffing and Recruiting", "Telecommunications", "Transportation/Trucking/Railroad", "Warehousing", "Wholesale",
]
ROLE_PREFIXES = ["info", "sales", "support", "admin", "hello", "contact", "office", "billing", "team", "marketing",
                 "accounts", "orders", "help", "noreply", "no-reply"]


@dataclass
class CRMConfig:
    portal: str = "HubSpot portal"
    stale_months: int = 18  # leads with no activity for this long are flagged
    state_format: str = "name"  # "name" (California) or "code" (CA)
    country_name: str = "United States"
    role_prefixes: list[str] = field(default_factory=lambda: list(ROLE_PREFIXES))
    extra_free_domains: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=lambda: list(INDUSTRIES))


def load_crm(path: str | Path | None) -> CRMConfig:
    cfg = CRMConfig()
    if not path:
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    crm = data.get("crm") or {}
    unknown = sorted(set(crm) - set(CRMConfig.__dataclass_fields__))
    if unknown:
        raise ValueError(f"{path}: unknown [crm] settings: {', '.join(unknown)}")
    for k, v in crm.items():
        setattr(cfg, k, v)
    if cfg.state_format not in ("name", "code"):
        raise ValueError(f"{path}: state_format must be \"name\" or \"code\"")
    return cfg
