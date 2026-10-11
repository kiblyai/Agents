"""Scores the notices the rules leave in, a few per request, with quotes from each notice checked in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import quote_found

from .notices import Notice
from .profile import Profile

SYSTEM = """You help a small federal contractor decide which SAM.gov notices to pursue.

Rules:
- Judge each notice only from its text against OUR COMPANY. For every capability match and risk, quote the notice word for word.
- fit: 0-100. 80+ means the work is squarely what we do; 50-79 partly; under 50 a stretch.
- decision: "bid" (solicitations we can win), "respond" (sources sought or presolicitations worth answering to shape the requirement), or "pass".
- risks: incumbents, past-performance or certification requirements, clearances above ours, on-site work, short timelines, anything that makes us weak.
- questions: what to ask the contracting officer, if anything.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"notices": [{"id": "<notice id>", "fit": <0-100>, "decision": "<bid|respond|pass>", "why": "<2 plain sentences>", "matches": [{"capability": "<our capability>", "quote": "<words from the notice>"}], "risks": [{"risk": "<...>", "quote": "<words from the notice>"}], "questions": ["<...>"]}]}"""


def build_prompt(batch: list[Notice], p: Profile) -> str:
    blocks = [f"### {n.id}\n{n.text()}" for n in batch]
    return (f"OUR COMPANY\n{p.as_text()}\n\nNOTICES\n" + "\n\n".join(blocks)
            + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}")


def _str(v) -> str:
    return "" if v is None else str(v).strip()


class Quoted(BaseModel):
    point: str = ""
    quote: str = ""


class NoticeScore(BaseModel):
    id: str = ""
    fit: int = 0
    decision: str = "pass"
    why: str = ""
    matches: list[Quoted] = Field(default_factory=list)
    risks: list[Quoted] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)

    @field_validator("id", "why", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("fit", mode="before")
    @classmethod
    def _fit(cls, v):
        m = re.search(r"\d+", str(v or 0))
        return max(0, min(100, int(m.group(0)))) if m else 0

    @field_validator("decision", mode="before")
    @classmethod
    def _d(cls, v):
        v = _str(v).lower()
        return v if v in ("bid", "respond", "pass") else "pass"

    @field_validator("matches", "risks", mode="before")
    @classmethod
    def _q(cls, v, info):
        key = "capability" if info.field_name == "matches" else "risk"
        items = v if isinstance(v, list) else []
        return [{"point": _str(x.get(key) or x.get("point")), "quote": _str(x.get("quote"))} if isinstance(x, dict)
                else {"point": _str(x)} for x in items]

    @field_validator("questions", mode="before")
    @classmethod
    def _l(cls, v):
        return [_str(x) for x in (v if isinstance(v, list) else [v] if v else []) if _str(x)]


class BatchScores(BaseModel):
    notices: list[NoticeScore] = Field(default_factory=list)

    @field_validator("notices", mode="before")
    @classmethod
    def _l(cls, v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


@dataclass
class Match:
    notice: Notice
    fit: int | None = None
    decision: str = ""
    why: str = ""
    matches: list[Quoted] = field(default_factory=list)
    risks: list[Quoted] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def status(self) -> str:
        if self.notice.out:
            return "filtered out"
        if self.error:
            return "not scored"
        return {"bid": "bid", "respond": "respond"}.get(self.decision, "pass")


def check_score(s: NoticeScore, n: Notice) -> Match:
    m = Match(notice=n, fit=s.fit, decision=s.decision, why=s.why, questions=s.questions)
    text = n.text()
    m.matches = [q for q in s.matches if q.quote and quote_found(q.quote, text, 0.8)]
    m.risks = [q for q in s.risks if q.quote and quote_found(q.quote, text, 0.8)]
    dropped = len(s.matches) + len(s.risks) - len(m.matches) - len(m.risks)
    if dropped:
        m.checks.append(f"{dropped} quotes dropped: not in the notice")
    if not m.matches and m.fit > 40:
        m.checks.append(f"fit lowered from {m.fit} to 40: no capability quoted from the notice")
        m.fit = 40
    if m.decision == "bid" and n.type.lower().startswith(("sources sought", "presolicitation")):
        m.checks.append(f"a {n.type.lower()} can't be bid on yet; marked 'respond'")
        m.decision = "respond"
    if m.decision in ("bid", "respond") and m.fit < 50:
        m.checks.append(f"{m.decision} with a fit of {m.fit}: check it's worth the effort")
    return m
