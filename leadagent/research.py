"""Scores a company against the target-customer spec, citing the pages each claim came from."""

from __future__ import annotations

import re
from datetime import date

from .config import ICP
from .models import CompanyResearch, Page, Signal

SYSTEM = """You are a careful B2B research analyst. You judge whether a company fits a client's target-customer profile and find reasons to contact it now.

Rules:
- Today's date is {today}.
- Use only the page text and LIST DATA provided (and web search results if any are attached). Never invent facts, numbers, dates or names.
- LIST DATA comes from the client's data provider. Trust it over website marketing copy for company size and funding stage.
- Judge each criterion as "met", "not_met" or "unknown", with short evidence. "met" always means good for fit. For not_excluded, "met" means the company is NOT in any excluded group.
- Use "not_met" when the evidence points against it, e.g. a Series C or $100M+ raised when the profile wants Seed-Series A, hundreds of employees or 50+ open roles when it wants a small company, or a consumer product when it wants B2B. Websites rarely state headcount: say "unknown" rather than guessing.
- If the company has been acquired, is being acquired, or has shut down, say so in disqualifiers.
- Every signal must quote or closely paraphrase its source and give the exact page URL as source_url (or "list" for LIST DATA). Add a headline: the signal in plain English, 4-12 words, e.g. "Hiring a Technical Account Executive". Give the date as YYYY-MM when shown.
- A signal dated more than 12 months before today is history, not a reason to reach out now.
- Prefer signals that match the profile's buying signals. Routine feature releases, changelog entries and integrations are weak signals: label them "product_update", and use "launch" only for a genuinely new product or market.
- fits_icp and score must agree: fits_icp is false whenever the score is below 6.
- Score 0-10: 8-10 every known criterion met and a current signal; 5-7 plausible fit; 0-4 any criterion not met, excluded, or no evidence of fit.
- why_now_index is the 0-based position in your signals list of the strongest current signal, or null.
- Reply with one JSON object only, no other text."""

SCHEMA_HINT = """{
  "company_name": "<name>",
  "summary": "<one or two sentences: what the company does and for whom>",
  "criteria": [{"name": "<industry|size|stage|geography|not_excluded>", "status": "<met|not_met|unknown>", "evidence": "<short>"}],
  "fits_icp": <true|false>,
  "score": <0-10>,
  "score_reasons": ["<short reason>"],
  "signals": [{"type": "<hiring|funding|leadership|expansion|launch|product_update|other>", "headline": "<4-12 plain words>", "evidence": "<quote or close paraphrase>", "source_url": "<exact page URL or list>", "date": "<YYYY-MM or null>"}],
  "why_now_index": <index into signals or null>,
  "disqualifiers": ["<only real disqualifiers: excluded group, acquired, shut down>"]
}"""

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def system_prompt(today: date) -> str:
    return SYSTEM.format(today=today.isoformat())


def list_text(facts: dict[str, str] | None) -> str:
    return "\n".join(f"{k}: {v}" for k, v in (facts or {}).items() if v)


def build_prompt(icp: ICP, domain: str, pages: list[Page], total_chars: int = 18000, web: bool = False,
                 facts: dict[str, str] | None = None) -> str:
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
        + (f"LIST DATA\n{list_text(facts)}\n\n" if list_text(facts) else "")
        + "\n\n".join(blocks)
        + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"
    )


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def evidence_on_page(evidence: str, page_text: str, threshold: float = 0.6) -> bool:
    """True when the evidence is supported by the cited text (a cheap hallucination check).

    Hard facts must match exactly: every number and every funding round ("Series B") in the evidence has to
    appear in the source. Beyond that, most of the evidence's meaningful words must appear there too.
    """
    ev = re.sub(r"[\s\-]+", " ", evidence.lower())
    hay = re.sub(r"[\s\-]+", " ", page_text.lower())
    for num in re.findall(r"\d[\d,.]*", ev):
        if num.strip(".,") not in hay:
            return False
    for rnd in re.findall(r"\bseries [a-z]\b", ev):
        if rnd not in hay:
            return False
    words = _words(ev)
    if not words:
        return False
    return sum(1 for w in words if w in hay) / len(words) >= threshold


def latest_date(*texts: str | None) -> tuple[int, int] | None:
    """Most recent (year, month) mentioned in the texts. A bare year counts as December (the lenient reading)."""
    found: list[tuple[int, int]] = []
    for text in texts:
        if not text:
            continue
        t = text.lower()
        for m in re.finditer(r"\b(20\d{2})[-/.](\d{1,2})\b", t):
            found.append((int(m.group(1)), int(m.group(2))))
        for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(20\d{2})\b", t):
            found.append((int(m.group(2)), MONTHS[m.group(1)]))
        if not found:
            for m in re.finditer(r"\b(20\d{2})\b", t):
                found.append((int(m.group(1)), 12))
    valid = [(y, mo) for y, mo in found if 1 <= mo <= 12]
    return max(valid) if valid else None


