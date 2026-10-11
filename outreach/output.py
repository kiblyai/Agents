"""Writes what a person checks and what the sequencer imports: sequences, the triage list, drafts and lists."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentkit.tables import Sheet, write_csv, write_xlsx

from .campaign import Campaign
from .replies import ReplyResult
from .sequence import Sequence


def sequence_columns(c: Campaign) -> list[str]:
    return (["email", "first_name", "last_name", "company_name", "website", "subject"]
            + [f"email_{i}" for i in range(1, c.steps + 1)])


def ready_rows(seqs: list[Sequence], c: Campaign) -> list[dict]:
    """Sequences with nothing to fix, as rows for the sequencer (map email_1..email_n to the campaign's steps)."""
    rows = []
    for s in seqs:
        if s.held or not s.bodies:
            continue
        lead = s.lead
        row = {"email": lead.email, "first_name": lead.first_name, "last_name": lead.last_name,
               "company_name": lead.company, "website": lead.website, "subject": s.subject}
        row.update({f"email_{i}": b for i, b in enumerate(s.bodies, 1)})
        rows.append(row)
    return rows


def status(s: Sequence) -> str:
    if s.lead.held:
        return "not written"
    if s.error:
        return s.error
    return "held: fix before sending" if s.held else "ready"


SEQUENCE_REVIEW_COLUMNS = ["lead", "email", "company", "status", "email_step", "must_fix", "check"]


def sequence_review(seqs: list[Sequence]) -> list[dict]:
    rows = []
    for s in seqs:
        base = {"lead": s.lead.name, "email": s.lead.email, "company": s.lead.company, "status": status(s)}
        if s.lead.held or s.error:
            rows.append({**base, "email_step": "", "must_fix": "yes" if s.error else "",
                         "check": "; ".join(s.lead.held) or s.error})
            continue
        for f in s.flags:
            rows.append({**base, "email_step": f.step or "", "must_fix": "yes" if f.hold else "", "check": f.text})
        for n in s.lead.notes:
            rows.append({**base, "email_step": "", "must_fix": "", "check": n})
    order = {"yes": 0, "": 1}
    rows.sort(key=lambda r: (order[r["must_fix"]], not r["status"].startswith("held")))
    return rows


def write_sequence_files(out: Path, seqs: list[Sequence], c: Campaign) -> dict:
    ready = ready_rows(seqs, c)
    write_csv(out / "sequences.csv", ready, sequence_columns(c))
    review = sequence_review(seqs)
    write_csv(out / "review_sequences.csv", review, SEQUENCE_REVIEW_COLUMNS)
    header = ["Lead", "Email", "Company", "Status", "Subject"] + [f"Email {i} (day {d})" for i, d in
                                                                 enumerate(c.days, 1)] + ["Checks"]
    rows, yellow = [], set()
    for i, s in enumerate(seqs):
        checks = [("FIX: " if f.hold else "") + (f"email {f.step}: " if f.step else "") + f.text for f in s.flags]
        rows.append([s.lead.name, s.lead.email, s.lead.company,
                     status(s) + (": " + "; ".join(s.lead.held) if s.lead.held else ""), s.subject,
                     *(s.bodies + [""] * (c.steps - len(s.bodies))), "\n".join(checks + s.lead.notes)])
        if s.held:
            yellow.add(i)
    write_xlsx(out / "sequences.xlsx", [Sheet("Sequences", header, rows, yellow,
                                              {1: 18, 2: 30, 3: 24, 4: 22, 5: 24, **{6 + i: 50 for i in range(c.steps)},
                                               6 + c.steps: 50})])
    (out / "sequences.json").write_text(json.dumps({"sequences": [
        {"email": s.lead.email, "name": s.lead.name, "status": status(s), "written": bool(s.bodies),
         "ready": not s.held and bool(s.bodies), "held": s.lead.held, "subject": s.subject,
         "personal_fact": s.personal_fact, "emails": s.bodies,
         "flags": [{"step": f.step, "hold": f.hold, "text": f.text} for f in s.flags], "notes": s.lead.notes,
         "error": s.error} for s in seqs]}, indent=1))
    return {"ready": len(ready), "review_items": len(review)}


# --- replies ------------------------------------------------------------------------------------------------------

TRIAGE_COLUMNS = ["priority", "category", "file", "from", "name", "company", "summary", "follow_up", "referral",
                  "draft", "must_fix", "checks"]


def triage_rows(results: list[ReplyResult]) -> list[dict]:
    rows = []
    for r in sorted(results, key=lambda x: x.priority):
        lead = r.lead
        rows.append({"priority": r.priority, "category": r.category.replace("_", " ") if not r.error else "not read",
                     "file": r.email.file, "from": r.email.sender, "name": lead.name if lead else r.email.sender_name,
                     "company": lead.company if lead else "", "summary": r.summary or r.quote,
                     "follow_up": r.follow_up,
                     "referral": " ".join(x for x in (r.referral_name, f"<{r.referral_email}>" if r.referral_email else "") if x),
                     "draft": r.draft_file, "must_fix": "yes" if r.error or any(f.hold for f in r.flags) else "",
                     "checks": "; ".join([r.error] * bool(r.error) + [f.text for f in r.flags])})
    return rows


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "reply"


def write_reply_files(out: Path, results: list[ReplyResult], c: Campaign) -> dict:
    drafts = out / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    for old in drafts.glob("*.txt"):
        old.unlink()
    for r in results:
        if not r.reply:
            continue
        subject = r.email.subject if r.email.subject.lower().startswith("re:") else f"Re: {r.email.subject}"
        path = drafts / f"{Path(r.email.file).stem}.txt"
        path.write_text(f"To: {r.email.sender}\nSubject: {subject}\n\n{r.reply}\n", encoding="utf-8")
        r.draft_file = f"drafts/{path.name}"
    triage = triage_rows(results)
    write_csv(out / "triage.csv", triage, TRIAGE_COLUMNS)
    sent = {r.email.file: r.email.day.isoformat() if r.email.day else "" for r in results}
    suppress = [{"email": r.suppress_email, "reason": r.suppress_reason, "date": sent[r.email.file],
                 "file": r.email.file} for r in results if r.suppress_email]
    write_csv(out / "suppress.csv", suppress, ["email", "reason", "date", "file"])
    follow = [{"email": r.email.sender, "name": r.lead.name if r.lead else r.email.sender_name,
               "company": r.lead.company if r.lead else "", "follow_up": r.follow_up,
               "reason": r.category.replace("_", " "), "other_contacts": "; ".join(r.other_contacts),
               "file": r.email.file} for r in results if r.follow_up]
    write_csv(out / "followups.csv", sorted(follow, key=lambda x: x["follow_up"]),
              ["follow_up", "email", "name", "company", "reason", "other_contacts", "file"])
    new = []
    for r in results:
        if r.category == "referral" and r.referral_email:
            first, _, last = r.referral_name.partition(" ")
            lead = r.lead
            new.append({"first_name": first, "last_name": last, "email": r.referral_email,
                        "company": lead.company if lead else "", "website": lead.website if lead else "",
                        "# employees": lead.employees if lead else "", "country": lead.country if lead else "",
                        "referred_by": f"{lead.name if lead else r.email.sender_name} <{r.email.sender}>",
                        "file": r.email.file})
    write_csv(out / "new_leads.csv", new, ["first_name", "last_name", "email", "company", "website", "# employees",
                                           "country", "referred_by", "file"])
    (out / "replies.json").write_text(json.dumps({"replies": [
        {"file": r.email.file, "from": r.email.sender, "category": r.category, "model_category": r.model_category,
         "summary": r.summary, "quote": r.quote, "follow_up": r.follow_up, "referral_name": r.referral_name,
         "referral_email": r.referral_email, "other_contacts": r.other_contacts, "needs_client": r.needs_client,
         "suppress": r.suppress_email, "draft": r.reply, "error": r.error,
         "flags": [{"hold": f.hold, "text": f.text} for f in r.flags]} for r in results]}, indent=1))
    return {"drafts": sum(1 for r in results if r.reply), "suppressed": len(suppress), "follow_ups": len(follow),
            "new_leads": len(new), "triage": triage}


def usage_stats(usage, runner) -> dict:
    return {"llm_requests": usage.requests, "llm_retries": usage.retries, "llm_repairs": usage.repairs,
            "llm_failed_attempts": usage.failed_attempts, "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens, "cost_usd": round(usage.cost_usd, 6),
            "models_used": usage.models, "from_cache": runner.from_cache, "stopped_reason": runner.stopped_reason}
