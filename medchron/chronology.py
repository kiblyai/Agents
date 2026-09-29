"""Turns checked extractions into the chronology: merge duplicates, sort, find treatment gaps, total bills, and
build the checking queue."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from .extract import Bill, Entry, name_tokens, words
from .records import CaseFile

# Words that don't tell two visit types apart.
VISIT_GENERIC = {"visit", "encounter", "note", "notes", "report", "follow", "up", "office", "evaluation", "eval",
                 "exam", "consult", "consultation", "progress", "daily", "record"}


PLACE_FILLER = {"the", "of", "and", "llc", "inc", "pc", "pllc", "ed", "er", "dept", "department"}


def same_person(a: str, b: str) -> bool:
    """"Priya Raman, MD", "Dr. Raman" and "P. Raman" match; "Mark Chen" and "Mark Jones" don't."""
    ta, tb = name_tokens(a), name_tokens(b)
    return bool(ta and tb) and len(ta & tb) >= min(len(ta), len(tb), 2)


def same_place(a: str, b: str) -> bool:
    """"Maple Falls General Hospital" matches "Maple Falls General Hospital ED" but not "Maple Falls Physical Therapy"."""
    ta, tb = words(a) - PLACE_FILLER, words(b) - PLACE_FILLER
    return bool(ta and tb) and (ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.75)


def same_encounter(a: Entry, b: Entry) -> bool:
    """Same date and same clinician (or, when one side has no clinician, same facility and a compatible visit type)."""
    if not a.date or a.date != b.date:
        return False
    if name_tokens(a.provider) and name_tokens(b.provider):
        return same_person(a.provider, b.provider)
    va, vb = words(a.visit_type) - VISIT_GENERIC, words(b.visit_type) - VISIT_GENERIC
    return same_place(a.facility, b.facility) and (not va or not vb or bool(va & vb))


def _pick(a: str, b: str, join: bool) -> str:
    if not a or b.lower() in a.lower():
        return a or b
    if a.lower() in b.lower():
        return b
    return f"{a}; {b}" if join else max(a, b, key=len)


def merge_into(a: Entry, b: Entry, page_file: dict[int, str]) -> None:
    """Fold b into a. A copy from another file is a duplicate record; pages from the same file continue the visit."""
    duplicate = not ({page_file.get(p) for p in a.pages} & {page_file.get(p) for p in b.pages})
    for name in ("provider", "facility", "visit_type", "complaints", "findings", "treatment", "work_status"):
        setattr(a, name, _pick(getattr(a, name), getattr(b, name), join=not duplicate and name not in
                               ("provider", "facility", "visit_type")))
    seen = {(d["code"] or d["description"]).lower() for d in a.diagnoses}
    for d in b.diagnoses:
        if (d["code"] or d["description"]).lower() not in seen:
            a.diagnoses.append(d)
            seen.add((d["code"] or d["description"]).lower())
    if duplicate:
        a.duplicate_pages = sorted(set(a.duplicate_pages) | set(b.pages) | set(b.duplicate_pages))
    else:
        a.pages = sorted(set(a.pages) | set(b.pages))
        a.duplicate_pages = sorted(set(a.duplicate_pages) | set(b.duplicate_pages))
    order = {"low": 0, "medium": 1, "high": 2}
    a.confidence = min(a.confidence, b.confidence, key=lambda c: order.get(c, 0))
    a.needs_review = a.needs_review or b.needs_review
    a.review_note = "; ".join(dict.fromkeys(n for n in (a.review_note, b.review_note) if n))


def merge_entries(entries: list[Entry], page_file: dict[int, str]) -> list[Entry]:
    """Merge duplicate and split records of the same encounter, then sort by date (undated entries last)."""
    ordered = sorted(entries, key=lambda e: (e.date or "9999", e.pages[:1] or [0]))
    merged: list[Entry] = []
    for e in ordered:
        match = next((m for m in merged if same_encounter(m, e)), None)
        if match:
            merge_into(match, e, page_file)
        else:
            merged.append(e)
    return sorted(merged, key=lambda e: (e.date or "9999", e.pages[:1] or [0]))


def merge_bills(bills: list[Bill], page_file: dict[int, str]) -> list[Bill]:
    """Drop the second copy of a charge that appears in another file. Two identical lines in the same file can be
    real (units billed separately), so those stay, flagged when they sit on different pages."""
    out: list[Bill] = []
    for b in sorted(bills, key=lambda x: (x.date or "9999", x.pages[:1] or [0])):
        key = (b.date, b.code or b.description.lower(), b.charge)
        twin = next((o for o in out if (o.date, o.code or o.description.lower(), o.charge) == key and b.charge), None)
        if twin is None:
            out.append(b)
            continue
        files_a, files_b = {page_file.get(p) for p in twin.pages}, {page_file.get(p) for p in b.pages}
        if not files_a & files_b:
            continue  # a copy of the same bill from another file
        if set(twin.pages) != set(b.pages):
            b.needs_review = True
            b.review_note = "; ".join(filter(None, [b.review_note, f"same charge as on page {twin.pages[0]}: "
                                                                  "duplicate or a second unit?"]))
        out.append(b)
    return out


@dataclass
class Gap:
    start: str
    end: str
    days: int
    before: str
    after: str


def _who(e: Entry) -> str:
    return " - ".join(x for x in (e.facility, e.provider) if x) or e.visit_type


def find_gaps(entries: list[Entry], gap_days: int) -> list[Gap]:
    """Spells of more than gap_days with no treatment, after the injury."""
    dated = [e for e in entries if e.date and not e.pre_incident]
    gaps = []
    for a, b in zip(dated, dated[1:]):
        days = (date.fromisoformat(b.date) - date.fromisoformat(a.date)).days
        if days > gap_days:
            gaps.append(Gap(start=a.date, end=b.date, days=days, before=_who(a), after=_who(b)))
    return gaps


