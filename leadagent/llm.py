"""Thin client for OpenAI-compatible chat APIs (OpenRouter by default) that returns validated JSON."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

import openai
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

RETRYABLE = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)


class DailyLimitError(RuntimeError):
    """The provider's daily request cap is exhausted; retrying today is pointless."""


class LLMOutputError(RuntimeError):
    """The model did not return JSON matching the schema, even after a repair attempt."""


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    repairs: int = 0
    retries: int = 0
    models: dict[str, int] = field(default_factory=dict)  # which model actually served each request


class RateLimiter:
    """Spaces request starts evenly so a requests-per-minute cap is never exceeded."""

    def __init__(self, rpm: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self.clock = clock
        self.sleep = sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = self.clock()
            if self._next > now:
                await self.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


def extract_json(text: str | None) -> dict:
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    if not text:
        raise ValueError("empty reply")
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object in reply")


class LLM:
    def __init__(self, client, model: str, rpm: float = 18.0, *, openrouter: bool = True,
                 max_retries: int = 4, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.model = model
        self.openrouter = openrouter
        self.max_retries = max_retries
        self.sleep = sleep
        self.limiter = RateLimiter(rpm, clock=clock, sleep=sleep)
        self.usage = Usage()

    @classmethod
    def from_settings(cls, settings) -> "LLM":
        headers = {"X-Title": settings.app_title} if "openrouter.ai" in settings.base_url else None
        client = openai.AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url, default_headers=headers)
        return cls(client, settings.model, settings.rpm, openrouter="openrouter.ai" in settings.base_url)

    async def _create(self, *, model: str, messages: list[dict], max_tokens: int, web_results: int):
        extra_body: dict = {}
        if self.openrouter:
            extra_body["usage"] = {"include": True}
            if web_results:
                extra_body["plugins"] = [{"id": "web", "max_results": web_results}]
        for attempt in range(self.max_retries + 1):
            await self.limiter.wait()
            try:
                resp = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    extra_body=extra_body or None,
                )
                self._record(resp)
                return resp
            except openai.RateLimitError as e:
                if re.search(r"per[- ]day|daily", str(e), re.I):
                    raise DailyLimitError(str(e)) from e
                if attempt == self.max_retries:
                    raise
                self.usage.retries += 1
                await self.sleep(min(60.0, 10.0 * (attempt + 1)))
            except RETRYABLE:
                if attempt == self.max_retries:
                    raise
                self.usage.retries += 1
                await self.sleep(min(30.0, 2.0 ** (attempt + 1)))

    def _record(self, resp) -> None:
        self.usage.requests += 1
        served = getattr(resp, "model", None)
        if served:
            self.usage.models[served] = self.usage.models.get(served, 0) + 1
        u = getattr(resp, "usage", None)
        if u is None:
            return
        self.usage.prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
        self.usage.completion_tokens += getattr(u, "completion_tokens", 0) or 0
        cost = getattr(u, "cost", None)
        if cost is None:
            cost = (getattr(u, "model_extra", None) or {}).get("cost")
        if isinstance(cost, (int, float)):
            self.usage.cost_usd += float(cost)

    @staticmethod
    def _content(resp) -> tuple[str | None, str | None]:
        if not getattr(resp, "choices", None):
            return None, None
        choice = resp.choices[0]
        return choice.message.content, getattr(choice, "finish_reason", None)

    async def complete_json(self, *, system: str, user: str, schema: type[T], model: str | None = None,
                            max_tokens: int = 1500, web_results: int = 0) -> T:
        model = model or self.model
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        resp = await self._create(model=model, messages=messages, max_tokens=max_tokens, web_results=web_results)
        content, finish = self._content(resp)
        if finish == "length" or not (content or "").strip():
            # "thinking" models can spend the whole token budget before answering: give them room once
            self.usage.retries += 1
            max_tokens *= 2
            resp = await self._create(model=model, messages=messages, max_tokens=max_tokens, web_results=web_results)
            content, finish = self._content(resp)
        try:
            return schema.model_validate(extract_json(content))
        except (ValueError, ValidationError) as err:
            # one repair round: show the model its own reply and the error
            self.usage.repairs += 1
            messages += [
                {"role": "assistant", "content": content or ""},
                {"role": "user", "content": f"That reply was not valid JSON for the requested format ({str(err)[:300]}). "
                                            "Reply with only the corrected JSON object."},
            ]
            resp = await self._create(model=model, messages=messages, max_tokens=max_tokens, web_results=0)
            content, _ = self._content(resp)
            try:
                return schema.model_validate(extract_json(content))
            except (ValueError, ValidationError) as err2:
                served = getattr(resp, "model", None) or model
                raise LLMOutputError(f"{served}: {str(err2)[:400]}") from err2
