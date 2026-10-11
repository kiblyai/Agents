"""Job posts in, ranked decisions and proposal drafts out. The money decides in code; the model drafts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from agentkit.runner import Runner

from .assess import Assessment, Draft, build_prompt, check_assessment, system_prompt
from .jobs import Job, apply_rules, client_score, skills_matched
from .profile import BUCKETS, Profile

# Bump when the prompt, schema or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"
COMPETITION = dict(zip(BUCKETS, [1.0, 0.8, 0.6, 0.5, 0.3, 0.1]))


@dataclass
class Decision:
    job: Job
    decision: str = ""  # "bid", "maybe" or "skip"
    reasons: list[str] = field(default_factory=list)  # why skip or maybe
    notes: list[str] = field(default_factory=list)  # worth knowing; doesn't change the decision
    hours: tuple[float, float] | None = None
    rate_mid: float | None = None  # effective $/h after fees
    rate_low: float | None = None
    client: int = 0
    client_notes: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    draft: Draft | None = None
    error: str = ""
    priority: float = 0.0


def money_check(j: Job, d: Decision, p: Profile) -> None:
    """Effective hourly rate after Upwork's fee: hourly jobs from the top of the range, fixed jobs from your own
    hours for that kind of project."""
    keep = 1 - p.fee  # rates are rounded to cents, so a job exactly at the floor isn't skipped by float error
    if j.kind == "hourly" and j.rate_high:
        d.rate_mid = d.rate_low = round(j.rate_high * keep, 2)
    elif j.kind == "fixed" and j.budget and d.draft and d.draft.project_type:
        lo, hi = p.project_type(d.draft.project_type).hours
        d.hours = (lo, hi)
        d.rate_mid = round(j.budget * keep / ((lo + hi) / 2), 2)
        d.rate_low = round(j.budget * keep / hi, 2)


def decide(d: Decision, p: Profile) -> None:
    """bid: nothing against it; maybe: something to weigh (listed in reasons); skip: a hard no. Notes are shown
    but don't change the decision."""
    j = d.job
    if j.out:
        d.decision, d.reasons = "skip", list(j.out)
        return
    if d.error:
        d.decision, d.reasons = "maybe", [d.error]
        return
    skip, concerns, notes = [], [], list(j.notes)
    if d.rate_mid is not None and d.rate_mid < p.floor_after_fees:
        skip.append(f"about ${d.rate_mid:,.0f}/h after fees at your {d.hours[0]:g}-{d.hours[1]:g} h estimate"
                    if d.hours else f"${d.rate_mid:,.0f}/h after fees")
    elif d.rate_low is not None and d.rate_low < p.floor_after_fees and d.hours:
        notes.append(f"${d.rate_low:,.0f}/h after fees if it takes {d.hours[1]:g} h: scope it tightly")
    if d.rate_mid is None:
        concerns.append("rate unknown: check the budget")
    fit = d.draft.fit if d.draft else 0
    if fit < 40:
        skip.append(f"fit {fit}")
    elif fit < 60:
        concerns.append(f"fit {fit}")
    if d.client < 60:
        concerns.append("client: " + "; ".join(d.client_notes))
    if len(d.skills) < 2:
        concerns.append(f"only {len(d.skills)} of the listed skills match yours")
    if d.draft and not d.draft.ready:
        concerns.append("the draft needs fixing")
    d.decision = "skip" if skip else "maybe" if concerns else "bid"
    d.reasons, d.notes = skip + concerns, notes
    edge = min((d.rate_mid or p.floor_after_fees) / p.floor_after_fees, 1.5)
    d.priority = round(fit / 100 * d.client / 100 * COMPETITION.get(j.proposals, 0.5) * edge, 3)


async def assess_all(jobs: list[Job], p: Profile, runner: Runner, today: date,
                     progress: Callable[[str], None] = print) -> list[Decision]:
    out = []
    system = system_prompt(p)
    for i, j in enumerate(jobs, 1):
        apply_rules(j, p, today)
        score, notes = client_score(j, today)
        d = Decision(job=j, client=score, client_notes=notes, skills=skills_matched(j, p))
        if not j.out:
            got, err = await runner.ask(system, build_prompt(j, p), Assessment, max_tokens=2500)
            if got is None:
                d.error = err
            else:
                d.draft = check_assessment(got, j, p)
                money_check(j, d, p)
        decide(d, p)
        out.append(d)
        progress(f"[{i}/{len(jobs)}] {j.file}: {d.decision}" + (f" ({d.reasons[0]})" if d.reasons else ""))
    order = {"bid": 0, "maybe": 1, "skip": 2}
    out.sort(key=lambda d: (order[d.decision], -d.priority))
    return out
