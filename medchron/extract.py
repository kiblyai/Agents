"""Extracts encounters and bill lines from a batch of record pages, and checks each against the pages it cites."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field, field_validator

from .records import Page

SYSTEM = """You build medical chronologies for personal-injury law firms. You read pages of a patient's medical records and extract every encounter and every billed charge.{patient_line}

Rules:
- Use only what the pages say. Never add a diagnosis, ICD-10 code, date, provider, dose or amount that is not written on the pages.
- One entry per encounter (office visit, emergency visit, admission, therapy session, imaging study, test, procedure). An encounter that runs over several pages is one entry citing all of them.
- date: the date of service as YYYY-MM-DD. Not the date of birth, a print, fax or signature date, or the date of injury mentioned in the history.
- provider: the treating clinician as written, e.g. "Priya Raman, MD". facility: the hospital or practice.
- visit_type: a few words, e.g. "Emergency department visit", "Physical therapy", "MRI cervical spine", "Office visit".
- complaints: what the patient reported, with pain scores. findings: exam, imaging and test results. diagnoses: as written, each with its ICD-10 code only if the page shows one. treatment: procedures, medications with doses, referrals, orders. work_status: time off or work restrictions as written.
- Keep fields short: plain phrases, numbers exactly as written. Use "" when the pages don't say.
- pages: the page numbers the entry comes from.
- bills: one line per charge on a bill, itemized statement or claim form: date of service, provider or facility, CPT/HCPCS code, description, charge as a number. Skip totals, payments, adjustments and balances, and never add anything up.
- confidence: "high" when the pages state it clearly, "medium" when you had to interpret the layout, "low" when unsure.
- no_content_pages: pages with no encounter and no charge (fax covers, authorizations, certifications, blank pages).
- other_patient_pages: pages about a different patient.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"entries": [{"date": "<YYYY-MM-DD>", "provider": "<name, credentials>", "facility": "<hospital or practice>", "visit_type": "<a few words>", "complaints": "<...>", "findings": "<...>", "diagnoses": [{"code": "<ICD-10 code or empty>", "description": "<as written>"}], "treatment": "<...>", "work_status": "<...>", "pages": [<page number>], "confidence": "<high|medium|low>"}], "bills": [{"date": "<YYYY-MM-DD>", "provider": "<...>", "code": "<CPT/HCPCS or empty>", "description": "<...>", "charge": <number>, "pages": [<page number>]}], "no_content_pages": [<page number>], "other_patient_pages": [<page number>]}"""


def system_prompt(patient: str = "") -> str:
    return SYSTEM.format(patient_line=f" The patient is {patient}." if patient else "")


def build_prompt(pages: list[Page], context: Page | None = None) -> str:
    blocks = []
    if context is not None:
        text = context.text if len(context.text) <= 3000 else context.text[:1500] + "\n[...]\n" + context.text[-1500:]
        blocks.append(f"### Page {context.n} (context: the page before this batch, already processed. Use it only for an "
                      f"encounter that continues onto the pages below.)\n{text.strip()}")
    blocks += [f"### Page {p.n}\n{p.text.strip()}" for p in pages]
    return "\n\n".join(blocks) + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"


# --- tolerant reply schema ----------------------------------------------------------------------------------------

def _pages(v) -> list[int]:
    if v is None:
        return []
    items = v if isinstance(v, list) else re.split(r"[,;]", str(v))
    out: list[int] = []
    for item in items:
        s = str(item)
        span = re.search(r"(\d+)\s*[-\u2013]\s*(\d+)", s)
        if span and 0 < int(span.group(2)) - int(span.group(1)) <= 50:
            out += list(range(int(span.group(1)), int(span.group(2)) + 1))
        else:
            out += [int(n) for n in re.findall(r"\d+", s)[:1]]
    return list(dict.fromkeys(out))


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    return str(v).strip()


class Diagnosis(BaseModel):
    code: str = ""
    description: str = ""

    @field_validator("code", "description", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


class ExtractedEntry(BaseModel):
    date: str = ""
    provider: str = ""
    facility: str = ""
    visit_type: str = ""
    complaints: str = ""
    findings: str = ""
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    treatment: str = ""
    work_status: str = ""
    pages: list[int] = Field(default_factory=list)
    confidence: str = "low"

    @field_validator("date", "provider", "facility", "visit_type", "complaints", "findings", "treatment",
                     "work_status", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("diagnoses", mode="before")
    @classmethod
    def _dx(cls, v):
        if not isinstance(v, list):
            v = [v] if v else []
        out = []
        for d in v:
            if isinstance(d, dict):
                out.append(d)
            elif isinstance(d, str) and d.strip():  # "S13.4XXA Sprain of ..." or just a description
                m = re.match(rf"\s*({ICD_PATTERN})\b[\s:\-\u2013]*(.*)", d.strip())
                out.append({"code": m.group(1), "description": m.group(2)} if m else {"description": d.strip()})
        return out

    @field_validator("pages", mode="before")
    @classmethod
    def _p(cls, v):
        return _pages(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        v = str(v or "").strip().lower()
        return v if v in ("high", "medium", "low") else "low"


class ExtractedBill(BaseModel):
    date: str = ""
    provider: str = ""
    code: str = ""
    description: str = ""
    charge: float | None = None
    pages: list[int] = Field(default_factory=list)

    @field_validator("date", "provider", "code", "description", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("charge", mode="before")
    @classmethod
    def _amount(cls, v):
        if isinstance(v, (int, float)):
            return float(v)
        m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v or ""))
        return float(m.group(0).replace(",", "")) if m else None

    @field_validator("pages", mode="before")
    @classmethod
    def _p(cls, v):
        return _pages(v)


class PageBatch(BaseModel):
    entries: list[ExtractedEntry] = Field(default_factory=list)
    bills: list[ExtractedBill] = Field(default_factory=list)
    no_content_pages: list[int] = Field(default_factory=list)
    other_patient_pages: list[int] = Field(default_factory=list)

    @field_validator("entries", "bills", mode="before")
    @classmethod
    def _list(cls, v):
        return [a for a in v if isinstance(a, dict)] if isinstance(v, list) else []

    @field_validator("no_content_pages", "other_patient_pages", mode="before")
    @classmethod
    def _p(cls, v):
        return _pages(v)


# --- reading dates, numbers and codes from page text ---------------------------------------------------------------

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12}
MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|"
       r"nov(?:ember)?|dec(?:ember)?)\.?")
DATE_PATTERNS = [
    ("ymd", re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")),
    ("mdy", re.compile(r"(?<![\d/.-])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/-])")),
    ("mdy", re.compile(r"(?<![\d/.-])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.])")),
    ("Mdy", re.compile(rf"\b{MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)),
    ("dMy", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s-]+{MON}[\s,-]+(\d{{4}}|\d{{2}})\b", re.I)),
]


def _year(y: int, today: date) -> int:
    if y >= 100:
        return y
    return 2000 + y if 2000 + y <= today.year + 1 else 1900 + y


def find_dates(text: str, today: date | None = None) -> set[date]:
    """Every date written on a page, in the formats US records use (numeric dates are read month first)."""
    return {d for _, d in _dates_at(text, today)}


def _dates_at(text: str, today: date | None = None) -> list[tuple[int, date]]:
    today = today or date.today()
    found: list[tuple[int, date]] = []
    for kind, rx in DATE_PATTERNS:
        for m in rx.finditer(text):
            g = m.groups()
            try:
                if kind == "ymd":
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                elif kind == "mdy":
                    mo, d, y = int(g[0]), int(g[1]), _year(int(g[2]), today)
                    if mo > 12 >= d:  # day first
                        mo, d = d, mo
                elif kind == "Mdy":
                    mo, d, y = MONTHS[g[0][:3].lower()], int(g[1]), int(g[2])
                else:
                    d, mo, y = int(g[0]), MONTHS[g[1][:3].lower()], _year(int(g[2]), today)
                found.append((m.start(), date(y, mo, d)))
            except (ValueError, KeyError):
                continue
    return found


# Labels that introduce the date of a visit (not a birth, fax, print or statement date).
SERVICE_LABEL = re.compile(
    r"\b(?:date of (?:service|visit|procedure|exam(?:ination)?|admission|discharge|encounter|surgery)|"
    r"(?:service|visit|exam|encounter|admit|admission|discharge|procedure|appointment) date|dos)\b\s*[:#-]?"
    r"|^\s*date\s*:", re.I | re.M)


def service_dates(text: str, today: date | None = None) -> set[date]:
    """Dates written right after a date-of-service label, e.g. "Date of visit: 04/15/2025" or "DOS 3/24/25".
    ("Seen on" is left out: later notes use it for earlier visits.)"""
    out: set[date] = set()
    for m in SERVICE_LABEL.finditer(text):
        found = _dates_at(text[m.end():].split("\n", 1)[0][:30], today)  # the first date on the label's line
        if found:
            out.add(min(found)[1])
    return out


def parse_date(s: str, today: date | None = None) -> date | None:
    """The model's date (YYYY-MM-DD, or any single written date)."""
    s = (s or "").strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    found = find_dates(s, today)
    return found.pop() if len(found) == 1 else None


NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def norm_number(s: str) -> str:
    whole, _, frac = s.replace(",", "").partition(".")
    whole, frac = whole.lstrip("0") or "0", frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def numbers(text: str, today: date | None = None) -> set[str]:
    """Numbers on a page, normalized ("1,450.00" -> "1450", "03" -> "3"), plus the parts of every date on it,
    so "2025-03-17" in an entry matches "March 17, 2025" on the page."""
    out = {norm_number(n) for n in NUM_RE.findall(text)}
    for d in find_dates(text, today):
        out |= {str(d.year), str(d.month), str(d.day)}
    return out


ICD_PATTERN = r"[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?"
ICD_RE = re.compile(rf"\b{ICD_PATTERN}\b")
CPT_RE = re.compile(r"\b(?:\d{4}[0-9A-Z]|[A-Z]\d{4})\b")


def icd_codes(text: str) -> set[str]:
    return {c.replace(".", "") for c in ICD_RE.findall(text.upper())}


# Words that say nothing about who a provider is.
TITLE_WORDS = {"dr", "md", "do", "dpt", "pt", "np", "pa", "rn", "dc", "phd", "mbbs", "facs", "fnp", "aprn", "mpt",
               "atc", "otr", "ot", "lpn", "crna", "dds", "od", "mr", "mrs", "ms", "jr", "sr", "ii", "iii"}
