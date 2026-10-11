"""Writes the ranked job list and one proposal file per job worth bidding on, ready to paste into Upwork."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentkit.tables import Sheet, write_csv, write_xlsx

from .pipeline import Decision

CONNECT_PRICE = 0.15  # dollars per Connect (analysis/model.py)
JOB_COLUMNS = ["rank", "decision", "file", "title", "budget", "rate_after_fees", "worst_case_rate", "hours_estimate",
               "client_score", "client_notes", "proposals", "connects", "fit", "project_type", "skills_matched",
               "reasons", "notes", "requirements", "red_flags", "proposal_file", "draft_checks"]


def _money(x: float | None) -> str:
    return "" if x is None else f"${x:,.0f}/h"


def job_rows(decisions: list[Decision]) -> list[dict]:
    rows = []
    for n, d in enumerate(decisions, 1):
        j, dr = d.job, d.draft
        rows.append({
            "rank": n if d.decision != "skip" else "", "decision": d.decision, "file": j.file, "title": j.title,
            "budget": j.budget_text, "rate_after_fees": _money(d.rate_mid), "worst_case_rate": _money(d.rate_low),
            "hours_estimate": f"{d.hours[0]:g}-{d.hours[1]:g}" if d.hours else "", "client_score": d.client,
            "client_notes": "; ".join(d.client_notes), "proposals": j.proposals,
            "connects": "" if j.connects is None else j.connects, "fit": dr.fit if dr else "",
            "project_type": dr.project_type if dr else "", "skills_matched": ", ".join(d.skills),
            "reasons": "; ".join(d.reasons), "notes": "; ".join(d.notes),
            "requirements": " | ".join(f"{q.point}: \"{q.quote}\"" for q in dr.requirements) if dr else "",
            "red_flags": " | ".join(f"{q.point}: \"{q.quote}\"" for q in dr.red_flags) if dr else "",
            "proposal_file": "", "draft_checks": "; ".join(("FIX: " if c.hold else "") + c.text for c in dr.checks)
            if dr else ""})
    return rows


def proposal_text(d: Decision) -> str:
    j, dr = d.job, d.draft
    lines = [f"Job: {j.title}", f"File: {j.file}", f"Budget: {j.budget_text}" + (f" (about {_money(d.rate_mid)} "
                                                                                  "after fees)" if d.rate_mid else ""),
             f"Decision: {d.decision}" + (f" ({'; '.join(d.reasons)})" if d.reasons else ""), "",
             "--- Cover letter (paste into Upwork) ---", "", dr.proposal]
    if j.questions:
        lines += ["", "--- Screening questions ---"]
        for i, q in enumerate(j.questions):
            lines += ["", f"{i + 1}. {q}", dr.answers[i] if i < len(dr.answers) else "[NOT ANSWERED]"]
    holds = [c.text for c in dr.checks if c.hold]
    if holds:
        lines += ["", "--- Fix before sending ---"] + [f"- {x}" for x in holds]
    return "\n".join(lines) + "\n"


def write_outputs(out: Path, decisions: list[Decision]) -> dict:
    folder = out / "proposals"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.txt"):
        old.unlink()
    rows = job_rows(decisions)
    for row, d in zip(rows, decisions):
        if d.decision in ("bid", "maybe") and d.draft and d.draft.proposal:
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(d.job.file).stem) + ".txt"
            (folder / name).write_text(proposal_text(d), encoding="utf-8")
            row["proposal_file"] = f"proposals/{name}"
    write_csv(out / "jobs.csv", rows, JOB_COLUMNS)
    write_xlsx(out / "jobs.xlsx", [Sheet("Jobs", [c.replace("_", " ").capitalize() for c in JOB_COLUMNS],
                                         [[r[c] for c in JOB_COLUMNS] for r in rows],
                                         {i for i, r in enumerate(rows) if r["decision"] == "bid"},
                                         {4: 40, 10: 40, 16: 50, 17: 40, 18: 50, 19: 40, 21: 50})])
    bids = [d for d in decisions if d.decision == "bid"]
    connects = sum(d.job.connects or 0 for d in bids)
    (out / "jobs.json").write_text(json.dumps({"jobs": [
        {**r, "rate_mid": d.rate_mid, "answers": d.draft.answers if d.draft else [],
         "ready": bool(d.draft and d.draft.ready), "skip_reasons": d.job.out} for r, d in zip(rows, decisions)]},
        indent=1))
    return {"bids": len(bids), "maybe": sum(1 for d in decisions if d.decision == "maybe"),
            "skips": sum(1 for d in decisions if d.decision == "skip"), "connects": connects,
            "connects_cost": round(connects * CONNECT_PRICE, 2)}
