"""Stand-ins for the model API and websites so tests run offline."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

import httpx


def reply(content: str, prompt_tokens: int = 100, completion_tokens: int = 20, cost: float = 0.0001):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, cost=cost),
    )


class FakeClient:
    """Mimics openai.AsyncOpenAI: `responder(kwargs)` returns the reply text (or raises)."""

    def __init__(self, responder: Callable[[dict], str]):
        self.calls: list[dict] = []
        self.responder = responder
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.responder(kwargs)
        return out if isinstance(out, SimpleNamespace) else reply(out)


async def no_sleep(_seconds: float) -> None:
    return None


def site_transport(pages: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    """Serve {url: (status, body)}; unknown hosts raise a connection error, unknown paths 404."""
    hosts = {httpx.URL(u).host for u in pages}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host not in hosts:
            raise httpx.ConnectError("unreachable", request=request)
        if url in pages:
            status, body = pages[url]
            ctype = "text/plain" if url.endswith("robots.txt") else "text/html"
            return httpx.Response(status, text=body, headers={"content-type": ctype})
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


def user_prompt(kwargs: dict) -> str:
    return next(m["content"] for m in kwargs["messages"] if m["role"] == "user")


def as_json(obj) -> str:
    return json.dumps(obj)
