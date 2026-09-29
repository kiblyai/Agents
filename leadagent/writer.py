"""Writes one opening line per company from its strongest verified signal.

The line is written at company level, so no personal data about contacts is sent to the model.
"""

from __future__ import annotations

import re

from .config import WriterConfig
from .models import CompanyResearch, FirstLine

SYSTEM = """You write the first line of a cold email to a company. It must mention one specific, verifiable fact about the company taken from the research provided, and nothing that is not in the research.
Write in second person to the reader ("you", "your team"). No greeting, no sign-off, no question marks, no exclamation marks.
Reply with one JSON object: {"line": "..."}"""


def problems(line: str, cfg: WriterConfig) -> list[str]:
    issues = []
    if not line.strip():
        issues.append("empty")
    words = len(line.split())
    if words > cfg.max_words:
        issues.append(f"{words} words; limit is {cfg.max_words}")
    lowered = line.lower()
    for phrase in cfg.banned_phrases:
        if phrase.lower() in lowered:
            issues.append(f"uses banned phrase '{phrase}'")
    if "!" in line:
        issues.append("contains an exclamation mark")
    if re.search(r"\{|\}|\[|\]", line):
        issues.append("contains template brackets")
    return issues


def build_prompt(research: CompanyResearch, cfg: WriterConfig) -> str:
    facts = [f"- {s.evidence} (source: {s.source_url})" for s in research.signals if s.verified] or [f"- {research.summary}"]
    return (
        f"Company: {research.company_name}\nWhat they do: {research.summary}\n"
        f"Strongest reason to reach out now: {research.why_now or 'none found; use a specific fact below'}\n"
        f"Verified facts:\n" + "\n".join(facts) +
        f"\n\nConstraints: at most {cfg.max_words} words; tone: {cfg.tone}; "
        f"never use these phrases: {', '.join(cfg.banned_phrases)}."
    )


async def write_first_line(llm, research: CompanyResearch, cfg: WriterConfig, model: str | None = None) -> tuple[str, str]:
    """Return (line, status) where status is 'ok' or 'needs_manual'."""
    prompt = build_prompt(research, cfg)
    result = await llm.complete_json(system=SYSTEM, user=prompt, schema=FirstLine, model=model, max_tokens=200)
    issues = problems(result.line, cfg)
    if not issues:
        return result.line.strip(), "ok"
    retry = prompt + f"\n\nYour previous line was: \"{result.line}\". Fix these problems: {'; '.join(issues)}."
    result = await llm.complete_json(system=SYSTEM, user=retry, schema=FirstLine, model=model, max_tokens=200)
    if problems(result.line, cfg):
        return result.line.strip(), "needs_manual"
    return result.line.strip(), "ok"
