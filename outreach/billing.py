"""Which meetings the client pays for: held meetings with people inside the meeting bar. Code only, no model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentkit.tables import pick, read_table

from .campaign import Campaign
from .leads import Lead, bar_problems, norm_country

HELD = {"held", "done", "completed", "complete", "yes", "attended", "happened"}


@dataclass
class Meeting:
    row: int
    day: str
    email: str
    name: str
    title: str
    company: str
    status: str
    notes: str


def read_meetings(path: str | Path) -> list[Meeting]:
    return [Meeting(row=i, day=pick(r, "date", "meeting date", "day"), email=pick(r, "email", "attendee email").lower(),
                    name=pick(r, "name", "attendee", "contact"), title=pick(r, "title", "job title"),
                    company=pick(r, "company", "account"), status=pick(r, "status", "outcome", "held").lower(),
                    notes=pick(r, "notes", "note", "comments"))
            for i, r in enumerate(read_table(path), 2)]


def bill(meetings: list[Meeting], leads: list[Lead], c: Campaign) -> tuple[list[dict], dict]:
    """One row per meeting (billable yes / no / check, with the reason) and the invoice totals."""
    by_email = {x.email: x for x in leads if x.email}
    rows = []
    for m in meetings:
        lead = by_email.get(m.email)
        source = "lead list"
        if lead is None and "@" in m.email:  # a referral: use the company facts of a lead at the same domain
            domain = m.email.split("@")[-1]
            lead = next((x for x in leads if x.domain == domain), None)
            source = f"company facts from {lead.email}" if lead else "not in the lead list"
        title = m.title or (lead.title if lead and lead.email == m.email else "")
        employees = lead.employees if lead else ""
        country = lead.country if lead else ""
        fails, unknown = bar_problems(title, employees, norm_country(country), c)
        if m.status not in HELD:
            billable, reason = "no", f"status '{m.status or 'blank'}' (only held meetings are billed)"
        elif fails:
            billable, reason = "no", "; ".join(fails)
        elif unknown:
            billable, reason = "check", "; ".join(unknown) + " - confirm before billing"
        else:
            billable, reason = "yes", f"held, inside the bar ({source})"
        rows.append({"date": m.day, "name": m.name, "email": m.email, "title": title, "company": m.company,
                     "status": m.status, "billable": billable,
                     "amount": f"{c.per_meeting:.2f}" if billable == "yes" else "0.00", "reason": reason})
    n = sum(1 for r in rows if r["billable"] == "yes")
    totals = {"platform_fee": c.platform_fee, "billable_meetings": n, "per_meeting": c.per_meeting,
              "meetings_total": n * c.per_meeting, "to_confirm": sum(1 for r in rows if r["billable"] == "check"),
              "invoice_total": c.platform_fee + n * c.per_meeting}
    return rows, totals


INVOICE_COLUMNS = ["date", "name", "email", "title", "company", "status", "billable", "amount", "reason"]
