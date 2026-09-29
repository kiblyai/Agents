"""Scores a company against the target-customer spec, citing the pages each claim came from."""

from __future__ import annotations

import re

from .config import ICP
from .models import CompanyResearch, Page

SYSTEM = """You are a careful B2B research analyst. You judge whether a company fits a client's target-customer profile and find reasons to contact it now.

Rules:
- Use only the page text provided (and web search results if any are attached). Never invent facts, numbers, dates or names.
- Every signal must quote or closely paraphrase the text it came from and give that page's exact URL as source_url.
- If the evidence is thin, lower the score and say why. A company with no evidence of fit scores 0-3.
- Score 0-10: 8-10 clear fit with a current signal; 5-7 plausible fit; 0-4 poor fit or excluded.
- why_now is one short sentence naming the single strongest signal, or "" if there is none.
- Reply with one JSON object only."""

SCHEMA_HINT = """{
  "company_name": "string",
  "summary": "one or two sentences on what the company does and for whom",
  "fits_icp": true,
  "score": 0,
  "score_reasons": ["short reason", "..."],
  "signals": [{"type": "hiring|funding|launch|expansion|leadership|other", "evidence": "quote or close paraphrase", "source_url": "exact page URL", "date": "YYYY-MM or null"}],
  "why_now": "one sentence or empty string",
  "disqualifiers": ["anything that matches the exclude list or rules the company out"]
}"""


def build_prompt(icp: ICP, domain: str, pages: list[Page], total_chars: int = 18000, web: bool = False) -> str:
    budget = total_chars
    blocks = []
    for i, page in enumerate(pages, 1):
        text = page.text[: max(0, budget)]
        budget -= len(text)
        blocks.append(f"### PAGE {i}\nURL: {page.url}\nTitle: {page.title}\n{text}")
        if budget <= 0:
            break
    web_note = "\nWeb search results may also be attached; cite their URLs the same way.\n" if web else ""
    return (
        f"TARGET CUSTOMER PROFILE\n{icp.as_prompt()}\n\n"
        f"COMPANY DOMAIN: {domain}\n{web_note}\n"
        + "\n\n".join(blocks)
        + f"\n\nReturn JSON in exactly this shape:\n{SCHEMA_HINT}"
    )


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def evidence_on_page(evidence: str, page_text: str, threshold: float = 0.6) -> bool:
    """True when most of the evidence's meaningful words appear on the cited page (a cheap hallucination check)."""
    words = _words(evidence)
    if not words:
        return False
    hay = page_text.lower()
    return sum(1 for w in words if w in hay) / len(words) >= threshold


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").replace("://www.", "://").lower()


def verify(research: CompanyResearch, pages: list[Page], web: bool = False) -> CompanyResearch:
    """Drop signals citing pages we never read; mark the rest verified if their evidence is on the page."""
    by_url = {_norm_url(p.url): p for p in pages}
    kept = []
    for sig in research.signals:
        page = by_url.get(_norm_url(sig.source_url))
        if page is None:
            if web and sig.source_url.startswith("http"):
                sig.verified = False
                kept.append(sig)
            continue
        sig.verified = evidence_on_page(sig.evidence, page.text)
        kept.append(sig)
    research.signals = kept
    if not any(s.verified for s in kept) and not web:
        research.why_now = ""
    return research


async def research_company(llm, icp: ICP, domain: str, pages: list[Page], *, web_results: int = 0) -> CompanyResearch:
    prompt = build_prompt(icp, domain, pages, web=bool(web_results))
    result = await llm.complete_json(system=SYSTEM, user=prompt, schema=CompanyResearch, web_results=web_results)
    return verify(result, pages, web=bool(web_results))
