"""Reads saved Upwork job posts (the page text, copied into .txt files) and works out the numbers in code:
budget, effective rate after fees, client quality, competition, skills and red flags."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agentkit.checks import EMAIL_RE, parse_date

from .profile import BUCKETS, Profile

MONEY = r"\$\s?([\d,]+(?:\.\d+)?)"
APPS = r"(?:telegram|whatsapp|skype|signal|discord|wechat|gmail)"
# A request to move the talk or the money off Upwork. Building a WhatsApp or Telegram bot is a normal job, so the app
# name alone doesn't count: it needs "contact me on ..." or a handle or number after it.
OFF_PLATFORM = re.compile(rf"\b(?:contact|message|text|reach|add|ping|dm|call|email|write to|talk to|chat with) (?:me|us) "
                          rf"(?:on|at|via|through|over|in) (?:{APPS}\b|\+?\d)|\b{APPS}(?: id| handle| number| username)?"
                          r"\s*[:-]?\s*[@+]\w|\b(?:pay|paid|payment) (?:you )?outside\b|\boff[- ]platform\b|"
                          r"\bsend (?:me )?your (?:bank|paypal)", re.I)
UNPAID = re.compile(r"\bunpaid (?:test|trial|sample|task|work)\b|(?<![-\w])free (?:test|sample)\b|"
                    r"\btest task\b.{0,40}\b(?:unpaid|free|not paid)\b", re.I)
# Where the skill tags end when they're listed one per line, as on the job page.
SECTION = re.compile(r"(?:activity on this job|about the client|send a proposal|proposals\b|you will be asked|"
                     r"preferred qualifications|client's recent history|other open jobs|explore similar|interviewing|"
                     r"project type|experience level|attachments?\b)", re.I)


@dataclass
class Job:
    file: str
    title: str = ""
    text: str = ""
    kind: str = ""  # "fixed" or "hourly"
    budget: float | None = None  # fixed price
    rate_low: float | None = None  # hourly range
    rate_high: float | None = None
    proposals: str = ""  # Upwork's bucket, e.g. "10 to 15"
    connects: int | None = None
    payment_verified: bool | None = None
    rating: float | None = None
    hire_rate: int | None = None
    jobs_posted: int | None = None
    total_spent: float | None = None
    member_since: date | None = None
    skills: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    out: list[str] = field(default_factory=list)  # why it's skipped (code)
    notes: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        """The client's own words: the post without Upwork's activity and client panels (whose numbers a proposal
        must not borrow, e.g. "10 to 15" proposals is not "10 years")."""
        m = re.search(r"^\s*(?:activity on this job|about the client|send a proposal for)\b", self.text, re.I | re.M)
        return self.text[:m.start()] if m else self.text

    @property
    def budget_text(self) -> str:
        if self.kind == "fixed" and self.budget is not None:
            return f"${self.budget:,.0f} fixed"
        if self.kind == "hourly" and self.rate_high:
            return (f"${self.rate_low:,.0f}-" if self.rate_low else "up to ") + f"${self.rate_high:,.0f}/h"
        return "not stated"


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _skills(text: str) -> list[str]:
    """Skill tags, from "Skills and Expertise: a, b" or the tags one per line under the heading."""
    m = (re.search(r"^[ \t]*skills and expertise[ \t]*(?::[ \t]*([^\n]*))?$", text, re.I | re.M)
         or re.search(r"^[ \t]*skills[ \t]*(?::[ \t]*([^\n]*))?$", text, re.I | re.M))
    if not m:
        return []
    tags = [m.group(1)] if (m.group(1) or "").strip() else []
    if not tags:
        for ln in text[m.end():].split("\n"):
            ln = ln.strip()
            if not ln or re.search(r"(?:^|\s)(?:skills|deliverables)$", ln, re.I):
                continue
            if len(ln) > 40 or SECTION.match(ln) or ln[0] in "$0123456789" or len(tags) == 15:
                break
            tags.append(ln)
    return [s.strip().lower() for t in tags for s in re.split(r"[,;•|]", t) if s.strip()]


def parse_job(name: str, text: str) -> Job:
    text = text.replace("\r\n", "\n")
    j = Job(file=name, text=text, title=next((ln.strip() for ln in text.split("\n") if ln.strip()), ""))
    low = text.lower()
    m = (re.search(rf"{MONEY}\s*-\s*{MONEY}\s*(?:/hr|/hour|an hour|\n?\s*hourly)", text, re.I)
         or re.search(rf"\bhourly(?: range| rate)?:?\s*{MONEY}\s*-\s*{MONEY}", text, re.I))
    if m:
        j.kind, j.rate_low, j.rate_high = "hourly", _money(m.group(1)), _money(m.group(2))
    elif re.search(r"\bhourly\b", low):
        j.kind = "hourly"
        m = re.search(rf"{MONEY}\s*(?:/hr|/hour|an hour)", text, re.I) or re.search(rf"{MONEY}\s*\n?\s*hourly",
                                                                                   text, re.I)
        if m:
            j.rate_high = _money(m.group(1))
    m = re.search(rf"{MONEY}\s*\n?\s*fixed[- ]price", text, re.I) or re.search(rf"fixed[- ]price[^$\n]*\n?\s*{MONEY}",
                                                                                 text, re.I)
    if m and not j.kind:
        j.kind, j.budget = "fixed", _money(m.group(1))
    m = re.search(r"proposals:\s*(less than 5|5 to 10|10 to 15|15 to 20|20 to 50|50\+)", low)
    j.proposals = m.group(1) if m else ""
    m = re.search(r"(\d+)\s+connects", low) or re.search(r"required connects[^:\n]*:\s*(\d+)", low)
    j.connects = int(m.group(1)) if m else None
    if "payment method verified" in low or "payment verified" in low:
        j.payment_verified = True
    if re.search(r"payment (?:method )?(?:not|un)\s?verified", low):
        j.payment_verified = False
    m = (re.search(r"(\d(?:\.\d+)?)\s*(?:out of 5\b|of 5 stars|stars?\b)", low)
         or re.search(r"\b(\d(?:\.\d+)?) of \d+ reviews?\b", low))
    j.rating = float(m.group(1)) if m else None
    m = re.search(r"(\d+)%\s*hire rate", low)
    j.hire_rate = int(m.group(1)) if m else None
    m = re.search(r"(\d+)\s+jobs? posted", low)
    j.jobs_posted = int(m.group(1)) if m else None
    m = re.search(rf"{MONEY}\s*([km])?\+?\s*total spent", text, re.I)
    if m:
        j.total_spent = _money(m.group(1)) * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)
    m = re.search(r"member since\s+([a-z]{3,9}\.? \d{1,2},? \d{4})", text, re.I)
    j.member_since = parse_date(m.group(1)) if m else None
    j.skills = _skills(text)
    qm = re.search(r"answer the following questions[^\n]*\n((?:\s*\d+[.)][^\n]+\n?)+)", text, re.I)
    if qm:
        j.questions = [re.sub(r"^\s*\d+[.)]\s*", "", q).strip() for q in qm.group(1).splitlines() if q.strip()]
    return j


def read_jobs(folder: str | Path) -> tuple[list[Job], list[str]]:
    jobs, skipped = [], []
    for p in sorted(Path(folder).iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        if p.suffix.lower() not in (".txt", ".md"):
            skipped.append(f"{p.name} (save each job post as a .txt file)")
            continue
        jobs.append(parse_job(p.name, p.read_text(encoding="utf-8", errors="replace")))
    return jobs, skipped


def client_score(j: Job, today: date) -> tuple[int, list[str]]:
    """0-100 from the client's own record on Upwork, with the reasons."""
    score, why = 0, []
    if j.payment_verified:
        score += 30
    else:
        why.append("payment not verified")
    if j.rating is None:
        score += 10
        why.append("no reviews yet")
    elif j.rating >= 4.5:
        score += 20
    else:
        why.append(f"rated {j.rating}")
    if j.hire_rate is not None and j.hire_rate >= 60:
        score += 20
    elif j.hire_rate is not None and j.hire_rate >= 30:
        score += 10
        why.append(f"hires on {j.hire_rate}% of jobs")
    else:
        why.append("hire rate unknown" if j.hire_rate is None else "hasn't hired anyone yet" if j.hire_rate == 0
                   else f"hires on only {j.hire_rate}% of jobs")
    if (j.total_spent or 0) >= 10_000:
        score += 20
    elif (j.total_spent or 0) >= 1_000:
        score += 10
    else:
        why.append("has spent under $1,000 on Upwork")
    if j.member_since and (today - j.member_since).days >= 365:
        score += 10
    else:
        why.append("new to Upwork")
    return score, why