FACILITY_WORDS = {"hospital", "medical", "center", "centre", "clinic", "health", "healthcare", "associates", "group",
                  "the", "of", "and", "llc", "inc", "pc", "pllc", "regional", "general", "care", "services", "department",
                  "dept", "practice", "family", "medicine", "physicians", "partners", "institute", "st", "saint"}


def name_tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) >= 2 and w not in TITLE_WORDS | FACILITY_WORDS}


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


# --- checked results ------------------------------------------------------------------------------------------------

@dataclass
class CheckContext:
    patient: str = ""
    doi: date | None = None
    today: date = field(default_factory=date.today)


@dataclass
class Entry:
    date: str = ""  # YYYY-MM-DD; "" when unknown
    provider: str = ""
    facility: str = ""
    visit_type: str = ""
    complaints: str = ""
    findings: str = ""
    diagnoses: list[dict] = field(default_factory=list)  # [{"code": "S13.4XXA", "description": "..."}]
    treatment: str = ""
    work_status: str = ""
    pages: list[int] = field(default_factory=list)
    confidence: str = "low"
    needs_review: bool = True
    review_note: str = ""
    pre_incident: bool = False
    duplicate_pages: list[int] = field(default_factory=list)  # pages of merged duplicate copies of this record

    def diagnosis_text(self) -> str:
        return "; ".join(f"{d['code']} {d['description']}".strip() for d in self.diagnoses)


@dataclass
class Bill:
    date: str = ""
    provider: str = ""
    code: str = ""
    description: str = ""
    charge: float | None = None
    pages: list[int] = field(default_factory=list)
    needs_review: bool = False
    review_note: str = ""


def _valid_pages(cited: list[int], allowed: set[int], notes: list[str]) -> list[int]:
    pages = sorted({p for p in cited if p in allowed})
    if len(pages) < len(set(cited)):
        notes.append("cited a page it was not given")
    if not pages:
        notes.append("no valid page cited")
    return pages


