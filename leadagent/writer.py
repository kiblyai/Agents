"""Writes one opening line per company from its strongest verified signal.

The line is written at company level, so no personal data about contacts is sent to the model.
"""

from __future__ import annotations

import re

from .config import WriterConfig
from .models import CompanyResearch, FirstLine

SYSTEM = """You write the opening line of a cold email. The reader works at the company described below, so speak to them about their own company.

The line must:
- address the reader directly ("you", "your team");
- mention one specific fact from the verified facts below, and nothing that is not there;
- not pitch, not ask a question, no greeting, no sign-off, no exclamation marks.

Good lines:
- Saw you're hiring an Account Executive and a Product Marketing Manager at the same time.
- Your changelog shows two launches this month, including new controls for the coding agent.

Bad lines (never write like these):
- Your team can consider Acme as a partner.   <- talks about the company instead of to it
- ...   <- placeholder, not a sentence

Reply with a JSON object whose only key is "line" and whose value is your sentence."""

SECOND_PERSON = re.compile(r"\byou(?:'re|r|rs|'ve|'ll)?\b", re.I)


def problems(line: str, cfg: WriterConfig) -> list[str]:
    issues = []
    stripped = line.strip()
    words = len(stripped.split())
    if not re.search(r"[A-Za-z]{3}", stripped):
        issues.append("not a sentence")
    elif words < cfg.min_words:
        issues.append(f"{words} words; minimum is {cfg.min_words}")
    if words > cfg.max_words:
        issues.append(f"{words} words; limit is {cfg.max_words}")
    if "..." in stripped or "…" in stripped:
        issues.append("contains an ellipsis placeholder")
    if stripped and not SECOND_PERSON.search(stripped):
        issues.append('does not speak to the reader ("you"/"your")')
    lowered = stripped.lower()
    for phrase in cfg.banned_phrases:
        if phrase.lower() in lowered:
            issues.append(f"uses banned phrase '{phrase}'")
    if "!" in stripped:
        issues.append("contains an exclamation mark")
    if re.search(r"[{}\[\]<>]", stripped):
        issues.append("contains template brackets")
    return issues


def build_prompt(research: CompanyResearch, cfg: WriterConfig) -> str:
    usable = [s for s in research.signals if s.verified and not s.stale]
    facts = [f"- {s.evidence}" + (f" ({s.date})" if s.date else "") for s in usable] or [f"- {research.summary}"]
    offer = (f"\nThe sender offers: {cfg.offer}. Pick the fact that makes that offer relevant, but do not mention the offer.\n"
             if cfg.offer else "")
    return (
        f"Company: {research.company_name}\nWhat they do: {research.summary}\n"
        f"Strongest reason to reach out now: {research.why_now or 'none; use the most specific fact below'}\n"
        f"Verified facts:\n" + "\n".join(facts) + "\n" + offer +
        f"\nConstraints: {cfg.min_words}-{cfg.max_words} words; tone: {cfg.tone}; "
        f"never use these phrases: {', '.join(cfg.banned_phrases)}."
    )


async def write_first_line(llm, research: CompanyResearch, cfg: WriterConfig, model: str | None = None) -> tuple[str, str]:
    """Return (line, status) where status is 'ok' or 'needs_manual'."""
    prompt = build_prompt(research, cfg)
    result = await llm.complete_json(system=SYSTEM, user=prompt, schema=FirstLine, model=model, max_tokens=1000)
    issues = problems(result.line, cfg)
    if not issues:
        return result.line.strip(), "ok"
    retry = prompt + f"\n\nYour previous line was: \"{result.line}\". Rewrite it to fix these problems: {'; '.join(issues)}."
    result = await llm.complete_json(system=SYSTEM, user=retry, schema=FirstLine, model=model, max_tokens=1000)
    if problems(result.line, cfg):
        return result.line.strip(), "needs_manual"
    return result.line.strip(), "ok"
