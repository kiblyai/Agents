"""Funders in, ranked prospects out; one request per funder the rules leave in. Then one request per draft."""

from __future__ import annotations

from datetime import date
from typing import Callable

from agentkit.runner import Runner

from .draft import Draft, DraftReply, Request, build_prompt as draft_prompt, check_draft, gather
from .draft import system_prompt as draft_system
from .funders import Funder, apply_rules
from .org import Org
from .prospect import SYSTEM as FIT_SYSTEM, FitReply, Prospect, build_prompt as fit_prompt, check_fit

# Bump when prompts, schemas or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"


async def prospect_all(funders: list[Funder], org: Org, runner: Runner, today: date,
                       progress: Callable[[str], None] = print) -> list[Prospect]:
    out = []
    for i, f in enumerate(funders, 1):
        tag = f"[{i}/{len(funders)}] {f.name}"
        apply_rules(f, org, today)
        if f.out:
            out.append(Prospect(funder=f))
            progress(f"{tag}: ruled out ({'; '.join(f.out)})")
            continue
        got, err = await runner.ask(FIT_SYSTEM, fit_prompt(f, org), FitReply, max_tokens=2000)
        if got is None:
            out.append(Prospect(funder=f, error=err))
            progress(f"{tag}: {err}")
            continue
        p = check_fit(got, f, org)
        out.append(p)
        progress(f"{tag}: fit {p.fit} ({p.status})" + (f", best for {p.best_program}" if p.best_program else ""))
    order = {"strong": 0, "possible": 1, "weak": 2, "not scored": 3, "ruled out": 4}
    out.sort(key=lambda p: (order[p.status], -(p.fit or 0)))
    return out


async def draft_letter(req: Request, org: Org, funder: Funder, lib, budget: dict, others: list[str], runner: Runner,
                       today: date) -> tuple[Draft, object]:
    apply_rules(funder, org, today)
    ev = gather(req, org, funder, lib, budget)
    got, err = await runner.ask(draft_system(req, org), draft_prompt(req, ev), DraftReply, max_tokens=4000)
    if got is None:
        return Draft(request=req, error=err), ev
    return check_draft(got, req, org, funder, ev, others, budget), ev
