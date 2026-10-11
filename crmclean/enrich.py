"""Fills in a company's missing industry, description, size and location from its own website, with each value
backed by a quote from the page that code checks."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import has_amount, quote_found, unsupported_numbers, word_count
from agentkit.places import STATES, norm_state

from .config import CRMConfig
from .records import Record
from .rules import Change, Flag, norm_domain

FIELDS = ["industry", "description", "numberofemployees", "city", "state"]
STAFF_WORDS = re.compile(r"\b(?:employees?|staff|people|team members?|professionals|engineers|workers|technicians|"
                         r"specialists|scientists|consultants|experts)\b", re.I)  # "since 1987" is not a staff count

SYSTEM = """You fill in missing company details in a CRM from the company's own website.

Rules:
- Use only PAGE TEXT. For every value, give the quote from the page that shows it, copied word for word. Leave a value "" (or null) when the page doesn't show it.
- industry: one label from ALLOWED INDUSTRIES, exactly as written there, or "".
- description: one plain sentence of at most 25 words on what the company does and for whom. Its quote is the page sentence it is based on.
- employees: a number only when the page states how many people work there.
- city and state: only the company's own location (headquarters or office), as the page states it.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"industry": {"value": "<label or ''>", "quote": "<...>"}, "description": {"value": "<one sentence>", "quote": "<...>"}, "employees": {"value": <number or null>, "quote": "<...>"}, "city": {"value": "<...>", "quote": "<...>"}, "state": {"value": "<...>", "quote": "<...>"}}"""


def build_prompt(c: Record, page_text: str, url: str, cfg: CRMConfig) -> str:
    return (f"COMPANY: {c.label} ({norm_domain(c.get('domain'))})\nPAGE: {url}\n\nALLOWED INDUSTRIES: "
            f"{'; '.join(cfg.industries)}\n\nPAGE TEXT\n{page_text[:12000]}\n\n"
            f"Return JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}")


class Valued(BaseModel):
    value: str = ""
    quote: str = ""

    @field_validator("value", "quote", mode="before")
    @classmethod
    def _s(cls, v):
        return "" if v is None else str(v).strip()


class Enrichment(BaseModel):
    industry: Valued = Field(default_factory=Valued)
    description: Valued = Field(default_factory=Valued)
    employees: Valued = Field(default_factory=Valued)
    city: Valued = Field(default_factory=Valued)
    state: Valued = Field(default_factory=Valued)

    @field_validator("*", mode="before")
    @classmethod
    def _v(cls, v):
        if isinstance(v, dict):
            return v
        return {"value": "" if v is None else str(v)}


def missing_fields(c: Record) -> list[str]:
    return [f for f in FIELDS if not c.get(f)]


def page_for(domain: str, sites_dir: Path | None) -> tuple[str, str]:
    """(text, where it came from) from a saved page in the sites folder (<domain>.html or .txt), else ("", "")."""
    if not sites_dir:
        return "", ""
    for ext in (".html", ".htm", ".txt", ".md"):
        p = sites_dir / f"{domain}{ext}"
        if p.exists():
            raw = p.read_text(encoding="utf-8", errors="replace")
            if ext in (".html", ".htm"):
                from leadagent.fetch import extract_text
                _, raw, _ = extract_text(raw)
            return raw, f"{p.name} (saved page)"
    return "", ""


async def fetch_page(domain: str, reader) -> tuple[str, str]:
    """The homepage and about page from the live site, through leadagent's polite reader."""
    pages = await reader.read(domain)
    text = "\n\n".join(f"[{p.url}]\n{p.text}" for p in pages)
    return text, pages[0].url if pages else ""


def check_enrichment(e: Enrichment, c: Record, page: str, source: str, cfg: CRMConfig
                     ) -> tuple[list[Change], list[Flag]]:
    changes, flags = [], []
    want = set(missing_fields(c))

    def add(field: str, value: str, quote: str, reason: str) -> None:
        changes.append(Change("company", c.id, c.label, field, c.get(field), value, reason, by="model",
                              source=f"{source}: \"{quote[:160]}\""))

    def backed(v: Valued, what: str) -> bool:
        if not v.value:
            return False
        if not v.quote or not quote_found(v.quote, page, 0.85):
            flags.append(Flag("company", c.id, c.label, "unbacked", f"{what} '{v.value}' dropped: its quote isn't on the "
                                                                    "page"))
            return False
        return True

    if "industry" in want and backed(e.industry, "industry"):
        label = next((i for i in cfg.industries if i.lower() == e.industry.value.lower()), "")
        if label:
            add("industry", label, e.industry.quote, "industry from the company's website")
        else:
            flags.append(Flag("company", c.id, c.label, "unknown_industry", f"industry '{e.industry.value}' is not one "
                                                                            "of the portal's labels; left blank"))
    if "description" in want and backed(e.description, "description"):
        d = e.description.value
        nums = unsupported_numbers(d, page)
        if word_count(d) > 30:
            flags.append(Flag("company", c.id, c.label, "long_description", "description over 30 words; left blank"))
        elif nums:
            flags.append(Flag("company", c.id, c.label, "unbacked", f"description mentions {', '.join(nums)}, which "
                                                                    "isn't on the page; left blank"))
        else:
            add("description", d, e.description.quote, "description from the company's website")
    if "numberofemployees" in want and backed(e.employees, "employees"):
        n = re.sub(r"[^\d]", "", e.employees.value)
        if n and has_amount(float(n), e.employees.quote) and STAFF_WORDS.search(e.employees.quote):
            add("numberofemployees", n, e.employees.quote, "staff count stated on the website")
        else:
            flags.append(Flag("company", c.id, c.label, "unbacked", f"employees '{e.employees.value}' is not a staff "
                                                                    "count in its quote; left blank"))
    if "city" in want and backed(e.city, "city"):
        if e.city.value.lower() in e.city.quote.lower():
            add("city", e.city.value, e.city.quote, "location stated on the website")
    if "state" in want and backed(e.state, "state"):
        code = norm_state(e.state.value)
        if code:
            add("state", code if cfg.state_format == "code" else STATES[code], e.state.quote,
                "location stated on the website")
    return changes, flags
