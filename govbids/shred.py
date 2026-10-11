"""Shreds an RFP into a compliance matrix: code finds every requirement sentence (word for word); the model only
labels them. Key facts (due date, page limits, submission) must quote the RFP."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import find_dates, has_amount, parse_date, quote_found

REQ_WORDS = re.compile(r"\b(?:shall|must|will|is required|are required|should|may not|is responsible|"
                       r"are responsible)\b", re.I)
SECTION_HEAD = re.compile(r"^\s*SECTION\s+([A-M])\b", re.I)
ITEM_START = re.compile(r"^\s*([A-M]\.\d+(?:\.\d+)*)\.?\s+(.*)$")
TYPES = ["technical", "management", "staffing", "security", "reporting", "schedule", "instruction", "format",
         "past performance", "pricing", "evaluation", "government", "other"]


@dataclass
class Requirement:
    id: str
    source: str  # document and page, e.g. "RFP.pdf p.2"
    section: str  # "C.3", or "Section L" when the paragraph has no number
    text: str  # the sentence, word for word
    type: str = ""
    volume: str = ""
    summary: str = ""
    offeror: bool = True
    checks: list[str] = field(default_factory=list)


def find_requirements(docs: list[tuple[str, str]]) -> list[Requirement]:
    """Every sentence with shall, must, will, is required and the like, with its section number and page."""
    out: list[Requirement] = []
    letter = ""
    for source, text in docs:
        blocks: list[tuple[str, list[str]]] = []
        for line in text.splitlines():
            if SECTION_HEAD.match(line):
                letter = SECTION_HEAD.match(line).group(1).upper()
                blocks.append((f"Section {letter}", []))
                continue
            m = ITEM_START.match(line)
            if m:
                blocks.append((m.group(1), [m.group(2)]))
            elif blocks:
                blocks[-1][1].append(line.strip())
            elif line.strip():
                blocks.append((f"Section {letter}" if letter else "", [line.strip()]))
        for section, lines in blocks:
            body = re.sub(r"\s+", " ", " ".join(lines)).strip()
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", body):
                if REQ_WORDS.search(sentence) and len(sentence.split()) >= 5:
                    out.append(Requirement(f"R{len(out) + 1}", source, section, sentence.strip()))
    return out


def volume_lines(docs: list[tuple[str, str]]) -> str:
    """The RFP's own lines about proposal volumes, to help label which volume answers what."""
    lines = [ln.strip() for _, t in docs for ln in t.splitlines() if re.search(r"\bvolume\b", ln, re.I)]
    return "\n".join(dict.fromkeys(lines))[:3000]


# --- labeling requirements ----------------------------------------------------------------------------------------

LABEL_SYSTEM = f"""You label requirements from a US federal RFP for a proposal compliance matrix.

For each requirement id:
- type: one of {", ".join(TYPES)}.
- volume: the proposal volume that must answer it, using the RFP's own volume names, or "".
- summary: at most 12 plain words.
- offeror: true when the offeror must do or provide something; false for what the Government will do or how it will evaluate.
Reply with one JSON object only."""

LABEL_HINT = """{"requirements": [{"id": "R1", "type": "<type>", "volume": "<volume or ''>", "summary": "<...>", "offeror": <true|false>}]}"""


def label_prompt(reqs: list[Requirement], volumes: str) -> str:
    items = "\n".join(f"[{r.id}] ({r.section}) {r.text}" for r in reqs)
    return (f"PROPOSAL VOLUMES (from the RFP)\n{volumes or '(not stated)'}\n\nREQUIREMENTS\n{items}\n\n"
            f"Return JSON in exactly this shape (replace every <...>):\n{LABEL_HINT}")


def _str(v) -> str:
    return "" if v is None else str(v).strip()