def is_stale(sig: Signal, today: date, months: int = 12) -> bool:
    when = latest_date(sig.date, sig.evidence)
    if when is None:
        return False
    age = (today.year - when[0]) * 12 + (today.month - when[1])
    return age > months


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").replace("://www.", "://").lower()


def verify(research: CompanyResearch, pages: list[Page], web: bool = False,
           facts: dict[str, str] | None = None) -> CompanyResearch:
    """Drop signals citing sources we never read; mark the rest verified if their evidence is really there.

    A headline that the source does not support is blanked, so the raw evidence is used instead.
    """
    by_url = {_norm_url(p.url): p.text for p in pages}
    listed = list_text(facts)
    kept = []
    for sig in research.signals:
        src = sig.source_url.strip().lower()
        text = listed if src in ("list", "list data") and listed else by_url.get(_norm_url(sig.source_url))
        if text is None:
            if web and sig.source_url.startswith("http"):
                sig.verified = False
                kept.append(sig)
            continue
        sig.verified = evidence_on_page(sig.evidence, text)
        if sig.headline and not evidence_on_page(sig.headline, text, threshold=0.5):
            sig.headline = ""
        kept.append(sig)
    research.signals = kept
    return research


NOT_REAL = re.compile(r"^\s*(none|n/?a|no|nothing|not applicable|-)?\s*\.?\s*$|^\s*(no|not|none)\b", re.I)


def _is_size(name: str) -> bool:
    return bool(re.search(r"size|employee|headcount", name, re.I))


def _is_stage(name: str) -> bool:
    return bool(re.search(r"stage|funding", name, re.I))


DEFAULT_PRIORITY = ["hiring", "funding", "leadership", "expansion", "launch", "product_update", "other"]


def _cap(research: CompanyResearch, cap: int, reason: str) -> None:
    research.fits_icp = research.fits_icp and cap >= 6
    if research.score > cap:
        research.score = cap
        research.score_reasons.append(f"capped at {cap}: {reason}")


def finalize(research: CompanyResearch, pages: list[Page], today: date, web: bool = False,
             priority: list[str] | None = None, facts: dict[str, str] | None = None,
             unknown_cap: int | None = None) -> CompanyResearch:
    """Apply the checks a model can't be trusted with: citations, dates, hard criteria, and the why-now.

    unknown_cap: if set, the score is capped there when neither company size nor funding stage is known.
    """
    priority = [p.lower() for p in (priority or DEFAULT_PRIORITY)]
    idx = research.why_now_index
    chosen = research.signals[idx] if idx is not None and 0 <= idx < len(research.signals) else None
    research = verify(research, pages, web=web, facts=facts)
    for sig in research.signals:
        sig.stale = is_stale(sig, today)

    research.disqualifiers = [d for d in research.disqualifiers if not NOT_REAL.search(d)]
    failed = [c for c in research.criteria if c.status == "not_met"]
    if failed:
        research.fits_icp = False
        _cap(research, 4, "; ".join(f"{c.name} not met ({c.evidence})" for c in failed))
    if research.disqualifiers:
        research.fits_icp = False
        _cap(research, 4, "disqualified: " + "; ".join(research.disqualifiers))
    if not research.fits_icp:
        # verdict and score disagree (e.g. "not a fit" but 9/10): trust the verdict, the cautious reading
        _cap(research, 4, "judged not a fit")
    if unknown_cap is not None:
        size = [c for c in research.criteria if _is_size(c.name)]
        stage = [c for c in research.criteria if _is_stage(c.name)]
        if all(c.status == "unknown" for c in size + stage):
            _cap(research, unknown_cap, "size and stage unknown; add employee and funding columns to your list, "
                                        "or check by hand")

    usable = [s for s in research.signals if s.verified and not s.stale]

    def rank(sig):
        t = sig.type.strip().lower()
        return (priority.index(t) if t in priority else len(priority), 0 if sig is chosen else 1)

    best = min(usable, key=rank) if usable else None
    research.why_now = ""
    if best is not None:
        text = (best.headline or best.evidence).strip()
        if len(text) > 200:
            text = text[:197].rstrip() + "..."
        research.why_now = text + (f" ({best.date})" if best.date else "")
    return research


async def research_company(llm, icp: ICP, domain: str, pages: list[Page], *, web_results: int = 0,
                           today: date | None = None, facts: dict[str, str] | None = None) -> CompanyResearch:
    today = today or date.today()
    prompt = build_prompt(icp, domain, pages, web=bool(web_results), facts=facts)
    result = await llm.complete_json(system=system_prompt(today), user=prompt, schema=CompanyResearch,
                                     web_results=web_results, max_tokens=4000)
    wants_size_or_stage = bool(icp.employee_range or icp.stages)
    unknown_cap = icp.min_score - 1 if icp.require_size_or_stage and wants_size_or_stage else None
    return finalize(result, pages, today, web=bool(web_results), priority=icp.signal_priority, facts=facts,
                    unknown_cap=unknown_cap)
