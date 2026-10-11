"""The transaction coordinator's settings (TOML file): how days count when a contract doesn't say, and reminders."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class TCConfig:
    name: str = "Transaction coordination"
    coordinator: str = ""
    email: str = ""
    phone: str = ""
    days_mean: str = "calendar"  # what a bare "days" means when the contract doesn't define it
    roll_to_business_day: bool = True  # when the contract doesn't say: weekend/holiday deadlines move to Monday
    federal_holidays: bool = True
    extra_holidays: list[date] = field(default_factory=list)
    remind_days: list[int] = field(default_factory=lambda: [3, 1])  # reminder emails this many days before
    soon_days: int = 5  # deadlines this close are marked "soon"

    def signature(self) -> str:
        return "\n".join(x for x in (self.coordinator, self.name, self.email, self.phone) if x)


def load_tc(path: str | Path | None) -> TCConfig:
    cfg = TCConfig()
    if not path:
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tc = data.get("tc") or {}
    unknown = sorted(set(tc) - set(TCConfig.__dataclass_fields__))
    if unknown:
        raise ValueError(f"{path}: unknown [tc] settings: {', '.join(unknown)}")
    for k, v in tc.items():
        setattr(cfg, k, v)
    if cfg.days_mean not in ("calendar", "business"):
        raise ValueError(f"{path}: days_mean must be \"calendar\" or \"business\"")
    cfg.extra_holidays = [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in cfg.extra_holidays]
    return cfg
