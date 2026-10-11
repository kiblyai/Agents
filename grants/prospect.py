"""Scores how well each remaining funder fits the nonprofit, with quotes from the funder's own words, checked in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import parse_date, quote_found

from .funders import Funder
from .org import Org

SYSTEM = """You research grant funders for a small US nonprofit and judge how well each one fits.

Rules:
- Use only FUNDER INFORMATION. Every priority, concern and requirement you list needs a quote: words copied exactly from it.
- fit: 0-100. 80+ means the funder's stated priorities match one of our programs directly; 50-79 a partial match; under 50 a poor match (different field, capital projects only, research only, and so on).
- best_program: the one of OUR PROGRAMS that fits best, named exactly, or "" if none fits.
- concerns: anything that makes us ineligible or a weak applicant (operating-support limits, matching funds, minimum budget, years in operation, required partnerships).
- suggested_ask: a dollar amount inside both the funder's typical range and our range, or null.
- deadline: YYYY-MM-DD, "rolling" or "".
- Reply with one JSON object only."""

SCHEMA_HINT = """{"fit": <0-100>, "best_program": "<our program name or ''>", "why": "<2-3 plain sentences>", "priorities_matched": [{"priority": "<...>", "quote": "<exact words from the funder information>"}], "concerns": [{"concern": "<...>", "quote": "<exact words>"}], "requirements": [{"requirement": "<...>", "quote": "<exact words>"}], "suggested_ask": <number or null>, "application": "<letter of inquiry|full proposal|online form|...>", "deadline": "<YYYY-MM-DD|rolling|''>"}"""


def build_prompt(f: Funder, org: Org) -> str:
    return (f"OUR NONPROFIT\n{org.as_text()}\n\nFUNDER INFORMATION\n{f.text()[:12000]}\n\n"
            f"Return JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}")


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    return str(v).strip()


class Quoted(BaseModel):
    point: str = ""
    quote: str = ""

    @classmethod
    def from_any(cls, v, key: str) -> "Quoted":
        if isinstance(v, dict):
            return cls(point=_str(v.get(key) or v.get("point") or v.get("text")), quote=_str(v.get("quote")))
        return cls(point=_str(v))


class FitReply(BaseModel):
    fit: int = 0
    best_program: str = ""
    why: str = ""
    priorities_matched: list[Quoted] = Field(default_factory=list)
    concerns: list[Quoted] = Field(default_factory=list)
    requirements: list[Quoted] = Field(default_factory=list)
    suggested_ask: float | None = None
    application: str = ""
    deadline: str = ""

    @field_validator("fit", mode="before")
    @classmethod
    def _fit(cls, v):
        m = re.search(r"\d+", str(v or 0))
        return max(0, min(100, int(m.group(0)))) if m else 0

    @field_validator("best_program", "why", "application", "deadline", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("priorities_matched", "concerns", "requirements", mode="before")
    @classmethod
    def _q(cls, v, info):
        key = {"priorities_matched": "priority", "concerns": "concern", "requirements": "requirement"}[info.field_name]
        items = v if isinstance(v, list) else ([v] if v else [])
        return [Quoted.from_any(x, key) for x in items]

    @field_validator("suggested_ask", mode="before")
    @classmethod
    def _n(cls, v):
        if v is None or isinstance(v, bool):
            return None
        m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v))
        return float(m.group(0).replace(",", "")) if m else None


@dataclass
class Prospect:
    funder: Funder
    fit: int | None = None
    best_program: str = ""
    why: str = ""
    priorities: list[Quoted] = field(default_factory=list)
    concerns: list[Quoted] = field(default_factory=list)
    requirements: list[Quoted] = field(default_factory=list)
    suggested_ask: float | None = None
    application: str = ""
    checks: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def status(self) -> str:
        if self.funder.out:
            return "ruled out"
        if self.error:
            return "not scored"
        return "strong" if (self.fit or 0) >= 70 else "possible" if (self.fit or 0) >= 50 else "weak"


def _verified(items: list[Quoted], text: str, what: str, checks: list[str]) -> list[Quoted]:
    ok = [q for q in items if q.quote and quote_found(q.quote, text, 0.8)]
    dropped = len(items) - len(ok)
    if dropped:
        checks.append(f"{dropped} {what} dropped: the quote is not in the funder's information")
    return ok


def check_fit(r: FitReply, f: Funder, org: Org) -> Prospect:
    p = Prospect(funder=f, fit=r.fit, why=r.why, application=r.application)
    text = f.text()
    p.priorities = _verified(r.priorities_matched, text, "priorities", p.checks)
    p.concerns = _verified(r.concerns, text, "concerns", p.checks)
    p.requirements = _verified(r.requirements, text, "requirements", p.checks)
    if not p.priorities and p.fit > 40:
        p.checks.append(f"fit lowered from {p.fit} to 40: no priority quoted from the funder's own words")
        p.fit = 40
    prog = org.program(r.best_program)
    if r.best_program and prog is None:
        p.checks.append(f"'{r.best_program}' is not one of our programs")
    p.best_program = prog.name if prog else ""
    ask = r.suggested_ask
    if ask is not None:
        lo, hi = f.grant_range or (0.0, 0.0)
        lo, hi = max(lo, org.ask_min), min(hi or org.ask_max, org.ask_max)
        if lo <= hi and not lo <= ask <= hi:
            fixed = min(max(ask, lo), hi)
            p.checks.append(f"suggested ask ${ask:,.0f} is outside ${lo:,.0f}-${hi:,.0f}; using ${fixed:,.0f}")
            ask = fixed
    p.suggested_ask = ask
    said = r.deadline.strip().lower()
    if said and said != "rolling" and f.deadline and parse_date(said) != f.deadline:
        p.checks.append(f"the model read the deadline as {r.deadline}; the funder list says {f.deadline.isoformat()}")
    if said == "rolling" and f.deadline:
        p.checks.append(f"the model says rolling; the funder list gives {f.deadline.isoformat()}")
    if said and said != "rolling" and f.rolling:
        p.checks.append(f"the model gives a deadline of {r.deadline}; the funder list says rolling")
    return p
