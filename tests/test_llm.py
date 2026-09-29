import asyncio

import httpx
import openai
import pytest
from pydantic import BaseModel

from leadagent.llm import LLM, DailyLimitError, LLMOutputError, RateLimiter, extract_json

from .fakes import FakeClient, no_sleep


class Out(BaseModel):
    ok: bool
    n: int = 0


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('Sure! ```json\n{"ok": true, "s": "a } b"}\n``` done') == {"ok": True, "s": "a } b"}
    assert extract_json('{"a": {"b": 1}} trailing {"c": 2}') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json(None)


def test_complete_json_repairs_once_then_succeeds():
    replies = iter(["not json at all", '{"ok": true, "n": 3}'])
    client = FakeClient(lambda kw: next(replies))
    llm = LLM(client, "m", rpm=0, sleep=no_sleep)
    out = asyncio.run(llm.complete_json(system="s", user="u", schema=Out))
    assert out == Out(ok=True, n=3)
    assert llm.usage.requests == 2 and llm.usage.repairs == 1
    assert client.calls[1]["messages"][-1]["role"] == "user"  # repair prompt appended
    assert client.calls[0]["extra_body"] == {"usage": {"include": True}}


def test_complete_json_gives_up_after_failed_repair():
    llm = LLM(FakeClient(lambda kw: '{"n": "x"}'), "m", rpm=0, sleep=no_sleep)
    with pytest.raises(LLMOutputError):
        asyncio.run(llm.complete_json(system="s", user="u", schema=Out))


def _rate_limit(msg: str) -> openai.RateLimitError:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.RateLimitError(msg, response=httpx.Response(429, request=req), body=None)


def test_daily_cap_stops_immediately_but_per_minute_cap_retries():
    def daily(kw):
        raise _rate_limit("Rate limit exceeded: free-models-per-day")

    llm = LLM(FakeClient(daily), "m", rpm=0, sleep=no_sleep)
    with pytest.raises(DailyLimitError):
        asyncio.run(llm.complete_json(system="s", user="u", schema=Out))
    assert llm.usage.retries == 0

    state = {"n": 0}

    def flaky(kw):
        state["n"] += 1
        if state["n"] < 3:
            raise _rate_limit("Rate limit exceeded: free-models-per-min")
        return '{"ok": true}'

    llm = LLM(FakeClient(flaky), "m", rpm=0, sleep=no_sleep)
    assert asyncio.run(llm.complete_json(system="s", user="u", schema=Out)).ok
    assert llm.usage.retries == 2


def test_web_search_plugin_only_when_asked():
    client = FakeClient(lambda kw: '{"ok": true}')
    llm = LLM(client, "m", rpm=0, sleep=no_sleep)
    asyncio.run(llm.complete_json(system="s", user="u", schema=Out, web_results=3))
    assert client.calls[0]["extra_body"]["plugins"] == [{"id": "web", "max_results": 3}]


def test_rate_limiter_spaces_requests():
    t = {"now": 0.0}
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        t["now"] += s

    limiter = RateLimiter(rpm=20, clock=lambda: t["now"], sleep=fake_sleep)

    async def go():
        for _ in range(3):
            await limiter.wait()

    asyncio.run(go())
    assert slept == [3.0, 3.0]  # 60s / 20 requests


def test_truncated_or_empty_reply_gets_more_room_once():
    from types import SimpleNamespace

    def respond(kw):
        if kw["max_tokens"] == 100:  # thinking model used the whole budget
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="length")],
                                   usage=None, model="some/thinking-model:free")
        return '{"ok": true}'

    client = FakeClient(respond)
    llm = LLM(client, "m", rpm=0, sleep=no_sleep)
    assert asyncio.run(llm.complete_json(system="s", user="u", schema=Out, max_tokens=100)).ok
    assert [c["max_tokens"] for c in client.calls] == [100, 200]
    assert llm.usage.retries == 1 and llm.usage.repairs == 0
    assert llm.usage.models == {"some/thinking-model:free": 1}


def test_output_error_names_the_model_that_failed():
    from types import SimpleNamespace

    client = FakeClient(lambda kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="nope"), finish_reason="stop")], usage=None, model="bad/model:free"))
    with pytest.raises(LLMOutputError, match="bad/model:free"):
        asyncio.run(LLM(client, "m", rpm=0, sleep=no_sleep).complete_json(system="s", user="u", schema=Out))
