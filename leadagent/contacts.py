"""Loads companies and contacts from CSV exports and picks the right people per company.

Email finding and verification need a paid provider; until one is chosen, emails are only syntax-checked.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from .fetch import normalize_domain
from .models import Contact

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
DOMAIN_COLUMNS = ("domain", "website", "company_domain", "company website", "url")
NAME_COLUMNS = ("company", "company_name", "name", "organization", "account name")


def _get(row: dict, *names: str) -> str:
    lowered = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    for n in names:
        if lowered.get(n):
            return lowered[n]
    return ""


# Firmographic columns from data-provider exports (Apollo and similar), passed to the model as trusted LIST DATA.
FACT_COLUMNS: dict[str, tuple[str, ...]] = {
    "Employees": ("# employees", "employees", "employee count", "number of employees", "headcount", "company size"),
    "Funding stage": ("latest funding", "funding stage", "latest funding round", "last funding round", "stage"),
    "Latest funding amount": ("latest funding amount", "last funding amount"),
    "Last raised": ("last raised at", "last funding date", "latest funding date"),
    "Total funding": ("total funding", "total funding amount", "total raised"),
    "Industry": ("industry",),
    "Country": ("company country", "hq country", "country"),
    "Founded": ("founded year", "founded"),
    "Annual revenue": ("annual revenue", "revenue"),
}


def company_facts(row: dict) -> dict[str, str]:
    return {label: v for label, names in FACT_COLUMNS.items() if (v := _get(row, *names))}


def load_companies(path: str | Path) -> list[tuple[str, str, dict[str, str]]]:
    """Return [(domain, name, facts)] in file order, de-duplicated by domain."""
    seen: set[str] = set()
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            domain = normalize_domain(_get(row, *DOMAIN_COLUMNS))
            if not domain or domain in seen:
                continue
            seen.add(domain)
            out.append((domain, _get(row, *NAME_COLUMNS), company_facts(row)))
    return out


def load_contacts(path: str | Path) -> dict[str, list[Contact]]:
    """Contacts keyed by company domain. Works with Apollo-style exports and with the companies file itself."""
    by_domain: dict[str, list[Contact]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            email = _get(row, "email", "work email", "email address")
            domain = normalize_domain(_get(row, *DOMAIN_COLUMNS)) or (email.split("@")[-1].lower() if "@" in email else "")
            first = _get(row, "first_name", "first name")
            last = _get(row, "last_name", "last name")
            if not first and not last:
                full = _get(row, "full_name", "full name", "contact name", "person name")
                first, _, last = full.partition(" ")
            title = _get(row, "title", "job title")
            if domain and (email or first or title):
                by_domain[domain].append(Contact(domain=domain, first_name=first, last_name=last, title=title, email=email))
    return dict(by_domain)


def pick_contacts(contacts: list[Contact], target_titles: list[str], limit: int) -> list[Contact]:
    """Contacts whose title matches a target title, best match first (order of target_titles)."""
    if not target_titles:
        return contacts[:limit]
    ranked = []
    for c in contacts:
        title = c.title.lower()
        rank = next((i for i, t in enumerate(target_titles) if re.search(rf"\b{re.escape(t.lower())}\b", title)), None)
        if rank is not None:
            ranked.append((rank, c))
    ranked.sort(key=lambda x: x[0])
    return [c for _, c in ranked[:limit]]


class EmailVerifier(Protocol):
    async def verify(self, email: str) -> str: ...


class SyntaxOnlyVerifier:
    """Placeholder until a verification provider is chosen: flags malformed addresses only."""

    async def verify(self, email: str) -> str:
        if not email:
            return "missing"
        return "unverified" if EMAIL_RE.match(email) else "invalid_syntax"
