"""Notices in, ranked matches out (a few notices per request); an RFP in, a compliance matrix out."""

from __future__ import annotations

from datetime import date
from typing import Callable

from agentkit.runner import Runner

from .match import SYSTEM, BatchScores, Match, build_prompt, check_score
from .notices import Notice, apply_rules
from .profile import Profile
from .shred import (FACTS_SYSTEM, LABEL_SYSTEM, Facts, Labels, Requirement, apply_labels, check_facts, facts_prompt,
                    find_requirements, label_prompt, volume_lines)

# Bump when prompts, schemas or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"
BATCH = 5  # notices per request
LABEL_BATCH = 40  # requirements per request
ORDER = {"bid": 0, "respond": 1, "pass": 2, "not scored": 3, "filtered out": 4}


async def match_all(notices: list[Notice], p: Profile, runner: Runner, today: date,
                    progress: Callable[[str], None] = print) -> list[Match]:
    for n in notices:
        apply_rules(n, p, today)
    results = {n.id: Match(notice=n) for n in notices if n.out}
    todo = [n for n in notices if not n.out]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    for b, batch in enumerate(batches, 1):
        got, err = await runner.ask(SYSTEM, build_prompt(batch, p), BatchScores, max_tokens=3000)
        if got is None:
            for n in batch:
                results[n.id] = Match(notice=n, error=err)
            progress(f"[batch {b}/{len(batches)}] {err}")
            continue
        by_id = {s.id.strip(): s for s in got.notices}
        for n in batch:
            s = by_id.get(n.id)
            results[n.id] = check_score(s, n) if s else Match(notice=n, error="the model skipped this notice; re-run "
                                                                             "to retry")
        progress(f"[batch {b}/{len(batches)}] scored {len(batch)} notices")
    out = [results[n.id] for n in notices]
    out.sort(key=lambda m: (ORDER[m.status], -(m.fit or 0)))
    return out


async def shred(docs: list[tuple[str, str]], runner: Runner, progress: Callable[[str], None] = print
                ) -> tuple[list[Requirement], list[dict], list[str]]:
    reqs = find_requirements(docs)
    vols = volume_lines(docs)
    batches = [reqs[i:i + LABEL_BATCH] for i in range(0, len(reqs), LABEL_BATCH)]
    for b, batch in enumerate(batches, 1):
        got, err = await runner.ask(LABEL_SYSTEM, label_prompt(batch, vols), Labels, max_tokens=4000)
        if got is None:
            for r in batch:
                r.checks.append(err)
            progress(f"[labels {b}/{len(batches)}] {err}")
            continue
        skipped = apply_labels(batch, got)
        progress(f"[labels {b}/{len(batches)}] {len(batch) - len(skipped)} of {len(batch)} labeled")
    got, err = await runner.ask(FACTS_SYSTEM, facts_prompt(docs), Facts, max_tokens=2000)
    if got is None:
        return reqs, [], [f"key facts: {err}"]
    rows, checks = check_facts(got, "\n".join(t for _, t in docs))
    progress(f"[key facts] {sum(1 for r in rows if r['value'])} found")
    return reqs, rows, checks
