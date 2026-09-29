"""Runs companies through read -> score -> write, with caching so an interrupted run resumes for free."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import ICP, Settings, WriterConfig
from .llm import DailyLimitError
from .models import CompanyResult
from .research import research_company
from .writer import write_first_line


async def process_company(domain: str, name: str, *, icp: ICP, writer_cfg: WriterConfig, settings: Settings,
                          llm, reader) -> CompanyResult:
    result = CompanyResult(domain=domain, input_name=name, icp_hash=icp.fingerprint())
    pages = await reader.read(domain)
    result.pages = [p.url for p in pages]
    if not pages:
        result.status = "unreachable"
        return result
    web_results = settings.web_results if settings.web_search else 0
    research = await research_company(llm, icp, domain, pages, web_results=web_results)
    result.research = research
    if research.fits_icp and research.score >= icp.min_score:
        result.status = "qualified"
        result.first_line, result.first_line_status = await write_first_line(
            llm, research, writer_cfg, model=settings.writer_model or None)
    else:
        result.status = "not_fit"
        result.first_line_status = "skipped"
    return result


def _cache_path(cache_dir: Path, domain: str) -> Path:
    return cache_dir / f"{domain}.json"


def load_cached(cache_dir: Path, domain: str, icp_hash: str) -> CompanyResult | None:
    path = _cache_path(cache_dir, domain)
    if not path.exists():
        return None
    try:
        cached = CompanyResult.model_validate_json(path.read_text())
    except ValueError:
        return None
    return cached if cached.icp_hash == icp_hash and cached.status in ("qualified", "not_fit") else None


@dataclass
class RunReport:
    results: list[CompanyResult] = field(default_factory=list)
    from_cache: int = 0
    stopped_reason: str = ""
    elapsed_s: float = 0.0


async def run(companies: list[tuple[str, str]], *, icp: ICP, writer_cfg: WriterConfig, settings: Settings,
              llm, reader, cache_dir: Path, limit: int | None = None,
              progress: Callable[[str], None] = print) -> RunReport:
    started = time.monotonic()
    todo = companies[:limit] if limit else list(companies)
    cache_dir.mkdir(parents=True, exist_ok=True)
    icp_hash = icp.fingerprint()
    queue: asyncio.Queue = asyncio.Queue()
    for item in todo:
        queue.put_nowait(item)
    results: dict[str, CompanyResult] = {}
    report = RunReport()
    stop = asyncio.Event()
    done = 0

    async def worker():
        nonlocal done
        while not stop.is_set():
            try:
                domain, name = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            cached = load_cached(cache_dir, domain, icp_hash)
            if cached is not None:
                results[domain] = cached
                report.from_cache += 1
                continue
            try:
                res = await process_company(domain, name, icp=icp, writer_cfg=writer_cfg, settings=settings,
                                            llm=llm, reader=reader)
            except DailyLimitError as e:
                report.stopped_reason = f"daily request limit reached; re-run tomorrow to continue ({str(e)[:120]})"
                stop.set()
                return
            except Exception as e:  # one bad company must not sink the run
                res = CompanyResult(domain=domain, input_name=name, status="error",
                                    error=f"{type(e).__name__}: {e}"[:300], icp_hash=icp_hash)
            if res.status in ("qualified", "not_fit"):  # unreachable/error are retried next run (no model cost)
                _cache_path(cache_dir, domain).write_text(res.model_dump_json(indent=2))
            results[domain] = res
            done += 1
            score = f" {res.research.score}/10" if res.research else ""
            why = f" - {res.research.why_now}" if res.research and res.research.why_now else ""
            progress(f"[{done + report.from_cache}/{len(todo)}] {domain}: {res.status}{score}{why}{(' - ' + res.error) if res.error else ''}")

    await asyncio.gather(*(worker() for _ in range(max(1, settings.concurrency))))
    report.results = [results[d] for d, _ in todo if d in results]
    report.elapsed_s = time.monotonic() - started
    return report


def summary(report: RunReport, usage, total_input: int) -> dict:
    counts: dict[str, int] = {}
    for r in report.results:
        counts[r.status] = counts.get(r.status, 0) + 1
    fresh = len(report.results) - report.from_cache
    return {
        "companies_in_file": total_input,
        "companies_done": len(report.results),
        "from_cache": report.from_cache,
        "status_counts": counts,
        "first_lines_needing_manual_edit": sum(1 for r in report.results if r.first_line_status == "needs_manual"),
        "stopped_early": report.stopped_reason,
        "llm_requests": usage.requests,
        "llm_repairs": usage.repairs,
        "llm_retries": usage.retries,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost_usd": round(usage.cost_usd, 4),
        "requests_per_new_company": round(usage.requests / fresh, 2) if fresh else None,
        "cost_per_new_company_usd": round(usage.cost_usd / fresh, 5) if fresh else None,
        "elapsed_s": round(report.elapsed_s, 1),
    }


def dump_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2))
