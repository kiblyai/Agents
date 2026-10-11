"""Exports in, a reviewed list of changes and merges out: rules in code, then one model request per company to enrich."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from agentkit.runner import Runner

from .config import CRMConfig
from .enrich import SYSTEM, Enrichment, build_prompt, check_enrichment, fetch_page, missing_fields, page_for
from .records import Record
from .rules import Change, Flag, Merge, company_rules, contact_rules, norm_domain

# Bump when the prompt, schema or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"


@dataclass
class Result:
    changes: list[Change] = field(default_factory=list)
    merges: list[Merge] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    enriched: int = 0
    no_page: list[str] = field(default_factory=list)


def clean(contacts: list[Record], companies: list[Record], cfg: CRMConfig, today: date) -> Result:
    res = Result()
    changes, merges, flags = company_rules(companies, cfg)
    merged = {m.duplicate for m in merges}
    kept = [c for c in companies if c.id not in merged]  # contacts are linked to the company that stays
    for ch, mg, fl in ((changes, merges, flags), contact_rules(contacts, kept, cfg, today)):
        res.changes += ch
        res.merges += mg
        res.flags += fl
    gone = {(m.kind, m.duplicate) for m in res.merges}  # records merged away need no other changes
    res.changes = [c for c in res.changes if (c.kind, c.id) not in gone]
    res.flags = [f for f in res.flags if (f.kind, f.id) not in gone]
    return res


async def enrich(res: Result, companies: list[Record], cfg: CRMConfig, runner: Runner | None,
                 sites_dir: Path | None = None, reader=None, limit: int | None = None,
                 progress: Callable[[str], None] = print) -> None:
    gone = {m.duplicate for m in res.merges if m.kind == "company"}
    todo = [c for c in companies if c.id not in gone and norm_domain(c.get("domain") or c.get("website"))
            and missing_fields(c)]
    if limit is not None:
        todo = todo[:limit]
    for i, c in enumerate(todo, 1):
        tag = f"[{i}/{len(todo)}] {c.label}"
        domain = norm_domain(c.get("domain") or c.get("website"))
        text, source = page_for(domain, sites_dir)
        if not text and reader is not None:
            text, source = await fetch_page(domain, reader)
        if not text:
            res.no_page.append(f"{c.label} ({domain})")
            progress(f"{tag}: no website text")
            continue
        if runner is None:
            continue
        got, err = await runner.ask(SYSTEM, build_prompt(c, text, source, cfg), Enrichment, max_tokens=1200)
        if got is None:
            res.flags.append(Flag("company", c.id, c.label, "not_enriched", err))
            progress(f"{tag}: {err}")
            continue
        changes, flags = check_enrichment(got, c, text, source, cfg)
        res.changes += changes
        res.flags += flags
        res.enriched += 1
        progress(f"{tag}: {len(changes)} fields filled" + (f", {len(flags)} dropped" if flags else ""))
