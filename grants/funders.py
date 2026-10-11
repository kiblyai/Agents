"""Reads the funder list and applies the hard rules in code: invitation only, geography, grant size, deadline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agentkit.checks import parse_date
from agentkit.docs import read_document
from agentkit.places import STATES
from agentkit.tables import pick, read_table

from .org import Org

INVITE_ONLY = re.compile(r"\b(?:by invitation only|invitation[- ]only|invite[- ]only|does not accept unsolicited|"
                         r"do not accept unsolicited|no unsolicited|unsolicited (?:proposals|requests|applications) "
                         r"(?:are|will) not (?:be )?accepted|pre-selected organizations only)\b", re.I)
NATIONWIDE = re.compile(r"\b(?:national|nationwide|united states|u\.s\.|all (?:50 )?states|anywhere in the us)\b", re.I)
REGIONS = {
    "midwest": ["IL", "IN", "IA", "KS", "MI", "MN", "MO", "NE", "ND", "OH", "SD", "WI"],
    "great lakes": ["IL", "IN", "MI", "MN", "NY", "OH", "PA", "WI"],
    "new england": ["CT", "ME", "MA", "NH", "RI", "VT"],
    "mid-atlantic": ["DE", "MD", "NJ", "NY", "PA", "DC"],
    "southeast": ["AL", "FL", "GA", "KY", "MS", "NC", "SC", "TN", "VA", "WV", "AR", "LA"],
    "southwest": ["AZ", "NM", "OK", "TX"],
    "pacific northwest": ["OR", "WA", "ID"],
    "west coast": ["CA", "OR", "WA"],
    "appalachia": ["KY", "OH", "PA", "TN", "VA", "WV", "NC"],
}


@dataclass
class Funder:
    row: int
    name: str
    website: str = ""
    focus: str = ""
    geography: str = ""
    typical_grant: str = ""
    deadline_text: str = ""
    application: str = ""
    contact: str = ""
    guidelines: str = ""  # text of the funder's guidelines, from the CSV or a file in the guidelines folder
    guidelines_source: str = ""
    grant_range: tuple[float, float] | None = None  # (min, max); max 0 = no upper limit
    deadline: date | None = None
    rolling: bool = False
    out: list[str] = field(default_factory=list)  # why it's ruled out (code)
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        """Everything known about the funder; quotes must come from here."""
        parts = [f"Funder: {self.name}", f"Focus: {self.focus}", f"Geography: {self.geography}",
                 f"Typical grant: {self.typical_grant}", f"Deadline: {self.deadline_text}",
                 f"Application: {self.application}"]
        return "\n".join(p for p in parts if not p.endswith(": ")) + (f"\n\nGuidelines:\n{self.guidelines}"
                                                                       if self.guidelines else "")


MONEY_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?\b", re.I)


def _money_values(text: str) -> list[float]:
    scale = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}
    return [float(n.replace(",", "")) * scale.get((s or "").lower(), 1) for n, s in MONEY_RE.findall(text or "")]


def grant_range(text: str) -> tuple[float, float] | None:
    """'$10,000 - $30,000' -> (10000, 30000); 'up to $5,000' -> (0, 5000); '$50,000+' -> (50000, 0)."""
    vals = _money_values(text)
    if not vals:
        return None
    low = (text or "").lower()
    if len(vals) >= 2:
        return min(vals), max(vals)
    v = vals[0]
    if re.search(r"\b(?:up to|maximum|max\.?|no more than|not to exceed|under)\b", low):
        return 0.0, v
    if re.search(r"\b(?:at least|minimum|min\.?|over|more than)\b", low) or re.search(r"\d\s*\+", low):
        return v, 0.0
    return v, v


def states_in(text: str) -> set[str]:
    out = set()
    for code, name in STATES.items():
        if re.search(rf"\b{re.escape(name)}\b", text, re.I) or re.search(rf"\b{code}\b", text):
            out.add(code)
    for region, codes in REGIONS.items():
        if re.search(rf"\b{region}\b", text, re.I):
            out |= set(codes)
    return out


def geography(funder_geo: str, org: Org) -> tuple[str, str]:
    """('ok' | 'no' | 'unclear', reason) for the funder's geography against where the nonprofit works."""
    text = funder_geo or ""
    if not text.strip() or NATIONWIDE.search(text):
        return "ok", ""
    places = [p.lower() for p in org.service_area + [org.city]]
    counties = [c.lower() for c in re.findall(r"([A-Z][A-Za-z.\- ]+? Count(?:y|ies))", text)]
    mentions_ours = any(p and p in text.lower() for p in places)
    states = states_in(text)
    if states and org.state not in states:
        return "no", f"funds {text.strip()}; we work in {org.state}"
    if counties and not mentions_ours:
        return "no", f"funds {text.strip()}; we serve {', '.join(org.service_area) or org.city}"
    if states or mentions_ours:
        return "ok", ""
    return "unclear", f"geography '{text.strip()}' could not be checked"


