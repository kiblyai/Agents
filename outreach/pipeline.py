"""Leads in, checked sequences out; replies in, sorted and drafted out. One cached model request per item."""

from __future__ import annotations

from typing import Callable

from agentkit.inbox import Email
from agentkit.runner import Runner

from .campaign import Campaign
from .leads import Lead
from .replies import ReplyResult, Triage, build_prompt as reply_prompt, check_triage, code_category, rule_result
from .replies import system_prompt as reply_system
from .sequence import Flag, Sequence, SequenceDraft, build_prompt as sequence_prompt, check_sequence
from .sequence import system_prompt as sequence_system

# Bump when prompts, schemas or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"


async def write_sequences(leads: list[Lead], c: Campaign, runner: Runner,
                          progress: Callable[[str], None] = print) -> list[Sequence]:
    out = []
    system = sequence_system(c)
    for i, lead in enumerate(leads, 1):
        tag = f"[{i}/{len(leads)}] {lead.name or lead.email or f'row {lead.row}'}"
        if lead.held:
            out.append(Sequence(lead=lead))
            progress(f"{tag}: held back ({'; '.join(lead.held)})")
            continue
        got, err = await runner.ask(system, sequence_prompt(lead, c), SequenceDraft, max_tokens=2500)
        if got is None:
            out.append(Sequence(lead=lead, error=err))
            progress(f"{tag}: {err}")
            continue
        seq = check_sequence(got, lead, c)
        out.append(seq)
        holds = sum(1 for f in seq.flags if f.hold)
        progress(f"{tag}: {'held for review (' + str(holds) + ' to fix)' if holds else 'ready'}")
    return out


def find_lead(e: Email, leads: list[Lead]) -> tuple[Lead | None, str]:
    """The lead a reply is from: by address, else the one lead at the same domain (a colleague answering)."""
    addr = (e.sender or "").lower()
    for lead in leads:
        if lead.email and lead.email == addr:
            return lead, ""
    domain = addr.split("@")[-1]
    same = [lead for lead in leads if lead.domain and lead.domain == domain]
    if len(same) == 1:
        return same[0], f"replied from {addr}, not the address we emailed ({same[0].email})"
    return None, ""


async def triage_replies(emails: list[Email], leads: list[Lead], c: Campaign, runner: Runner,
                         progress: Callable[[str], None] = print) -> list[ReplyResult]:
    out = []
    system = reply_system(c)
    for i, e in enumerate(emails, 1):
        tag = f"[{i}/{len(emails)}] {e.file}"
        lead, note = find_lead(e, leads)
        forced = code_category(e)
        if forced:  # bounces, opt-outs and away messages need no model, so they're honored even if it's down
            r = rule_result(e, lead, c, forced)
            if forced == "bounce" and r.suppress_email:
                r.lead = next((x for x in leads if x.email == r.suppress_email), None)
        else:
            got, err = await runner.ask(system, reply_prompt(e, lead, c), Triage, max_tokens=1500)
            if got is None:
                out.append(ReplyResult(email=e, lead=lead, error=err))
                progress(f"{tag}: {err}")
                continue
            r = check_triage(got, e, lead, c)
        if note:
            r.flags.append(Flag(0, False, note))
        out.append(r)
        progress(f"{tag}: {r.category.replace('_', ' ')}" + (" (by rule)" if forced else "")
                 + (f", follow up {r.follow_up}" if r.follow_up else "") + (", suppress" if r.suppress_email else ""))
    return out
