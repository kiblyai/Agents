"""The contractor's profile (TOML file): NAICS codes, certifications, capabilities, where it works and its clearance."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agentkit.places import norm_state

CERTS = {"SB", "8A", "HZ", "SDVOSB", "VOSB", "WOSB", "EDWOSB", "ISBEE"}
CLEARANCES = ["none", "public trust", "secret", "top secret", "ts/sci"]


@dataclass
class Profile:
    name: str = ""
    naics: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)  # e.g. ["SB", "SDVOSB"]
    capabilities: list[str] = field(default_factory=list)
    past_performance: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)  # state codes the company can work in; empty or "remote" = anywhere
    clearance: str = "none"  # the company's facility / staff clearance level
    min_days: int = 7  # skip notices due sooner than this
    notice_types: list[str] = field(default_factory=lambda: ["solicitation", "combined synopsis/solicitation",
                                                             "sources sought", "presolicitation"])
    exclude_words: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        return "\n".join([f"Company: {self.name}", f"NAICS: {', '.join(self.naics)}",
                          f"Certifications: {', '.join(self.certifications) or 'none'}",
                          f"Capabilities: {'; '.join(self.capabilities)}",
                          "Past performance: " + ("; ".join(self.past_performance) or "none listed"),
                          f"Works in: {', '.join(self.places) or 'anywhere'}", f"Clearance: {self.clearance}"])


def load_profile(path: str | Path) -> Profile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    p = Profile()
    info = data.get("company") or {}
    unknown = sorted(set(info) - set(Profile.__dataclass_fields__))
    if unknown:
        raise ValueError(f"{path}: unknown [company] settings: {', '.join(unknown)}")
    for k, v in info.items():
        setattr(p, k, v)
    p.naics = [str(n).strip() for n in p.naics]
    p.certifications = [c.upper().replace("(", "").replace(")", "").replace(" ", "") for c in p.certifications]
    bad = sorted(set(p.certifications) - CERTS)
    if bad:
        raise ValueError(f"{path}: unknown certifications {bad}; use {sorted(CERTS)}")
    p.places = [x if x.lower() == "remote" else norm_state(x) or x for x in p.places]
    p.clearance = p.clearance.lower()
    if p.clearance not in CLEARANCES:
        raise ValueError(f"{path}: clearance must be one of {CLEARANCES}")
    p.notice_types = [t.lower() for t in p.notice_types]
    if not p.name or not p.naics:
        raise ValueError(f"{path}: [company] needs name and naics")
    return p