class Label(BaseModel):
    id: str = ""
    type: str = "other"
    volume: str = ""
    summary: str = ""
    offeror: bool = True

    @field_validator("id", "volume", "summary", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("type", mode="before")
    @classmethod
    def _t(cls, v):
        v = _str(v).lower().replace("_", " ")
        return v if v in TYPES else "other"

    @field_validator("offeror", mode="before")
    @classmethod
    def _b(cls, v):
        return v if isinstance(v, bool) else _str(v).lower() not in ("false", "no", "0", "government")


class Labels(BaseModel):
    requirements: list[Label] = Field(default_factory=list)

    @field_validator("requirements", mode="before")
    @classmethod
    def _l(cls, v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


GOVERNMENT_RE = re.compile(r"^\s*(?:the\s+)?government\s+(?:will|shall|may)\b", re.I)


def apply_labels(reqs: list[Requirement], labels: Labels) -> list[str]:
    """Put the labels on the requirements; the text never changes. Returns the ids the model skipped."""
    by_id = {x.id: x for x in labels.requirements}
    skipped = []
    for r in reqs:
        lab = by_id.get(r.id)
        if lab is None:
            skipped.append(r.id)
            r.checks.append("not labeled; label by hand")
            continue
        r.type, r.volume, r.summary, r.offeror = lab.type, lab.volume, lab.summary, lab.offeror
        if GOVERNMENT_RE.match(r.text) and r.offeror and r.type != "evaluation":
            r.checks.append("reads as a Government action, but labeled as the offeror's")
    return skipped


# --- key facts ----------------------------------------------------------------------------------------------------

FACTS_SYSTEM = """You pull the key facts a proposal manager needs from a US federal RFP.

Rules:
- Every fact needs a quote from the RFP, copied word for word. Use "" when the RFP doesn't say.
- Dates as YYYY-MM-DD; keep the time and time zone as written.
- page_limits: one entry per volume with a page limit.
- Reply with one JSON object only."""

FACTS_HINT = """{"due": {"date": "<YYYY-MM-DD>", "time": "<as written>", "quote": "<...>"}, "questions_due": {"date": "<YYYY-MM-DD>", "quote": "<...>"}, "submission": {"value": "<how and where to submit>", "quote": "<...>"}, "page_limits": [{"volume": "<...>", "pages": <number>, "quote": "<...>"}], "format": {"value": "<font, margins, page size>", "quote": "<...>"}, "evaluation": {"value": "<e.g. best value tradeoff or LPTA>", "quote": "<...>"}, "contract_type": {"value": "<...>", "quote": "<...>"}, "period_of_performance": {"value": "<...>", "quote": "<...>"}}"""


def facts_text(docs: list[tuple[str, str]], limit: int = 40000) -> str:
    """The cover pages and Sections L and M, where the key facts live (whole RFP when it is short)."""
    full = "\n\n".join(f"[{s}]\n{t}" for s, t in docs)
    if len(full) <= limit:
        return full
    keep = [f"[{s}]\n{t}" for s, t in docs[:3]]
    keep += [f"[{s}]\n{t}" for s, t in docs[3:] if re.search(r"SECTION\s+[LM]\b|INSTRUCTIONS|EVALUATION", t, re.I)]
    return "\n\n".join(keep)[:limit]


def facts_prompt(docs: list[tuple[str, str]]) -> str:
    return f"RFP TEXT\n{facts_text(docs)}\n\nReturn JSON in exactly this shape (replace every <...>):\n{FACTS_HINT}"


class Fact(BaseModel):
    value: str = ""
    date: str = ""
    time: str = ""
    quote: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


class PageLimit(BaseModel):
    volume: str = ""
    pages: int | None = None
    quote: str = ""

    @field_validator("volume", "quote", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("pages", mode="before")
    @classmethod
    def _n(cls, v):
        m = re.search(r"\d+", str(v or ""))
        return int(m.group(0)) if m else None


class Facts(BaseModel):
    due: Fact = Field(default_factory=Fact)
    questions_due: Fact = Field(default_factory=Fact)
    submission: Fact = Field(default_factory=Fact)
    page_limits: list[PageLimit] = Field(default_factory=list)
    format: Fact = Field(default_factory=Fact)
    evaluation: Fact = Field(default_factory=Fact)
    contract_type: Fact = Field(default_factory=Fact)
    period_of_performance: Fact = Field(default_factory=Fact)

    @field_validator("due", "questions_due", "submission", "format", "evaluation", "contract_type",
                     "period_of_performance", mode="before")
    @classmethod
    def _f(cls, v):
        return v if isinstance(v, dict) else {"value": _str(v)}

    @field_validator("page_limits", mode="before")
    @classmethod
    def _l(cls, v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def check_facts(f: Facts, text: str) -> tuple[list[dict], list[str]]:
    """(fact rows with what was verified, checks). A fact whose quote isn't in the RFP is dropped."""
    rows, checks = [], []

    def ok(fact_quote: str, name: str) -> bool:
        if not fact_quote:
            return False
        if not quote_found(fact_quote, text, 0.85):
            checks.append(f"{name} dropped: its quote is not in the RFP")
            return False
        return True

    for name, fact in (("Proposal due", f.due), ("Questions due", f.questions_due)):
        if not ok(fact.quote, name):
            rows.append({"fact": name, "value": "", "quote": "", "check": "not found: read the RFP"})
            continue
        d = parse_date(fact.date)
        if d is None or d not in find_dates(fact.quote):
            checks.append(f"{name}: the date {fact.date or '(none)'} is not the one in its quote")
            rows.append({"fact": name, "value": "", "quote": fact.quote, "check": "date doesn't match the quote"})
            continue
        rows.append({"fact": name, "value": " ".join(x for x in (d.isoformat(), fact.time) if x), "quote": fact.quote,
                     "check": ""})
    for name, fact in (("Submission", f.submission), ("Format", f.format), ("Evaluation", f.evaluation),
                       ("Contract type", f.contract_type), ("Period of performance", f.period_of_performance)):
        if fact.value and ok(fact.quote, name):
            rows.append({"fact": name, "value": fact.value, "quote": fact.quote, "check": ""})
    for pl in f.page_limits:
        if pl.pages is None or not ok(pl.quote, f"{pl.volume} page limit"):
            continue
        if not has_amount(pl.pages, pl.quote):
            checks.append(f"{pl.volume} page limit {pl.pages} is not the number in its quote")
            continue
        rows.append({"fact": f"Page limit: {pl.volume}", "value": f"{pl.pages} pages", "quote": pl.quote, "check": ""})
    return rows, checks
