"""Turns research results into the files a client receives and the sample you check by hand."""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

from .contacts import pick_contacts
from .models import CompanyResult, Contact

LEAD_COLUMNS = ["domain", "company", "score", "why_now", "signals", "sources", "contact_name", "title", "email",
                "email_status", "first_line", "first_line_status"]
COMPANY_COLUMNS = ["domain", "company", "status", "score", "fits_icp", "why_now", "criteria", "summary",
                   "disqualifiers", "pages_read", "error"]


def _signals(r: CompanyResult) -> str:
    if not r.research:
        return ""
    sigs = sorted(r.research.signals, key=lambda s: (not s.verified, s.stale))

    def label(s):
        flags = [f for f, on in (("unverified", not s.verified), ("old", s.stale)) if on]
        date = f" [{s.date}]" if s.date else ""
        return f"{s.type or 'signal'}: {s.evidence}{date}" + (f" ({', '.join(flags)})" if flags else "")

    return " | ".join(label(s) for s in sigs)


def _sources(r: CompanyResult) -> str:
    if not r.research:
        return ""
    return " | ".join(dict.fromkeys(s.source_url for s in r.research.signals if s.source_url))


async def lead_rows(results: list[CompanyResult], contacts: dict[str, list[Contact]], target_titles: list[str],
                    per_company: int, verifier) -> list[dict]:
    rows = []
    for r in results:
        if r.status != "qualified" or not r.research:
            continue
        base = {
            "domain": r.domain,
            "company": r.research.company_name or r.input_name,
            "score": r.research.score,
            "why_now": r.research.why_now,
            "signals": _signals(r),
            "sources": _sources(r),
            "first_line": r.first_line,
            "first_line_status": r.first_line_status,
        }
        picked = pick_contacts(contacts.get(r.domain, []), target_titles, per_company)
        if not picked:
            rows.append({**base, "contact_name": "", "title": "", "email": "", "email_status": "no_contact"})
        for c in picked:
            rows.append({**base, "contact_name": c.full_name, "title": c.title, "email": c.email,
                         "email_status": await verifier.verify(c.email)})
    rows.sort(key=lambda x: -x["score"])
    return rows


def company_rows(results: list[CompanyResult]) -> list[dict]:
    rows = []
    for r in results:
        res = r.research
        rows.append({
            "domain": r.domain,
            "company": (res.company_name if res else "") or r.input_name,
            "status": r.status,
            "score": res.score if res else "",
            "fits_icp": res.fits_icp if res else "",
            "why_now": res.why_now if res else "",
            "criteria": " | ".join(f"{c.name}: {c.status}" + (f" ({c.evidence})" if c.evidence else "")
                                   for c in res.criteria) if res else "",
            "summary": res.summary if res else "",
            "disqualifiers": " | ".join(res.disqualifiers) if res else "",
            "pages_read": len(r.pages),
            "error": r.error,
        })
    return rows


def qa_sample(rows: list[dict], share: float = 0.05, minimum: int = 5, seed: int = 7) -> list[dict]:
    """Random rows to check by hand before delivery (the plan's 5% quality check)."""
    k = min(len(rows), max(minimum, math.ceil(len(rows) * share)))
    return random.Random(seed).sample(rows, k)


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
