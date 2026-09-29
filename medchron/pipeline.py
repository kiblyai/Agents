"""Case file in, checked extractions out: batch the pages, ask the model, check every entry, cache the replies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import openai

from agentkit.llm import DailyLimitError

from .extract import Bill, CheckContext, Entry, PageBatch, build_prompt, check_bill, check_entry, system_prompt
from .records import CaseFile, Page

# Bump when the prompt, schema or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"
BUSY_LIMIT = 3  # batches in a row failing because the model is busy before the run stops


@dataclass
class Options:
    patient: str = ""
    doi: date | None = None
    batch_pages: int = 5
    batch_chars: int = 15000
    max_tokens: int = 6000
    gap_days: int = 30
    spot_check: float = 0.10


@dataclass
class RunResult:
    entries: list[Entry] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    no_content: set[int] = field(default_factory=set)
    other_patient: set[int] = field(default_factory=set)
    not_processed: set[int] = field(default_factory=set)
    stopped_reason: str = ""
    batches: int = 0
    batches_from_cache: int = 0
    batch_errors: list[str] = field(default_factory=list)


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"


def make_batches(pages: list[Page], opts: Options) -> list[list[Page]]:
    """Consecutive pages of one file, up to batch_pages pages and batch_chars characters per request."""
    batches: list[list[Page]] = []
    cur: list[Page] = []
    for p in pages:
        if p.needs_ocr:
            continue
        if cur and (p.file != cur[-1].file or len(cur) >= opts.batch_pages
                    or sum(len(x.text) for x in cur) + len(p.text) > opts.batch_chars):
            batches.append(cur)
            cur = []
        cur.append(p)
    if cur:
        batches.append(cur)
    return batches


def _context(case: CaseFile, first: Page) -> Page | None:
    """The page before a batch, so a visit that starts there is understood (its date and clinician)."""
    if first.n < 2:
        return None
    prev = case.page(first.n - 1)
    return prev if prev.file == first.file and not prev.needs_ocr else None


def _cache_path(cache_dir: Path, model: str, system: str, prompt: str) -> Path:
    h = hashlib.sha256(json.dumps([CACHE_VERSION, model, system, prompt]).encode()).hexdigest()[:24]
    return cache_dir / f"{h}.json"


async def extract_all(case: CaseFile, opts: Options, llm, cache_dir: Path, limit: int | None = None,
                      progress: Callable[[str], None] = print) -> RunResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    texts = {p.n: p.text for p in case.pages}
    ctx = CheckContext(patient=opts.patient, doi=opts.doi)
    system = system_prompt(opts.patient)
    pages = [p for p in case.pages if limit is None or p.n <= limit]
    batches = make_batches(pages, opts)
    run = RunResult(batches=len(batches), not_processed={p.n for p in case.pages if p.n not in {q.n for q in pages}})
    busy = 0
    for b, batch in enumerate(batches, 1):
        batch_pages = {p.n for p in batch}
        span = f"pages {batch[0].n}-{batch[-1].n}" if len(batch) > 1 else f"page {batch[0].n}"
        if run.stopped_reason:
            run.not_processed |= batch_pages
            continue
        context = _context(case, batch[0])
        prompt = build_prompt(batch, context)
        path = _cache_path(cache_dir, llm.model, system, prompt)
        got: PageBatch | None = None
        if path.exists():
            try:
                got = PageBatch.model_validate_json(path.read_text())
                run.batches_from_cache += 1
            except ValueError:
                got = None
        if got is None:
            try:
                got = await llm.complete_json(system=system, user=prompt, schema=PageBatch, max_tokens=opts.max_tokens)
                path.write_text(got.model_dump_json(indent=1))
                busy = 0
            except DailyLimitError as e:
                run.stopped_reason = f"daily request limit reached; re-run tomorrow to continue ({str(e)[:100]})"
                run.not_processed |= batch_pages
                continue
            except Exception as e:  # one bad batch must not sink the case
                run.batch_errors.append(f"{span}: {type(e).__name__}: {str(e)[:200]}")
                run.not_processed |= batch_pages
                progress(f"[batch {b}/{len(batches)}] {span}: failed ({type(e).__name__})")
                busy = busy + 1 if isinstance(e, openai.RateLimitError) else 0
                if busy >= BUSY_LIMIT:
                    run.stopped_reason = (f"the model was busy for {busy} batches in a row; re-run later, pick another "
                                          "model, or set MEDCHRON_FALLBACK_MODELS")
                continue
        allowed = batch_pages | ({context.n} if context else set())
        new_entries = [check_entry(e, texts, allowed, ctx) for e in got.entries]
        new_bills = [check_bill(x, texts, allowed, ctx) for x in got.bills]
        if context:  # anything only on the context page was already extracted with the previous batch
            new_entries = [e for e in new_entries if not (e.pages and set(e.pages) <= {context.n})]
            new_bills = [x for x in new_bills if not (x.pages and set(x.pages) <= {context.n})]
        run.entries += new_entries
        run.bills += new_bills
        run.no_content |= set(got.no_content_pages) & batch_pages
        run.other_patient |= set(got.other_patient_pages) & batch_pages
        flagged = sum(1 for x in [*new_entries, *new_bills] if x.needs_review)
        progress(f"[batch {b}/{len(batches)}] {span}: {plural(len(new_entries), 'entry', 'entries')}, "
                 f"{plural(len(new_bills), 'bill line')}, {flagged} flagged")
    return run
