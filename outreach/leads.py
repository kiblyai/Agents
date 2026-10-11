"""Reads leads (leadagent's leads.csv, an Apollo export or any CSV/Excel with the usual columns) and checks the
meeting bar in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentkit.checks import valid_email
from agentkit.tables import pick, read_table

from .campaign import Campaign

COUNTRIES = {"us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states": "US",
             "united states of america": "US", "america": "US", "ca": "CA", "canada": "CA", "uk": "GB", "gb": "GB",
             "united kingdom": "GB", "england": "GB", "great britain": "GB", "au": "AU", "australia": "AU",
             "ie": "IE", "ireland": "IE", "nz": "NZ", "new zealand": "NZ"}


def norm_country(s: str) -> str:
    s = (s or "").strip().lower()
    return COUNTRIES.get(s, s.upper() if len(s) == 2 else s.title())


def employee_range(s: str) -> tuple[int, int] | None:
    """'60' -> (60, 60); '51-200' or '51 to 200' -> (51, 200); '1,000+' -> (1000, 0); '' -> None."""
    t = (s or "").replace(",", "").strip()
    m = re.match(r"^(\d+)\s*(?:-|–|to)\s*(\d+)$", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+)\s*\+$", t)
    if m:
        return int(m.group(1)), 0
    return (int(t), int(t)) if t.isdigit() else None


@dataclass
class Lead:
    row: int
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    company: str = ""
    website: str = ""
    employees: str = ""
    country: str = ""
    city: str = ""
    industry: str = ""
    signal: str = ""  # why now: a recent, checkable fact about the company
    signal_source: str = ""
    opening_line: str = ""  # leadagent's first line, if any
    held: list[str] = field(default_factory=list)  # why no emails are written for this lead
    notes: list[str] = field(default_factory=list)  # things to know, not blocking

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def domain(self) -> str:
        return self.email.split("@")[-1].lower() if "@" in self.email else ""

    def facts(self) -> list[tuple[str, str]]:
        """What the model may say about the lead, labeled; the checks look for its words here."""
        items = [("Name", self.name), ("Title", self.title), ("Company", self.company), ("Website", self.website),
                 ("Industry", self.industry), ("City", self.city), ("Employees", self.employees),
                 ("Recent signal", self.signal), ("Opening line already written", self.opening_line)]
        return [(k, v) for k, v in items if v]

    def facts_text(self) -> str:
        return "\n".join(v for _, v in self.facts())


def read_leads(path: str | Path) -> list[Lead]:
    leads = []
    for i, r in enumerate(read_table(path), 2):
        first, last = pick(r, "first name", "firstname", "first"), pick(r, "last name", "lastname", "last")
        if not first:
            full = pick(r, "contact name", "full name", "name", "contact")
            first, _, last = full.partition(" ")
        leads.append(Lead(
            row=i, email=pick(r, "email", "email address", "work email", "contact email").strip().lower(),
            first_name=first.strip(), last_name=last.strip(),
            title=pick(r, "title", "job title", "position"),
            company=pick(r, "company", "company name", "organization", "account name", "account"),
            website=pick(r, "website", "company website", "domain", "company domain", "url"),
            employees=pick(r, "# employees", "employees", "employee count", "number of employees", "company size",
                           "headcount"),
            country=norm_country(pick(r, "country", "company country", "person country")),
            city=pick(r, "city", "company city", "person city"),
            industry=pick(r, "industry"),
            signal=pick(r, "why now", "why_now", "signal", "trigger", "buying signal"),
            signal_source=pick(r, "signal source", "sources", "source"),
            opening_line=pick(r, "first line", "first_line", "opening line", "icebreaker"),
        ))
    return leads


def bar_problems(title: str, employees: str, country: str, c: Campaign) -> tuple[list[str], list[str]]:
    """(reasons a meeting would not be billable, things that can't be checked) against the campaign's bar."""
    fails, unknown = [], []
    if c.titles:
        low = (title or "").lower()
        if not low:
            unknown.append("no job title")
        elif not any(re.search(rf"\b{re.escape(t)}\b", low) for t in c.titles):
            fails.append(f"title '{title}' is not one the client wants")
    if c.min_employees or c.max_employees:
        rng = employee_range(employees)
        if rng is None:
            unknown.append("company size unknown")
        else:
            lo, hi = rng
            if hi and hi < c.min_employees:
                fails.append(f"company has {employees} employees; the bar is {c.min_employees}"
                             + (f"-{c.max_employees}" if c.max_employees else "+"))
            elif c.max_employees and lo > c.max_employees:
                fails.append(f"company has {employees} employees; the bar is {c.min_employees}-{c.max_employees}")
            elif lo < c.min_employees or (c.max_employees and (hi == 0 or hi > c.max_employees)):
                unknown.append(f"company size {employees} straddles the bar")
    if c.countries:
        if not country:
            unknown.append("country unknown")
        elif country not in c.countries:
            fails.append(f"country {country} is outside {', '.join(c.countries)}")
    return fails, unknown


def screen(leads: list[Lead], c: Campaign, suppressed: set[str] = frozenset()) -> None:
    """Hold back leads that must not get emails, in code: bad or repeated emails, do-not-contact, outside the bar."""
    seen: set[str] = set()
    for lead in leads:
        if not valid_email(lead.email):
            lead.held.append(f"no valid email ('{lead.email}')" if lead.email else "no email")
        elif lead.email in seen:
            lead.held.append("same email as an earlier row")
        elif lead.email in suppressed or (lead.domain and f"@{lead.domain}" in suppressed):
            lead.held.append("on the do-not-contact list")
        seen.add(lead.email)
        fails, unknown = bar_problems(lead.title, lead.employees, lead.country, c)
        if fails and not c.write_outside_bar:
            lead.held.append("outside the meeting bar: " + "; ".join(fails))
        elif fails:
            lead.notes.append("a meeting would not be billable: " + "; ".join(fails))
        if unknown:
            lead.notes.append("check the bar: " + "; ".join(unknown))
        if not lead.signal and not lead.held:
            lead.notes.append("no recent signal, so the first line uses the company or industry")


def read_suppressed(path: str | Path | None) -> set[str]:
    """Emails (or '@domain' entries) never to contact: a previous run's suppress.csv or the sequencer's blocklist."""
    if not path:
        return set()
    out = set()
    for r in read_table(path):
        v = pick(r, "email", "email address", "address", "domain").strip().lower()
        if v:
            out.add(v if "@" in v else f"@{v}")
    return out
