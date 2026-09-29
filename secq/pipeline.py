"""Questionnaire in, draft out: find sources per question, draft in batches, check, and write the files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from agentkit.llm import DailyLimitError

from .docs import KnowledgeBase
from .draft import SYSTEM, DraftBatch, DraftResult, Evidence, build_prompt, check, missing_result, overlap
from .search import BM25
from .sheet import Question

CACHE_VERSION = "2"


@dataclass
class Options:
    company: str
    batch_size: int = 8
    top_k: int = 4  # passages per question
    library_k: int = 2  # past answers per question
    max_tokens: int = 6000


@dataclass
class RunResult:
    results: list[DraftResult] = field(default_factory=list)
    stopped_reason: str = ""
    batches_from_cache: int = 0
    batch_errors: list[str] = field(default_factory=list)


def gather_evidence(questions: list[Question], kb: KnowledgeBase, opts: Options) -> list[Evidence]:
    p_index = BM25([f"{p.source} {p.text}" for p in kb.passages]) if kb.passages else None
    l_index = BM25([a.question for a in kb.library]) if kb.library else None
    out = []
    for n, q in enumerate(questions, 1):
        ev = Evidence()
        if p_index:
            for rank, (i, _) in enumerate(p_index.top(q.text, opts.top_k, context=q.section), 1):
                ev.passages[f"S{n}-{rank}"] = kb.passages[i]
        if l_index:
            hits = l_index.top(q.text, opts.library_k)
            best = hits[0][1] if hits else 0
            for rank, (i, score) in enumerate(hits, 1):
                # only close matches: the model is told to reuse their wording, so "customer data" alone is not enough
                if score >= 0.5 * best and overlap(q.text, kb.library[i].question) >= 0.5:
                    ev.library[f"L{n}-{rank}"] = kb.library[i]
        out.append(ev)
    return out


def _cache_path(cache_dir: Path, model: str, company: str, prompt: str) -> Path:
    h = hashlib.sha256(json.dumps([CACHE_VERSION, model, company, prompt]).encode()).hexdigest()[:24]
    return cache_dir / f"{h}.json"


async def draft_all(questions: list[Question], kb: KnowledgeBase, opts: Options, llm, cache_dir: Path,
                    progress: Callable[[str], None] = print) -> RunResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    evidence = gather_evidence(questions, kb, opts)
    items = [(f"Q{n}", q, ev) for n, (q, ev) in enumerate(zip(questions, evidence), 1)]
    run = RunResult()
    system = SYSTEM.format(company=opts.company)
    batches = [items[i:i + opts.batch_size] for i in range(0, len(items), opts.batch_size)]
    for b, batch in enumerate(batches, 1):
        if run.stopped_reason:
            run.results += [missing_result(q, "not processed yet: re-run to continue") for _, q, _ in batch]
            continue
        prompt = build_prompt(batch)
        path = _cache_path(cache_dir, llm.model, opts.company, prompt)
        drafted: DraftBatch | None = None
        if path.exists():
            try:
                drafted = DraftBatch.model_validate_json(path.read_text())
                run.batches_from_cache += 1
            except ValueError:
                drafted = None
        if drafted is None:
            try:
                drafted = await llm.complete_json(system=system, user=prompt, schema=DraftBatch, max_tokens=opts.max_tokens)
                path.write_text(drafted.model_dump_json(indent=1))
            except DailyLimitError as e:
                run.stopped_reason = f"daily request limit reached; re-run tomorrow to continue ({str(e)[:100]})"
                run.results += [missing_result(q, "not processed yet: re-run to continue") for _, q, _ in batch]
                continue
            except Exception as e:  # one bad batch must not sink the questionnaire
                run.batch_errors.append(f"batch {b}: {type(e).__name__}: {str(e)[:200]}")
                run.results += [missing_result(q, f"drafting failed ({type(e).__name__}); re-run to retry")
                                for _, q, _ in batch]
                progress(f"[batch {b}/{len(batches)}] failed: {type(e).__name__}")
                continue
        by_id = {d.id.strip(): d for d in drafted.answers}
        for pid, q, ev in batch:
            d = by_id.get(pid)
            run.results.append(check(d, q, ev) if d else missing_result(q, "the model skipped this question; re-run to retry"))
        flagged = sum(1 for r in run.results[-len(batch):] if r.needs_review)
        progress(f"[batch {b}/{len(batches)}] {len(batch)} questions drafted, {flagged} flagged for review")
    return run


def summary(run: RunResult, kb: KnowledgeBase, questions_total: int, prefilled: int, usage) -> dict:
    res = run.results
    return {
        "questions_found": questions_total,
        "skipped_already_answered": prefilled,
        "drafted": sum(1 for r in res if r.answer or r.explanation),
        "needs_review": sum(1 for r in res if r.needs_review),
        "confidence": {c: sum(1 for r in res if r.confidence == c) for c in ("high", "medium", "low")},
        "not_covered_by_documents": sum(1 for r in res if "Not covered" in r.review_note),
        "not_processed": sum(1 for r in res if "not processed" in r.review_note or "failed" in r.review_note),
        "stopped_early": run.stopped_reason,
        "batch_errors": run.batch_errors,
        "batches_from_cache": run.batches_from_cache,
        "knowledge_base": {"passages": len(kb.passages), "past_answers": len(kb.library),
                           "files_read": kb.files_read, "files_skipped": kb.files_skipped},
        "llm_requests": usage.requests,
        "llm_failed_attempts": usage.failed_attempts,
        "llm_repairs": usage.repairs,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost_usd": round(usage.cost_usd, 4),
        "models_used": usage.models,
    }


REVIEW_COLUMNS = ["needs_review", "confidence", "sheet", "row", "qid", "section", "question", "answer", "explanation",
                  "review_note", "sources"]


def review_rows(results: list[DraftResult]) -> list[dict]:
    rows = []
    order = {"low": 0, "medium": 1, "high": 2}
    for r in sorted(results, key=lambda x: (not x.needs_review, order.get(x.confidence, 0), x.sheet, x.row)):
        d = asdict(r)
        d["sources"] = "; ".join(r.source_labels)
        d["needs_review"] = "yes" if r.needs_review else ""
        rows.append(d)
    return rows
