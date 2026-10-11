"""Writes the ranked notices, a short digest to read each morning, and the RFP compliance matrix."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from agentkit.tables import Sheet, write_csv, write_xlsx

from .match import Match
from .shred import Requirement

MATCH_COLUMNS = ["status", "fit", "notice", "title", "agency", "type", "set_aside", "naics", "place", "due", "why",
                 "matches", "risks", "questions", "filtered_out", "notes", "checks", "link"]
MATRIX_COLUMNS = ["id", "section", "source", "requirement", "type", "volume", "summary", "offeror_must",
                  "proposal_section", "owner", "status", "checks"]


def _q(items) -> str:
    return " | ".join(f"{q.point}: \"{q.quote}\"" for q in items)


def match_rows(matches: list[Match]) -> list[dict]:
    rows = []
    for m in matches:
        n = m.notice
        rows.append({"status": m.status, "fit": "" if m.fit is None else m.fit, "notice": n.id, "title": n.title,
                     "agency": " / ".join(x for x in (n.agency, n.office) if x), "type": n.type,
                     "set_aside": n.set_aside or n.set_aside_code, "naics": n.naics,
                     "place": ", ".join(x for x in (n.city, n.state) if x),
                     "due": n.deadline.isoformat() if n.deadline else n.deadline_text, "why": m.why,
                     "matches": _q(m.matches), "risks": _q(m.risks), "questions": " | ".join(m.questions),
                     "filtered_out": "; ".join(n.out), "notes": "; ".join(n.notes),
                     "checks": "; ".join([m.error] * bool(m.error) + m.checks), "link": n.link})
    return rows


def digest(matches: list[Match], company: str, today: date) -> str:
    """A short read for the morning: what to bid, what to answer, and what was filtered out."""
    keep = [m for m in matches if m.status in ("bid", "respond")]
    lines = [f"# {company}: SAM.gov notices, {today.strftime('%B %d, %Y').replace(' 0', ' ')}", ""]
    if not keep:
        lines.append("Nothing worth pursuing today.")
    for m in keep:
        n = m.notice
        lines += [f"## {m.status.upper()}: {n.title} (fit {m.fit})",
                  f"{n.agency} · {n.type} · {n.set_aside or 'no set-aside'} · NAICS {n.naics} · due "
                  f"{n.deadline.isoformat() if n.deadline else n.deadline_text}", "", m.why, ""]
        lines += [f"- Matches: {q.point} (\"{q.quote}\")" for q in m.matches]
        lines += [f"- Risk: {q.point} (\"{q.quote}\")" for q in m.risks]
        lines += [f"- Note: {x}" for x in n.notes] + [f"- Ask: {x}" for x in m.questions]
        lines += [f"- {n.link}" if n.link else "", ""]
    passed = [m for m in matches if m.status in ("pass", "not scored")]
    out = [m for m in matches if m.status == "filtered out"]
    lines += [f"Also read: {len(passed)} scored as a pass; {len(out)} filtered out by the rules "
              "(see matches.csv for why)."]
    return "\n".join(x for x in lines if x is not None) + "\n"


def write_matches(out: Path, matches: list[Match], company: str, today: date) -> list[dict]:
    rows = match_rows(matches)
    write_csv(out / "matches.csv", rows, MATCH_COLUMNS)
    write_xlsx(out / "matches.xlsx", [Sheet("Notices", [c.replace("_", " ").capitalize() for c in MATCH_COLUMNS],
                                            [[r[c] for c in MATCH_COLUMNS] for r in rows],
                                            {i for i, r in enumerate(rows) if r["status"] in ("bid", "respond")},
                                            {4: 40, 5: 30, 11: 50, 12: 60, 13: 50, 15: 40})])
    (out / "digest.md").write_text(digest(matches, company, today), encoding="utf-8")
    (out / "matches.json").write_text(json.dumps({"matches": rows}, indent=1))
    return rows


def write_shred(out: Path, reqs: list[Requirement], facts: list[dict], checks: list[str]) -> dict:
    rows = [{"id": r.id, "section": r.section, "source": r.source, "requirement": r.text, "type": r.type,
             "volume": r.volume, "summary": r.summary, "offeror_must": "yes" if r.offeror else "no",
             "proposal_section": "", "owner": "", "status": "", "checks": "; ".join(r.checks)} for r in reqs]
    write_csv(out / "compliance_matrix.csv", rows, MATRIX_COLUMNS)
    write_csv(out / "key_facts.csv", facts, ["fact", "value", "quote", "check"])
    write_xlsx(out / "compliance_matrix.xlsx", [
        Sheet("Compliance matrix", [c.replace("_", " ").capitalize() for c in MATRIX_COLUMNS],
              [[r[c] for c in MATRIX_COLUMNS] for r in rows], {i for i, r in enumerate(rows) if r["checks"]},
              {3: 18, 4: 70, 7: 30, 9: 18, 10: 14, 12: 40}),
        Sheet("Key facts", ["Fact", "Value", "Quote", "Check"], [[f["fact"], f["value"], f["quote"], f["check"]]
                                                                for f in facts],
              {i for i, f in enumerate(facts) if f["check"]}, {1: 26, 2: 30, 3: 70, 4: 30}),
    ])
    review = [{"item": r.id, "check": c} for r in reqs for c in r.checks] + [{"item": "key facts", "check": c}
                                                                           for c in checks]
    write_csv(out / "review_shred.csv", review, ["item", "check"])
    (out / "shred.json").write_text(json.dumps({"requirements": [asdict(r) for r in reqs], "facts": facts,
                                                "checks": checks}, indent=1))
    return {"requirements": len(rows), "offeror": sum(1 for r in reqs if r.offeror), "review": len(review)}
