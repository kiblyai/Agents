"""Drafts a letter of inquiry (or proposal sections) from the nonprofit's documents, then checks it in code."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import amounts, has_amount, unsupported_numbers, word_count, words
from agentkit.docs import chunk, document_files, read_document
from agentkit.search import BM25
from agentkit.tables import pick, read_table

from .funders import Funder
from .org import Org

PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|\bTBD\b|\bXX+\b|<[^>\n]{2,40}>|\$X\b")
ASK_RE = re.compile(r"(?:request(?:ing|s)?|ask(?:ing)?|seek(?:ing|s)?|grant of|award of|invest(?:ment)? of|"
                    r"support of)\D{0,40}?\$\s?(\d[\d,]*(?:\.\d+)?)", re.I)


@dataclass
class Section:
    name: str
    words: int = 150
    hint: str = ""


@dataclass
class Request:
    funder: str
    program: str
    amount: float
    kind: str = "Letter of inquiry"
    word_limit: int = 0  # whole letter; 0 = only the section limits
    sections: list[Section] = field(default_factory=list)
    signer: str = ""
    salutation: str = ""


DEFAULT_SECTIONS = [Section("Introduction and request", 90, "who we are and the amount we ask for"),
                    Section("The need", 120, "the problem in our community, with local data"),
                    Section("The program", 150, "what the program does, for whom, where and when"),
                    Section("Outcomes and evaluation", 110, "results so far and how we will measure this year"),
                    Section("Organization and budget", 80, "our track record and how the grant will be used")]


def load_request(path: str | Path) -> Request:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    r = data.get("request") or {}
    allowed = {"funder", "program", "amount", "type", "word_limit", "sections", "signer", "salutation"}
    unknown = sorted(set(r) - allowed)
    if unknown:
        raise ValueError(f"{path}: unknown [request] settings: {', '.join(unknown)}")
    missing = [k for k in ("funder", "program", "amount") if not r.get(k)]
    if missing:
        raise ValueError(f"{path}: [request] needs {', '.join(missing)}")
    sections = [Section(s["name"], int(s.get("words", 150)), s.get("hint", "")) for s in r.get("sections", [])]
    return Request(funder=r["funder"], program=r["program"], amount=float(r["amount"]),
                   kind=r.get("type", "Letter of inquiry"), word_limit=int(r.get("word_limit", 0)),
                   sections=sections or list(DEFAULT_SECTIONS), signer=r.get("signer", ""),
                   salutation=r.get("salutation", ""))


# --- the nonprofit's documents and budget -------------------------------------------------------------------------

@dataclass
class Passage:
    id: str
    source: str
    text: str


@dataclass
class Library:
    passages: list[Passage] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)


def load_docs(folder: str | Path) -> Library:
    lib = Library()
    files, skipped = document_files(folder)
    lib.files_skipped += skipped
    for path in files:
        try:
            sections = read_document(path)
        except Exception as e:  # a broken file must not stop the draft
            lib.files_skipped.append(f"{path.name} ({type(e).__name__})")
            continue
        n = len(lib.passages)
        for source, body in sections:
            for text in chunk(body, 140):
                lib.passages.append(Passage(f"P{len(lib.passages) + 1}", source, text))
        lib.files_read.append(f"{path.name} ({len(lib.passages) - n} passages)")
    return lib


def read_budget(path: str | Path | None) -> dict[str, list[tuple[str, float]]]:
    """Program budgets: CSV with Program, Item (or Line item) and Amount columns."""
    out: dict[str, list[tuple[str, float]]] = {}
    if not path:
        return out
    for r in read_table(path):
        prog, item = pick(r, "program", "project"), pick(r, "item", "line item", "expense", "category")
        amt = amounts(pick(r, "amount", "cost", "budget"))
        if prog and item and amt:
            out.setdefault(prog.strip().lower(), []).append((item, max(amt)))
    return out


def budget_text(program: str, lines: list[tuple[str, float]]) -> str:
    total = sum(a for _, a in lines)
    return (f"Budget for {program}: " + "; ".join(f"{i} ${a:,.0f}" for i, a in lines) + f"; total ${total:,.0f}")


# --- the prompt ---------------------------------------------------------------------------------------------------

SYSTEM = """You are a grant writer drafting a {kind} for {org}, a small US nonprofit, to {funder}.

