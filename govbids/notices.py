"""Reads SAM.gov contract notices (CSV export or the API) and applies the hard rules in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agentkit.checks import parse_date
from agentkit.places import norm_state
from agentkit.tables import pick, read_table

from .profile import CLEARANCES, Profile

# SAM.gov set-aside codes -> the certification a company needs (any one of them).
SET_ASIDES = {
    "SBA": {"SB"}, "SBP": {"SB"}, "8A": {"8A"}, "8AN": {"8A"}, "HZC": {"HZ"}, "HZS": {"HZ"},
    "SDVOSBC": {"SDVOSB"}, "SDVOSBS": {"SDVOSB"}, "WOSB": {"WOSB", "EDWOSB"}, "WOSBSS": {"WOSB", "EDWOSB"},
    "EDWOSB": {"EDWOSB"}, "EDWOSBSS": {"EDWOSB"}, "VSA": {"VOSB", "SDVOSB"}, "VSS": {"VOSB", "SDVOSB"},
    "ISBEE": {"ISBEE"}, "IEE": {"ISBEE"},
}
SET_ASIDE_WORDS = [  # for exports that only carry the description
    (r"service[- ]disabled veteran", "SDVOSBC"), (r"\bhubzone\b", "HZC"), (r"\b8\s*\(?a\)?", "8A"),
    (r"economically disadvantaged women", "EDWOSB"), (r"women[- ]owned", "WOSB"), (r"veteran[- ]owned", "VSA"),
    (r"indian (?:small business|economic)", "ISBEE"), (r"partial small business", "SBP"),
    (r"total small business|small business set[- ]aside", "SBA"),
]
CLEARANCE_RE = re.compile(r"\b(ts/sci|top secret|secret|public trust)\b(?:[- ](?:level|security))?\s*clearance|"
                          r"\bclearance\b[^.]{0,40}\b(ts/sci|top secret|secret|public trust)\b", re.I)


@dataclass
class Notice:
    id: str
    title: str = ""
    solicitation: str = ""
    agency: str = ""
    office: str = ""
    posted: str = ""
    type: str = ""
    set_aside_code: str = ""
    set_aside: str = ""
    deadline: date | None = None
    deadline_text: str = ""
    naics: str = ""
    city: str = ""
    state: str = ""
    link: str = ""
    description: str = ""
    out: list[str] = field(default_factory=list)  # why it's filtered out (code)
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        """What the model sees, and where quotes must come from."""
        head = [f"Notice: {self.id}", f"Title: {self.title}", f"Agency: {self.agency} {self.office}".strip(),
                f"Type: {self.type}", f"Set-aside: {self.set_aside or self.set_aside_code or 'none'}",
                f"NAICS: {self.naics}", f"Place of performance: {', '.join(x for x in (self.city, self.state) if x)}",
                f"Response deadline: {self.deadline_text}"]
        return "\n".join(head) + f"\n\n{self.description[:4000]}"


def set_aside_code(code: str, text: str) -> str:
    c = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if c in SET_ASIDES:
        return c
    for rx, value in SET_ASIDE_WORDS:
        if re.search(rx, text or "", re.I):
            return value
    return ""


def read_notices(path: str | Path) -> list[Notice]:
    """SAM.gov's Contract Opportunities CSV (or `fetch` output): one row per notice."""
    out = []
    for i, r in enumerate(read_table(path), 2):
        n = Notice(id=pick(r, "noticeid", "notice id", "id") or f"row{i}", title=pick(r, "title"),
                   solicitation=pick(r, "sol#", "solicitation number", "solicitationnumber"),
                   agency=pick(r, "department/ind.agency", "department", "agency", "fullparentpathname"),
                   office=pick(r, "office", "sub-tier", "subtier"), posted=pick(r, "posteddate", "posted date"),
                   type=pick(r, "type", "notice type", "basetype"), set_aside=pick(r, "setaside", "set aside",
                                                                                    "typeofsetasidedescription"),
                   deadline_text=pick(r, "responsedeadline", "response deadline", "response date"),
                   naics=pick(r, "naicscode", "naics code", "naics")[:6], city=pick(r, "popcity", "pop city"),
                   state=norm_state(pick(r, "popstate", "pop state")) or pick(r, "popstate", "pop state"),
                   link=pick(r, "link", "uilink", "url"), description=pick(r, "description"))
        n.set_aside_code = set_aside_code(pick(r, "setasidecode", "set aside code", "typeofsetaside"), n.set_aside)
        n.deadline = parse_date(n.deadline_text[:10]) or parse_date(n.deadline_text)
        out.append(n)
    return out


def required_clearance(text: str) -> str:
    levels = [m.group(1) or m.group(2) for m in CLEARANCE_RE.finditer(text or "")]
    levels = [x.lower() for x in levels if x]
    return max(levels, key=CLEARANCES.index) if levels else ""


def apply_rules(n: Notice, p: Profile, today: date) -> None:
    """Filter a notice out in code when the facts say the company can't or shouldn't bid."""
    kind = n.type.strip().lower()
    if kind and not any(kind.startswith(t) for t in p.notice_types):
        n.out.append(f"notice type '{n.type}'")
    if n.naics and n.naics not in p.naics:
        n.out.append(f"NAICS {n.naics} is not one of ours")
    need = SET_ASIDES.get(n.set_aside_code)
    if need and not need & set(p.certifications):
        n.out.append(f"set aside for {n.set_aside or n.set_aside_code}, which we don't hold")
    if n.deadline is None:
        n.notes.append(f"response deadline '{n.deadline_text}' could not be read")
    elif n.deadline < today:
        n.out.append(f"responses were due {n.deadline.isoformat()}")
    elif (n.deadline - today).days < p.min_days:
        n.out.append(f"due {n.deadline.isoformat()}, only {(n.deadline - today).days} days away")
    if n.state and p.places and "remote" not in [x.lower() for x in p.places] and n.state not in p.places:
        n.out.append(f"work is in {n.state}")
    elif n.state and p.places and n.state not in p.places:
        n.notes.append(f"work is in {n.state}; check it can be done remotely")
    words = [w for w in p.exclude_words if re.search(rf"\b{re.escape(w)}\b", f"{n.title} {n.description}", re.I)]
    if words:
        n.out.append(f"mentions {', '.join(words)}")
    need_level = required_clearance(n.description)
    if need_level and CLEARANCES.index(need_level) > CLEARANCES.index(p.clearance):
        n.notes.append(f"requires a {need_level.upper() if '/' in need_level else need_level} clearance; we have "
                       f"{p.clearance}")