def _group_label(labels: list[str], name: str, same) -> str:
    """The first label seen for this facility or person, so small spelling differences share one row."""
    label = next((x for x in labels if same(x, name)), None)
    if label is None:
        label = name or "(unknown)"
        labels.append(label)
    return label


def provider_summary(entries: list[Entry]) -> list[dict]:
    labels: list[str] = []
    rows: dict[str, dict] = {}
    for e in entries:
        if e.pre_incident:  # listed on their own in the summary
            continue
        label = _group_label(labels, e.facility, same_place) if e.facility else \
            _group_label(labels, e.provider, same_person)
        r = rows.setdefault(label, {"provider": label, "first": "", "last": "", "visits": 0})
        r["visits"] += 1
        if e.date:
            r["first"] = min(filter(None, [r["first"], e.date]))
            r["last"] = max(r["last"], e.date)
    return sorted(rows.values(), key=lambda r: r["first"] or "9999")


def bill_totals(bills: list[Bill]) -> list[dict]:
    labels: list[str] = []
    rows: dict[str, dict] = {}
    for b in bills:
        label = _group_label(labels, b.provider, same_place)
        r = rows.setdefault(label, {"provider": label, "lines": 0, "total": 0.0, "to_check": 0})
        r["lines"] += 1
        r["total"] = round(r["total"] + (b.charge or 0.0), 2)
        r["to_check"] += int(b.needs_review)
    return sorted(rows.values(), key=lambda r: -r["total"])


def page_range(pages: list[int]) -> str:
    """[3, 4, 5, 9] -> "3-5, 9"."""
    out, run = [], []
    for p in sorted(set(pages)):
        if run and p != run[-1] + 1:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
            run = []
        run.append(p)
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ", ".join(out)


@dataclass
class Chronology:
    entries: list[Entry] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)
    bill_totals: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    days_to_first_treatment: int | None = None

    @property
    def total_billed(self) -> float:
        return round(sum(b.charge or 0.0 for b in self.bills), 2)


REVIEW_COLUMNS = ["kind", "date", "pages", "what", "note"]


def build(entries: list[Entry], bills: list[Bill], case: CaseFile, *, doi: date | None, gap_days: int = 30,
          spot_check: float = 0.10, no_content: set[int] = frozenset(), other_patient: set[int] = frozenset(),
          not_processed: set[int] = frozenset()) -> Chronology:
    page_file = {p.n: p.file for p in case.pages}
    c = Chronology()
    c.entries = merge_entries(entries, page_file)
    c.bills = merge_bills(bills, page_file)
    c.gaps = find_gaps(c.entries, gap_days)
    c.providers = provider_summary(c.entries)
    c.bill_totals = bill_totals(c.bills)
    first = next((e.date for e in c.entries if e.date and not e.pre_incident), "")
    if doi and first:
        c.days_to_first_treatment = (date.fromisoformat(first) - doi).days

    used: dict[int, str] = {}
    for e in c.entries:
        for p in e.pages + e.duplicate_pages:
            used.setdefault(p, f"{e.date or 'undated'} {e.visit_type}".strip())
    for b in c.bills:
        for p in b.pages:
            used.setdefault(p, "bill")

    review: list[dict] = []
    page_issues: list[dict] = []
    for e in c.entries:
        if e.needs_review:
            review.append({"kind": "entry", "date": e.date, "pages": page_range(e.pages), "what": _who(e),
                           "note": e.review_note or f"{e.confidence} confidence"})
    for b in c.bills:
        if b.needs_review:
            review.append({"kind": "bill", "date": b.date, "pages": page_range(b.pages),
                           "what": f"{b.provider} {b.code} {b.description}".strip(), "note": b.review_note})
    for p in case.pages:
        status = ""
        if p.needs_ocr:
            status = "no text on this page (a scan?): re-run with --ocr or read it yourself"
        elif p.n in not_processed:
            status = "not processed yet: re-run to continue"
        elif p.n in other_patient:
            status = "the model says this page is about another patient"
        elif p.n not in used and p.n not in no_content:
            status = "no entry or bill uses this page: check for anything missed"
        c.pages.append({"page": p.n, "file": p.file, "file_page": p.file_page, "characters": len(p.text.strip()),
                        "used_by": used.get(p.n, "no content" if p.n in no_content else ""), "check": status})
        if not status:
            continue
        last = page_issues[-1] if page_issues else None
        if last and last["note"] == status and last["file"] == p.file and last["to"] == p.n - 1:
            last["to"] = p.n  # one row for a run of pages with the same problem
        else:
            page_issues.append({"from": p.n, "to": p.n, "file": p.file, "file_page": p.file_page, "note": status})
    for g in page_issues:
        span = f"{g['from']}-{g['to']}" if g["to"] > g["from"] else str(g["from"])
        what = f"{g['file']} p.{g['file_page']}" + (f"-{g['file_page'] + g['to'] - g['from']}" if g["to"] > g["from"] else "")
        review.append({"kind": "page", "date": "", "pages": span, "what": what, "note": g["note"]})

    clean = [e for e in c.entries if not e.needs_review]
    rng = random.Random("|".join(f"{e.date}{e.pages}" for e in clean))
    for e in sorted(rng.sample(clean, min(len(clean), max(1, round(spot_check * len(clean))))) if clean else [],
                    key=lambda x: x.date):
        review.append({"kind": "spot check", "date": e.date, "pages": page_range(e.pages), "what": _who(e),
                       "note": "random sample of confident entries: compare every field with the page"})
    c.review = review
    return c
