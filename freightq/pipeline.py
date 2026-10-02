"""Emails in, checked and priced shipments out: one model request per email, cached, then code checks and pricing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import openai

from agentkit.llm import DailyLimitError

from .broker import BrokerConfig
from .extract import SYSTEM, EmailReading, EmailResult, build_prompt, check_reading
from .inbox import Email
from .pricing import LaneLoad, price

# Bump when the prompt, schema, checks or pricing change, so cached replies from older logic are redone.
CACHE_VERSION = "1"
BUSY_LIMIT = 3  # emails in a row failing because the model is busy before the run stops


@dataclass
class RunResult:
    results: list[EmailResult] = field(default_factory=list)
    stopped_reason: str = ""
    from_cache: int = 0
    errors: int = 0


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"


def _cache_path(cache_dir: Path, model: str, prompt: str) -> Path:
    h = hashlib.sha256(json.dumps([CACHE_VERSION, model, SYSTEM, prompt]).encode()).hexdigest()[:24]
    return cache_dir / f"{h}.json"


async def read_all(emails: list[Email], cfg: BrokerConfig, lanes: list[LaneLoad], llm, cache_dir: Path,
                   today: date | None = None, max_tokens: int = 3000,
                   progress: Callable[[str], None] = print) -> RunResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    run = RunResult()
    busy = 0
    for i, e in enumerate(emails, 1):
        tag = f"[{i}/{len(emails)}] {e.file}"
        if run.stopped_reason:
            run.results.append(EmailResult(email=e, error="not processed yet: re-run to continue"))
            continue
        prompt = build_prompt(e)
        path = _cache_path(cache_dir, llm.model, prompt)
        got: EmailReading | None = None
        if path.exists():
            try:
                got = EmailReading.model_validate_json(path.read_text())
                run.from_cache += 1
            except ValueError:
                got = None
        if got is None:
            try:
                got = await llm.complete_json(system=SYSTEM, user=prompt, schema=EmailReading, max_tokens=max_tokens)
                path.write_text(got.model_dump_json(indent=1))
                busy = 0
            except DailyLimitError as err:
                run.stopped_reason = f"daily request limit reached; re-run tomorrow to continue ({str(err)[:100]})"
                run.results.append(EmailResult(email=e, error="not processed yet: re-run to continue"))
                continue
            except Exception as err:  # one bad email must not sink the inbox
                run.errors += 1
                run.results.append(EmailResult(email=e, error=f"model request failed ({type(err).__name__}: "
                                                              f"{str(err)[:150]}); re-run to retry"))
                progress(f"{tag}: failed ({type(err).__name__})")
                busy = busy + 1 if isinstance(err, openai.RateLimitError) else 0
                if busy >= BUSY_LIMIT:
                    run.stopped_reason = (f"the model was busy for {busy} emails in a row; re-run later, pick another "
                                          "model, or set FREIGHTQ_FALLBACK_MODELS")
                continue
        r = check_reading(got, e, cfg, today)
        asof = e.day or today or date.today()
        for s in r.shipments:
            s.quote = price(s, lanes, cfg, asof)
        run.results.append(r)
        priced = sum(1 for s in r.shipments if s.quote.priced)
        flagged = sum(1 for s in r.shipments if s.flags) + (1 if r.flags else 0)
        progress(f"{tag}: {r.kind.replace('_', ' ')}, {plural(len(r.shipments), 'lane')}, {priced} priced, "
                 f"{flagged} flagged")
    return run