def read_funders(path: str | Path, guidelines_dir: str | Path | None = None) -> list[Funder]:
    gdir = Path(guidelines_dir) if guidelines_dir else None
    files = {p.stem.lower(): p for p in gdir.iterdir() if p.is_file()} if gdir and gdir.is_dir() else {}
    out = []
    for i, r in enumerate(read_table(path), 2):
        f = Funder(row=i, name=pick(r, "funder", "name", "foundation", "funder name"),
                   website=pick(r, "website", "url"), focus=pick(r, "focus", "focus areas", "priorities", "areas"),
                   geography=pick(r, "geography", "geographic focus", "region", "service area"),
                   typical_grant=pick(r, "typical grant", "grant size", "grant range", "award range", "amount"),
                   deadline_text=pick(r, "deadline", "due date", "next deadline"),
                   application=pick(r, "application", "application type", "process", "how to apply"),
                   contact=pick(r, "contact", "program officer"),
                   guidelines=pick(r, "guidelines", "notes"))
        ref = pick(r, "guidelines file", "file")
        slug = re.sub(r"[^a-z0-9]+", "-", f.name.lower()).strip("-")
        gfile = files.get(Path(ref).stem.lower()) if ref else files.get(slug)
        if gfile:
            try:
                f.guidelines = "\n\n".join(body for _, body in read_document(gfile)).strip()
                f.guidelines_source = gfile.name
            except ValueError as e:
                f.notes.append(str(e))
        out.append(f)
    return out


def apply_rules(f: Funder, org: Org, today: date, soon_days: int = 14) -> None:
    """Rule a funder out in code when the facts say so; the model only scores the rest."""
    everything = " ".join([f.application, f.guidelines, f.focus])
    m = INVITE_ONLY.search(everything)
    if m:
        f.out.append(f"invitation only ('{m.group(0)}')")
    geo, why = geography(f.geography, org)
    if geo == "no":
        f.out.append(f"outside its geography: {why}")
    elif geo == "unclear":
        f.notes.append(why)
    f.grant_range = grant_range(f.typical_grant)
    if f.grant_range:
        lo, hi = f.grant_range
        if hi and hi < org.ask_min:
            f.out.append(f"grants up to ${hi:,.0f}, below the ${org.ask_min:,.0f} minimum worth applying for")
        elif lo > org.ask_max:
            f.out.append(f"grants start at ${lo:,.0f}, above the ${org.ask_max:,.0f} we seek")
        elif org.annual_budget and lo > 0.5 * org.annual_budget:
            f.out.append(f"grants start at ${lo:,.0f}, over half the ${org.annual_budget:,.0f} budget")
    elif f.typical_grant:
        f.notes.append(f"grant size '{f.typical_grant}' could not be read")
    d = f.deadline_text.strip().lower()
    if re.search(r"\brolling|ongoing|any ?time|open\b", d):
        f.rolling = True
    elif d:
        f.deadline = parse_date(f.deadline_text, today)
        if f.deadline is None:
            f.notes.append(f"deadline '{f.deadline_text}' could not be read")
        elif f.deadline < today:
            f.out.append(f"deadline {f.deadline.isoformat()} has passed (check when the next cycle opens)")
        elif (f.deadline - today).days <= soon_days:
            f.notes.append(f"deadline {f.deadline.isoformat()} is only {(f.deadline - today).days} days away")
