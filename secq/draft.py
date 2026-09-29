"""Drafts answers in batches and checks each one against the sources it cites."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from .docs import LibraryAnswer, Passage
from .search import normalize, tokens
from .sheet import Question

SYSTEM = """You complete security questionnaires on behalf of {company}. Write in the company's voice ("we").

Rules:
- Use only the sources listed under each question. Never claim a control, certification, number, frequency, tool or vendor the sources do not state.
- answer: "Yes", "No", "Partial" or "N/A" for yes/no questions; otherwise a short direct answer.
- explanation: 1-3 plain sentences a security reviewer would accept, based on the sources.
- sources: the ids you used, e.g. ["S3-1", "L3-1"].
- confidence: "high" when a source states it directly, "medium" when it follows clearly from the sources, "low" otherwise.
- needs_review: true when confidence is not high, when the question asks for a commitment, contract term or legal position, or when sources conflict. Say why in review_note.
- If no source covers the question: answer "", explanation "", sources [], confidence "low", needs_review true, review_note "Not covered by the documents provided".
- Where a previously approved answer (L ids) matches the question, reuse its wording.
- Reply with one JSON object only: {{"answers": [one object per question id]}}"""

SCHEMA_HINT = """{"answers": [{"id": "<question id, e.g. Q3>", "answer": "<Yes|No|Partial|N/A|short answer>", "explanation": "<1-3 sentences>", "sources": ["<source id>"], "confidence": "<high|medium|low>", "needs_review": <true|false>, "review_note": "<why, or empty>"}]}"""

# Claims that must appear in a cited source before we accept them.
CLAIM_TERMS = ["soc2", "iso27001", "hipaa", "pci", "gdpr", "ccpa", "fedramp", "hitrust", "aes", "tls", "sso", "mfa",
               "saml", "pentest", "bug bounty", "vulnscan", "24/7", "encrypt", "backup", "bcdr", "logging",
               "backgroundcheck", "subprocessor"]


class Drafted(BaseModel):
    id: str = ""
    answer: str = ""
    explanation: str = ""
    sources: list[str] = Field(default_factory=list)
    confidence: str = "low"
    needs_review: bool = True
    review_note: str = ""

    @field_validator("id", "answer", "explanation", "review_note", mode="before")
    @classmethod
    def _as_str(cls, v):
        return "" if v is None else str(v)

    @field_validator("sources", mode="before")
    @classmethod
    def _as_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in re.split(r"[,;\s]+", v) if s.strip()]
        return [str(s).strip() for s in v]

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        v = str(v or "").strip().lower()
        return v if v in ("high", "medium", "low") else "low"


class DraftBatch(BaseModel):
    answers: list[Drafted] = Field(default_factory=list)

    @field_validator("answers", mode="before")
    @classmethod
    def _list(cls, v):
        return [a for a in v if isinstance(a, dict)] if isinstance(v, list) else []


@dataclass
class Evidence:
    """The sources offered for one question, keyed by the ids shown to the model."""

    passages: dict[str, Passage] = field(default_factory=dict)
    library: dict[str, LibraryAnswer] = field(default_factory=dict)

    def text(self, sid: str) -> str:
        if sid in self.passages:
            return self.passages[sid].text
        if sid in self.library:
            a = self.library[sid]
            return f"{a.question} {a.answer}"
        return ""

    def label(self, sid: str) -> str:
        if sid in self.passages:
            return self.passages[sid].source
        if sid in self.library:
            return f"past answer: {self.library[sid].source}"
        return sid


@dataclass
class DraftResult:
    key: str
    sheet: str
    row: int
    qid: str
    section: str
    question: str
    answer: str = ""
    explanation: str = ""
    confidence: str = "low"
    needs_review: bool = True
    review_note: str = ""
    sources: list[str] = field(default_factory=list)
    source_labels: list[str] = field(default_factory=list)


def build_prompt(items: list[tuple[str, Question, Evidence]]) -> str:
    blocks = []
    for pid, q, ev in items:
        lines = [f"### {pid}"]
        if q.section:
            lines.append(f"Section: {q.section}")
        lines.append(f"Question: {q.text}")
        lines.append("Sources:" if ev.passages else "Sources: none found")
        lines += [f"[{sid}] ({p.source}) {p.text}" for sid, p in ev.passages.items()]
        if ev.library:
            lines.append("Previously approved answers:")
            lines += [f"[{sid}] Q: {a.question} | A: {a.answer}" for sid, a in ev.library.items()]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"


def _unsupported(text: str, cited: str) -> list[str]:
    """Numbers and security claims in the answer that the cited sources do not contain."""
    said, src = normalize(text), normalize(cited)
    missing = [n for n in re.findall(r"\d+(?:\.\d+)?", said) if n not in src]
    missing += [t for t in CLAIM_TERMS if re.search(rf"\b{re.escape(t)}", said) and not re.search(rf"\b{re.escape(t)}", src)]
    return list(dict.fromkeys(missing))


# Words too generic to show that a source is about the question.
GENERIC = {"data", "customer", "customers", "security", "information", "company", "policy", "policies", "process",
           "processes", "system", "systems", "use", "used", "using", "current", "ensure", "all", "least", "carry",
           "employees", "organization", "organisation", "service", "services"}


def _stem(w: str) -> str:
    return w[:5] if len(w) >= 5 else w


def addresses_question(question: str, cited: str) -> bool:
    """True if the cited sources share at least one meaningful word with the question."""
    q = {_stem(w) for w in tokens(question) if w not in GENERIC and not w.isdigit()}
    src = {_stem(w) for w in tokens(cited)}
    return not q or bool(q & src)


def check(d: Drafted, q: Question, ev: Evidence) -> DraftResult:
    """Deterministic checks on one drafted answer; anything doubtful is flagged for review."""
    r = DraftResult(key=q.key, sheet=q.sheet, row=q.row, qid=q.qid, section=q.section, question=q.text,
                    answer=d.answer.strip(), explanation=d.explanation.strip(), confidence=d.confidence,
                    needs_review=d.needs_review, review_note=d.review_note.strip())
    notes = [r.review_note] if r.review_note else []
    valid = [s for s in dict.fromkeys(d.sources) if s in ev.passages or s in ev.library]
    if len(valid) < len(set(d.sources)):
        notes.append("cited a source it was not given")
    r.sources = valid
    r.source_labels = list(dict.fromkeys(ev.label(s) for s in valid))
    has_content = bool(r.answer or r.explanation)
    if has_content and not valid:
        r.confidence = "low"
        notes.append("no valid source cited")
    if has_content and valid:
        cited = " ".join(ev.text(s) for s in valid)
        missing = _unsupported(f"{r.answer} {r.explanation}", cited)
        if re.match(r"\s*(yes|partial)\b", r.answer, re.I):
            # a "Yes" also vouches for the question's own numbers and certifications ("at least $5M", "ISO 27001")
            missing += [m for m in _unsupported(q.text, cited) if m not in missing]
        if missing:
            r.confidence = "low"
            notes.append("mentions " + ", ".join(missing) + " not found in the cited sources")
        if not addresses_question(f"{q.section} {q.text}", cited):
            r.confidence = "low"
            notes.append("the cited sources do not seem to be about this question")
    if not has_content and not notes:
        notes.append("Not covered by the documents provided")
    if r.confidence != "high" or not has_content:
        r.needs_review = True
    r.review_note = "; ".join(dict.fromkeys(n for n in notes if n))
    return r


def missing_result(q: Question, note: str) -> DraftResult:
    return DraftResult(key=q.key, sheet=q.sheet, row=q.row, qid=q.qid, section=q.section, question=q.text,
                       needs_review=True, review_note=note)