Rules:
- Write each section listed, in order, in plain and specific language for a busy program officer. No headings inside the text.
- Use only the facts in SOURCES. Every statistic, number, date and outcome must come from a source you cite in that section's "sources". Never invent numbers, partners, outcomes or quotes.
- Ask for exactly ${amount:,.0f} for {program}. Mention {funder} by name and connect the request to its stated priorities. Never mention any other funder by name.
- Keep each section under its word limit. No placeholders such as [X] or TBD.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"sections": [{"name": "<section name>", "text": "<the section>", "sources": ["P3", "B1"]}]}"""


@dataclass
class Evidence:
    """The sources offered to the model, by the ids it cites."""

    items: dict[str, Passage] = field(default_factory=dict)

    def text(self, ids: list[str]) -> str:
        return "\n".join(self.items[i].text for i in ids if i in self.items)


def gather(req: Request, org: Org, funder: Funder, lib: Library, budget: dict, top_k: int = 4,
           all_under_words: int = 3500) -> Evidence:
    """The sources for the draft: the profile, the funder's guidelines, the program budget, and the nonprofit's
    documents. Small document sets go in whole; big ones are searched per section."""
    ev = Evidence()
    ev.items["O1"] = Passage("O1", "nonprofit profile", org.as_text())
    ev.items["F1"] = Passage("F1", f"{funder.name} guidelines", funder.text()[:2500])
    lines = budget.get(req.program.lower())
    if lines:
        ev.items["B1"] = Passage("B1", "budget", budget_text(req.program, lines))
    if sum(word_count(p.text) for p in lib.passages) <= all_under_words:
        for p in lib.passages:
            ev.items[p.id] = p
    elif lib.passages:
        index = BM25([f"{p.source} {p.text}" for p in lib.passages])
        for n, s in enumerate(req.sections, 1):
            for i, _ in index.top(f"{s.name} {s.hint} {req.program}", top_k):
                p = lib.passages[i]
                ev.items[p.id] = p
    return ev


def build_prompt(req: Request, ev: Evidence) -> str:
    limits = "\n".join(f"{n}. {s.name} (up to {s.words} words){': ' + s.hint if s.hint else ''}"
                       for n, s in enumerate(req.sections, 1))
    total = f"\nThe whole letter must stay under {req.word_limit} words." if req.word_limit else ""
    sources = "\n".join(f"[{i}] ({p.source}) {p.text}" for i, p in ev.items.items())
    return (f"SECTIONS TO WRITE\n{limits}{total}\n\nSOURCES\n{sources}\n\n"
            f"Return JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}")


def system_prompt(req: Request, org: Org) -> str:
    return SYSTEM.format(kind=req.kind.lower(), org=org.name, funder=req.funder, amount=req.amount,
                         program=req.program)


class DraftSection(BaseModel):
    name: str = ""
    text: str = ""
    sources: list[str] = Field(default_factory=list)

    @field_validator("name", "text", mode="before")
    @classmethod
    def _s(cls, v):
        if isinstance(v, list):
            return "\n\n".join(str(x) for x in v if x)
        return "" if v is None else str(v).strip()

    @field_validator("sources", mode="before")
    @classmethod
    def _l(cls, v):
        if v is None or v == "":
            return []
        items = v if isinstance(v, list) else re.split(r"[,;\s]+", str(v))
        return [str(x).strip().strip("[]") for x in items if str(x).strip()]


class DraftReply(BaseModel):
    sections: list[DraftSection] = Field(default_factory=list)

    @field_validator("sections", mode="before")
    @classmethod
    def _list(cls, v):
        if isinstance(v, dict):
            v = [{"name": k, "text": t} if isinstance(t, str) else {"name": k, **t} for k, t in v.items()]
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


# --- checks -------------------------------------------------------------------------------------------------------

@dataclass
class Flag:
    section: str  # "" = the whole letter
    hold: bool
    text: str


@dataclass
class Draft:
    request: Request
    sections: list[tuple[Section, str, list[str]]] = field(default_factory=list)  # (spec, text, valid source ids)
    flags: list[Flag] = field(default_factory=list)
    error: str = ""

    @property
    def words(self) -> int:
        return sum(word_count(t) for _, t, _ in self.sections)

    @property
    def ready(self) -> bool:
        return not self.error and bool(self.sections) and not any(f.hold for f in self.flags)


def _same_name(a: str, b: str) -> bool:
    return set(words(a)) - {"the", "and", "of"} == set(words(b)) - {"the", "and", "of"}


def check_draft(reply: DraftReply, req: Request, org: Org, funder: Funder, ev: Evidence, others: list[str],
                budget: dict) -> Draft:
    d = Draft(request=req)
    flags = d.flags
    got = list(reply.sections)
    for spec in req.sections:
        sec = next((s for s in got if _same_name(s.name, spec.name)), None)
        renamed = not any(_same_name(s.name, x.name) for s in got for x in req.sections)
        idx = req.sections.index(spec)
        if sec is None and renamed and idx < len(got):  # the model renamed every section: take them in order
            sec = got[idx]
        if sec is None or not sec.text:
            flags.append(Flag(spec.name, True, "section missing"))
            continue
        valid = [i for i in dict.fromkeys(sec.sources) if i in ev.items]
        if len(valid) < len(set(sec.sources)):
            flags.append(Flag(spec.name, False, "cited a source it was not given"))
        n = word_count(sec.text)
        if n > spec.words * 1.1:
            flags.append(Flag(spec.name, True, f"{n} words; the limit is {spec.words}"))
        elif n > spec.words:
            flags.append(Flag(spec.name, False, f"{n} words; the limit is {spec.words}"))
        allowed = "\n".join([ev.text(valid), ev.items["O1"].text, f"${req.amount:,.0f}", req.program, funder.name])
        nums = unsupported_numbers(sec.text, allowed)
        if nums:
            flags.append(Flag(spec.name, True, f"states {', '.join(nums)}, which the cited sources don't say"
                                               + ("" if valid else " (no sources cited)")))
        if PLACEHOLDER_RE.search(sec.text):
            flags.append(Flag(spec.name, True, f"placeholder '{PLACEHOLDER_RE.search(sec.text).group(0)}'"))
        d.sections.append((spec, sec.text, valid))
    if not d.sections:
        return d
    full = "\n\n".join(t for _, t, _ in d.sections)
    if req.word_limit and d.words > req.word_limit:
        flags.append(Flag("", True, f"{d.words} words in all; the limit is {req.word_limit}"))
    asks = {float(a.replace(",", "")) for a in ASK_RE.findall(full)}
    wrong = sorted(a for a in asks if abs(a - req.amount) > 0.5)
    if wrong:
        flags.append(Flag("", True, f"asks for ${wrong[0]:,.0f}; the request is ${req.amount:,.0f}"))
    if not has_amount(req.amount, full):
        flags.append(Flag("", True, f"never states the ${req.amount:,.0f} request"))
    for other in others:
        if other and not _same_name(other, funder.name) and re.search(rf"\b{re.escape(other)}\b", full, re.I):
            flags.append(Flag("", True, f"names another funder: {other}"))
    if funder.name.lower() not in full.lower():
        flags.append(Flag("", False, f"never names {funder.name}"))
    if req.program.lower() not in full.lower():
        flags.append(Flag("", False, f"never names the program ({req.program})"))
    lines = budget.get(req.program.lower())
    if lines:
        total = sum(a for _, a in lines)
        if req.amount > total:
            flags.append(Flag("", True, f"asks ${req.amount:,.0f} for a program that costs ${total:,.0f}"))
        elif req.amount > 0.5 * total:
            flags.append(Flag("", False, f"the grant would cover {req.amount / total:.0%} of the program's "
                                         f"${total:,.0f} budget; funders may ask who pays the rest"))
    elif budget:
        flags.append(Flag("", False, f"no budget lines for {req.program} in the budget file"))
    if funder.grant_range:
        lo, hi = funder.grant_range
        if req.amount < lo or (hi and req.amount > hi):
            flags.append(Flag("", True, f"${req.amount:,.0f} is outside {funder.name}'s typical grant "
                                        f"({funder.typical_grant})"))
    if funder.out:
        flags.append(Flag("", True, f"{funder.name} is ruled out: {'; '.join(funder.out)}"))
    return d
