"""Cached model requests for agents that ask once per item: replies are saved, and a daily limit stops the run cleanly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import openai
from pydantic import BaseModel

from .llm import DailyLimitError

T = TypeVar("T", bound=BaseModel)

NOT_YET = "not processed yet: re-run to continue"


@dataclass
class Runner:
    """Asks the model for one item at a time, reusing saved replies.

    After the provider's daily limit, or `busy_limit` busy failures in a row, every later `ask` returns NOT_YET
    without a request, so the run finishes with the rest marked for the next run.
    """

    llm: Any
    cache_dir: Path
    version: str  # the agent's CACHE_VERSION: bump it when prompts, schemas or checks change
    busy_limit: int = 3
    hint: str = ""  # what else to try when the model stays busy, e.g. "set OUTREACH_FALLBACK_MODELS"
    stopped_reason: str = ""
    from_cache: int = 0
    errors: int = 0
    _busy: int = 0

    def _path(self, system: str, prompt: str) -> Path:
        h = hashlib.sha256(json.dumps([self.version, self.llm.model, system, prompt]).encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.json"

    async def ask(self, system: str, prompt: str, schema: type[T], max_tokens: int = 3000) -> tuple[T | None, str]:
        """(reply, "") on success, or (None, why) when there is no reply this run."""
        if self.stopped_reason:
            return None, NOT_YET
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(system, prompt)
        if path.exists():
            try:
                got = schema.model_validate_json(path.read_text())
                self.from_cache += 1
                return got, ""
            except ValueError:
                pass
        try:
            got = await self.llm.complete_json(system=system, user=prompt, schema=schema, max_tokens=max_tokens)
        except DailyLimitError as e:
            self.stopped_reason = ("daily request limit reached; re-run after it resets to continue. "
                                   f"Provider says: {str(e)[:300]}")
            return None, NOT_YET
        except Exception as e:  # one bad item must not sink the run
            self.errors += 1
            self._busy = self._busy + 1 if isinstance(e, openai.RateLimitError) else 0
            if self._busy >= self.busy_limit:
                self.stopped_reason = (f"the model was busy for {self._busy} requests in a row; re-run later or pick "
                                       "another model" + (f", or {self.hint}" if self.hint else ""))
            return None, f"model request failed ({type(e).__name__}: {str(e)[:150]}); re-run to retry"
        self._busy = 0
        path.write_text(got.model_dump_json(indent=1))
        return got, ""
