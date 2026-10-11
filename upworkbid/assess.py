"""Asks the model to classify a job and draft the proposal, then checks the draft in code before anyone sends it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import EMAIL_RE, find_phrases, quote_found, unsupported_numbers, word_count, words

from .jobs import Job
from .profile import Profile

SYSTEM = """You help a freelance AI engineer decide whether to bid on an Upwork job, and draft the proposal.

Rules:
- project_type: the closest one of PROJECT TYPES, named exactly.
- fit: 0-100 against the freelancer's skills and portfolio.
- requirements and red_flags: each with a quote from the job post, copied word for word. Red flags: vague or growing scope, a budget far too small for the work, anything risky.
- proposal: at most {words} words, plain and specific. Open with one sentence about THIS job, built on a detail from the post; copy that detail into opening_detail word for word. Name the one portfolio item that fits best (its id in portfolio_used) and what it shows. End with a concrete first step or one sharp question. No greeting like "Dear Hiring Manager", no sign-off, no contact details, no links except the portfolio links given.
- answers: one answer per screening question, in order, each under 60 words.
- Never invent experience, clients, numbers or results that aren't in FREELANCER.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"project_type": "<one of the project types>", "fit": <0-100>, "requirements": [{"requirement": "<...>", "quote": "<words from the post>"}], "red_flags": [{"flag": "<...>", "quote": "<words from the post>"}], "opening_detail": "<the detail from the post the proposal opens with, word for word>", "proposal": "<the proposal>", "portfolio_used": ["P1"], "answers": ["<answer to question 1>"]}"""


def system_prompt(p: Profile) -> str:
    return SYSTEM.format(words=p.proposal_words)


def build_prompt(j: Job, p: Profile) -> str:
    qs = "\n".join(f"{i}. {q}" for i, q in enumerate(j.questions, 1)) or "(none)"
    return (f"FREELANCER\n{p.as_text()}\n\nPROJECT TYPES: {'; '.join(t.name for t in p.project_types)}\n\n"
            f"JOB POST ({j.file})\n{j.text[:9000]}\n\nSCREENING QUESTIONS\n{qs}\n\n"
            f"Return JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}")


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v if x)
    return str(v).strip()


class Quoted(BaseModel):
    point: str = ""
    quote: str = ""


class Assessment(BaseModel):
    project_type: str = ""
    fit: int = 0
    requirements: list[Quoted] = Field(default_factory=list)
    red_flags: list[Quoted] = Field(default_factory=list)
    opening_detail: str = ""
    proposal: str = ""
    portfolio_used: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)

    @field_validator("project_type", "opening_detail", "proposal", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("fit", mode="before")
    @classmethod
    def _fit(cls, v):
        m = re.search(r"\d+", str(v or 0))
        return max(0, min(100, int(m.group(0)))) if m else 0

    @field_validator("requirements", "red_flags", mode="before")
    @classmethod
    def _q(cls, v, info):
        key = "requirement" if info.field_name == "requirements" else "flag"
        items = v if isinstance(v, list) else []
        return [{"point": _str(x.get(key) or x.get("point")), "quote": _str(x.get("quote"))} if isinstance(x, dict)
                else {"point": _str(x)} for x in items]

    @field_validator("portfolio_used", "answers", mode="before")
    @classmethod
    def _l(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, dict):
            v = list(v.values())
        return [_str(x) for x in (v if isinstance(v, list) else [v]) if _str(x)]


@dataclass
class Check:
    hold: bool  # True: fix before sending
    text: str


@dataclass
class Draft:
    project_type: str = ""
    fit: int = 0
    requirements: list[Quoted] = field(default_factory=list)
    red_flags: list[Quoted] = field(default_factory=list)
    proposal: str = ""
    answers: list[str] = field(default_factory=list)
    portfolio: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.proposal) and not any(c.hold for c in self.checks)


PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,30}\]|\{[^}\n]{1,30}\}|<[^>\n]{2,30}>")


def _text_checks(text: str, what: str, allowed: str, p: Profile, checks: list[Check]) -> None:
    banned = find_phrases(text, p.banned)
    if banned:
        checks.append(Check(True, f"{what}: banned phrase " + ", ".join(f"'{b}'" for b in banned)))
    if EMAIL_RE.search(text) or PHONE_RE.search(text):
        checks.append(Check(True, f"{what}: contact details before a contract break Upwork's rules"))
    links = [u.rstrip(".,)") for u in URL_RE.findall(text)]
    ok_links = {x.link.rstrip("/") for x in p.portfolio if x.link}
    bad = [u for u in links if u.rstrip("/") not in ok_links]
    if bad:
        checks.append(Check(True, f"{what}: link {bad[0]} is not one of your portfolio links"))
    nums = unsupported_numbers(PHONE_RE.sub("", text), allowed)
    if nums:
        checks.append(Check(True, f"{what}: mentions {', '.join(nums)}, which isn't in your profile or the post"))
    if PLACEHOLDER_RE.search(text):
        checks.append(Check(True, f"{what}: placeholder '{PLACEHOLDER_RE.search(text).group(0)}'"))


def check_assessment(a: Assessment, j: Job, p: Profile) -> Draft:
    d = Draft(fit=a.fit, answers=a.answers)
    checks = d.checks
    t = p.project_type(a.project_type)
    if t is None:
        checks.append(Check(True, f"project type '{a.project_type}' is not one of yours, so hours can't be estimated"))
    d.project_type = t.name if t else ""
    d.requirements = [q for q in a.requirements if q.quote and quote_found(q.quote, j.text, 0.8)]
    d.red_flags = [q for q in a.red_flags if q.quote and quote_found(q.quote, j.text, 0.8)]
    dropped = len(a.requirements) + len(a.red_flags) - len(d.requirements) - len(d.red_flags)
    if dropped:
        checks.append(Check(False, f"{dropped} quotes dropped: not in the job post"))
    allowed = "\n".join([p.claims_text(), j.description, j.budget_text])
    text = a.proposal.strip()
    if not text:
        checks.append(Check(True, "no proposal drafted"))
        return d
    n = word_count(text)
    if n > p.proposal_words * 1.2:
        checks.append(Check(True, f"{n} words; keep it under {p.proposal_words}"))
    elif n > p.proposal_words:
        checks.append(Check(False, f"{n} words; the limit is {p.proposal_words}"))
    _text_checks(text, "proposal", allowed, p, checks)
    if not a.opening_detail or not quote_found(a.opening_detail, j.text, 0.8):
        checks.append(Check(True, "the opening isn't built on a detail from this job post"))
    else:
        first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        detail = {w for w in words(a.opening_detail) if len(w) > 3}
        if detail and len(detail & set(words(first))) < min(2, len(detail)):
            checks.append(Check(False, "the first sentence doesn't seem to use the job detail"))
    used = [x for x in a.portfolio_used if p.item(x)]
    if len(used) < len(a.portfolio_used):
        checks.append(Check(True, "cites a portfolio item you don't have"))
    if not used:
        checks.append(Check(True, "names no portfolio item"))
    d.portfolio = [p.item(x).id for x in used]
    if len(a.answers) != len(j.questions):
        checks.append(Check(True, f"answers {len(a.answers)} of {len(j.questions)} screening questions"))
    for i, ans in enumerate(a.answers[:len(j.questions)], 1):
        if word_count(ans) > 80:
            checks.append(Check(False, f"answer {i} is {word_count(ans)} words; keep answers short"))
        _text_checks(ans, f"answer {i}", allowed, p, checks)
    d.proposal = text + (f"\n\n{p.signature}" if p.signature else "")
    return d