def _check_date(raw: str, cited: str, pages: list[int], ctx: CheckContext, notes: list[str],
                labeled: bool = False) -> str:
    d = parse_date(raw, ctx.today)
    if d is None:
        notes.append(f"unreadable date '{raw}'" if raw else "no date of service")
        return ""
    if pages and d not in find_dates(cited, ctx.today):
        notes.append(f"date {d} not found on the cited pages")
    elif pages and labeled:
        # the date of injury in the history, a birth date or a fax date is on the page too; the visit's own label wins
        shown = service_dates(cited, ctx.today)
        if shown and d not in shown:
            notes.append(f"the cited pages give the date of service as {', '.join(sorted(map(str, shown)))}, not {d}")
    if d > ctx.today:
        notes.append(f"date {d} is in the future")
    return d.isoformat()


def check_entry(e: ExtractedEntry, texts: dict[int, str], allowed: set[int], ctx: CheckContext) -> Entry:
    """Deterministic checks on one extracted encounter; anything doubtful goes to review."""
    notes: list[str] = []
    pages = _valid_pages(e.pages, allowed, notes)
    cited = "\n".join(texts[p] for p in pages)
    r = Entry(provider=e.provider, facility=e.facility, visit_type=e.visit_type, complaints=e.complaints,
              findings=e.findings, treatment=e.treatment, work_status=e.work_status, pages=pages,
              confidence=e.confidence)
    r.date = _check_date(e.date, cited, pages, ctx, notes, labeled=True)

    on_page = icd_codes(cited)
    for dx in e.diagnoses:
        code = re.sub(r"[\s.]", "", dx.code.upper())
        if code and code not in on_page:
            notes.append(f"removed ICD code {dx.code} (not on the cited pages)")
            code = ""
        if code or dx.description:
            shown = dx.code.upper().replace(" ", "") if code else ""
            r.diagnoses.append({"code": shown, "description": dx.description})

    if pages:
        said = " ".join([r.visit_type, r.complaints, r.findings, r.treatment, r.work_status,
                         *(d["description"] for d in r.diagnoses)])
        page_numbers = numbers(cited, ctx.today)
        missing = [n for n in dict.fromkeys(norm_number(x) for x in NUM_RE.findall(said)) if n not in page_numbers]
        if missing:
            notes.append("mentions " + ", ".join(missing[:6]) + " not found on the cited pages")
        page_words = words(cited)
        if not name_tokens(r.provider) and not name_tokens(r.facility):
            notes.append("no provider or facility")
        for label, value in (("provider", r.provider), ("facility", r.facility)):
            toks = name_tokens(value)
            if toks and not toks & page_words:
                notes.append(f"{label} '{value}' not found on the cited pages")
        name = [w for w in re.findall(r"[a-z]+", ctx.patient.lower()) if w not in TITLE_WORDS]
        last = name[-1] if name else ""  # the surname, skipping "Jr." and the like
        if last and last not in page_words:
            notes.append("patient's name not on the cited pages (another patient's record?)")

    d = parse_date(r.date) if r.date else None
    r.pre_incident = bool(d and ctx.doi and d < ctx.doi)
    if notes:
        r.confidence = "low"
    r.needs_review = r.confidence != "high" or bool(notes)
    r.review_note = "; ".join(dict.fromkeys(notes))
    return r


def check_bill(b: ExtractedBill, texts: dict[int, str], allowed: set[int], ctx: CheckContext) -> Bill:
    notes: list[str] = []
    pages = _valid_pages(b.pages, allowed, notes)
    cited = "\n".join(texts[p] for p in pages)
    r = Bill(provider=b.provider, description=b.description, charge=b.charge, pages=pages)
    r.date = _check_date(b.date, cited, pages, ctx, notes)
    m = CPT_RE.search(b.code.upper())
    code = m.group(0) if m else b.code.strip().upper()
    if code and pages and not re.search(rf"\b{re.escape(code)}\b", cited.upper()):
        notes.append(f"removed code {b.code} (not on the cited pages)")
        code = ""
    r.code = code
    if b.charge is None:
        notes.append("no charge amount")
    elif pages and norm_number(f"{b.charge:.2f}") not in numbers(cited, ctx.today):
        notes.append(f"charge {b.charge:,.2f} not found on the cited pages")
    r.needs_review = bool(notes)
    r.review_note = "; ".join(dict.fromkeys(notes))
    return r