def skills_matched(j: Job, p: Profile) -> list[str]:
    mine = set(p.skills)
    out = []
    for s in j.skills:
        words = set(re.findall(r"[a-z0-9]+", s))
        if s in mine or words & mine:
            out.append(s)
    return out


def apply_rules(j: Job, p: Profile, today: date) -> None:
    """Skip a job in code when the numbers or the post itself rule it out."""
    keep = 1 - p.fee
    if j.payment_verified is False:
        j.out.append("payment method not verified")
    if j.kind == "hourly" and j.rate_high and round(j.rate_high * keep, 2) < p.floor_after_fees:
        j.out.append(f"pays up to ${j.rate_high:,.0f}/h, ${j.rate_high * keep:,.0f} after fees; your floor is "
                     f"${p.floor_after_fees:,.0f}")
    if j.kind == "fixed" and j.budget is not None and j.budget < p.min_fixed_budget:
        j.out.append(f"budget ${j.budget:,.0f} is under your ${p.min_fixed_budget:,.0f} minimum")
    if not j.kind:
        j.notes.append("budget not found in the post")
    if j.proposals and BUCKETS.index(j.proposals) > BUCKETS.index(p.max_proposals.lower()):
        j.out.append(f"already {j.proposals} proposals")
    if j.hire_rate is not None and j.hire_rate < 30 and (j.jobs_posted or 0) >= 5:
        j.out.append(f"client hires on only {j.hire_rate}% of {j.jobs_posted} jobs")
    m = OFF_PLATFORM.search(j.text)
    if m or EMAIL_RE.search(j.text):
        j.out.append(f"asks to talk or pay off Upwork ('{m.group(0) if m else 'an email address'}'): against Upwork's "
                     "rules and a common scam")
    if UNPAID.search(j.text):
        j.notes.append("asks for unpaid test work")
    words = [w for w in p.exclude_words if re.search(rf"\b{re.escape(w)}\b", j.text, re.I)]
    if words:
        j.out.append(f"mentions {', '.join(words)}")
