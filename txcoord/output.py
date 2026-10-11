"""Writes what a transaction coordinator sends and keeps: timeline, calendar file, checklist, emails and review."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from agentkit.tables import write_csv

from .check import Transaction
from .config import TCConfig

TIMELINE_COLUMNS = ["date", "weekday", "deadline", "status", "party", "how", "as_written", "checks"]
REVIEW_COLUMNS = ["file", "deadline", "must_fix", "check"]
TASKS = [  # (words in the deadline's name, the task)
    ("earnest", "Confirm the title or escrow company received the earnest money"),
    ("inspect", "Make sure the inspection is done and any objections are delivered in writing"),
    ("apprais", "Confirm the appraisal came in at or above the price"),
    (r"financ|loan|mortgage", "Get the written loan commitment from the lender"),
    (r"hoa|condo|association", "Get the HOA documents from the seller and send them to the buyer"),
    ("title", "Review the title commitment and send any objections"),
    ("disclos", "Get the seller's disclosures signed and delivered"),
    ("walk", "Schedule the final walk-through with the buyer's agent"),
    ("possession", "Confirm keys and possession with both agents"),
    (r"closing|settlement", "Confirm closing time, place, funds and the closing disclosure"),
]
PARTY_ROLES = {"buyer": ["buyer"], "seller": ["listing", "seller"], "lender": ["lender", "buyer"],
               "title": ["title", "escrow", "buyer", "listing"], "both": ["buyer", "listing"]}


def day(d: date) -> str:
    return d.strftime("%a %b %d, %Y").replace(" 0", " ")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "item"


def task_for(name: str) -> str:
    return next((t for words, t in TASKS if re.search(words, name, re.I)), name)


def timeline_rows(t: Transaction) -> list[dict]:
    return [{"date": d.due.isoformat() if d.due else "", "weekday": d.due.strftime("%A") if d.due else "",
             "deadline": d.name, "status": d.status, "party": d.party, "how": d.how, "as_written": d.term,
             "checks": "; ".join(("FIX: " if f.hold else "") + f.text for f in d.flags)} for d in t.deadlines]


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """iCalendar lines longer than 75 octets continue on the next line after a space."""
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def ics(t: Transaction, cfg: TCConfig, stamp: date) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//txcoord//contract deadlines//EN", "CALSCALE:GREGORIAN"]
    for d in t.deadlines:
        if not d.due:
            continue
        lines += ["BEGIN:VEVENT", f"UID:{slug(t.folder)}-{slug(d.name)}-{d.due:%Y%m%d}@txcoord",
                  f"DTSTAMP:{stamp:%Y%m%d}T000000Z", f"DTSTART;VALUE=DATE:{d.due:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{d.due + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_ics_text(f'{t.address or t.folder}: {d.name}')}",
                  f"DESCRIPTION:{_ics_text(f'{d.term} ({d.how})')}"]
        for n in cfg.remind_days:
            lines += ["BEGIN:VALARM", f"TRIGGER:-P{n}D", "ACTION:DISPLAY",
                      f"DESCRIPTION:{_ics_text(f'{d.name} due in {n} day' + ('s' if n != 1 else ''))}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def checklist(t: Transaction) -> str:
    out = [f"# {t.address or t.folder}: checklist", ""]
    who = ", ".join(t.buyers) or "?"
    out += [f"Buyers: {who}. Sellers: {', '.join(t.sellers) or '?'}. Price: "
            + (f"${t.price:,.0f}" if t.price else "?") + (f". Earnest money: ${t.earnest_money:,.0f}"
                                                          if t.earnest_money else "")
            + (f". Financing: {t.financing}" if t.financing else "") + ".", ""]
    out += ["- [ ] Open the file: save the contract and addenda, send the intro email with the key dates"]
    for d in t.deadlines:
        when = day(d.due) if d.due else "date to set"
        out.append(f"- [ ] {when}: {task_for(d.name)} ({d.name}; {d.party or 'both'})"
                   + (" - past: confirm it was met" if d.status == "past" else ""))
    holds = [f for f in t.all_flags() if f.hold]
    if holds:
        out += ["", "## Fix before sending anything", ""] + [f"- {f.text}" for f in holds]
    return "\n".join(out) + "\n"


def _recipients(t: Transaction, party: str) -> list[str]:
    keys = PARTY_ROLES.get(party, PARTY_ROLES["both"])
    return [c["email"] for c in t.contacts if c["email"] and any(k in c["role"].lower() for k in keys)]


def emails(t: Transaction, cfg: TCConfig, today: date, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.rglob("*.txt"):
        old.unlink()
    everyone = [c["email"] for c in t.contacts if c["email"]]
    dated = [d for d in t.deadlines if d.due]
    lines = [f"- {day(d.due)}: {d.name}" + (f" ({d.term})" if d.term and d.name != "Closing" else "") for d in dated]
    undated = [f"- {d.name}: {d.how}" for d in t.deadlines if not d.due]
    intro = (f"To: {', '.join(everyone)}\nSubject: {t.address or t.folder}: key dates\n\nHello all,\n\n"
             f"I'm the transaction coordinator for {t.address or t.folder}. Here are the key dates from the contract"
             + (" and addenda" if t.changes else "") + ":\n\n" + "\n".join(lines)
             + ("\n\nTo set when they happen:\n" + "\n".join(undated) if undated else "")
             + "\n\nPlease reply if anything differs from your copy.\n\n" + cfg.signature() + "\n")
    (folder / "intro.txt").write_text(intro, encoding="utf-8")
    n = 1
    rem = folder / "reminders"
    rem.mkdir(exist_ok=True)
    for d in dated:
        send = next((d.due - timedelta(days=k) for k in sorted(cfg.remind_days, reverse=True)
                     if d.due - timedelta(days=k) >= today), None)
        if send is None or d.name == "Acceptance":
            continue
        to = _recipients(t, d.party) or everyone
        body = (f"To: {', '.join(to)}\nSubject: Reminder: {d.name} due {day(d.due)}\n\nHello,\n\n"
                f"A reminder that the {d.name.lower()} for {t.address or t.folder} is due {day(d.due)}"
                + (f" ({d.term})" if d.term and d.name != "Closing" else "") + ".\n\n"
                f"{task_for(d.name)}, and let me know when it's done.\n\n{cfg.signature()}\n")
        (rem / f"{send.isoformat()}_{slug(d.name)}.txt").write_text(body, encoding="utf-8")
        n += 1
    return n


def write_transaction(out: Path, t: Transaction, cfg: TCConfig, today: date) -> dict:
    folder = out / slug(t.folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "timeline.csv", timeline_rows(t), TIMELINE_COLUMNS)
    (folder / "calendar.ics").write_text(ics(t, cfg, today), encoding="utf-8", newline="")
    (folder / "checklist.md").write_text(checklist(t), encoding="utf-8")
    n = emails(t, cfg, today, folder / "emails")
    return {"folder": folder, "emails": n}


def review_rows(ts: list[Transaction]) -> list[dict]:
    rows = []
    for t in ts:
        if t.error:
            rows.append({"file": t.folder, "deadline": "", "must_fix": "yes", "check": t.error})
            continue
        rows += [{"file": t.folder, "deadline": "", "must_fix": "yes" if f.hold else "", "check": f.text}
                 for f in t.flags]
        rows += [{"file": t.folder, "deadline": d.name, "must_fix": "yes" if f.hold else "", "check": f.text}
                 for d in t.deadlines for f in d.flags]
    rows.sort(key=lambda r: r["must_fix"] != "yes")
    return rows


def write_all(out: Path, ts: list[Transaction], cfg: TCConfig, today: date) -> dict:
    rows = []
    for t in ts:
        if not t.error:
            write_transaction(out, t, cfg, today)
            rows += [{"file": t.folder, **r} for r in timeline_rows(t)]
    rows.sort(key=lambda r: (r["date"] == "", r["date"]))
    write_csv(out / "all_deadlines.csv", rows, ["date", "file"] + [c for c in TIMELINE_COLUMNS if c != "date"])
    review = review_rows(ts)
    write_csv(out / "review.csv", review, REVIEW_COLUMNS)

    def plain(t: Transaction) -> dict:
        d = asdict(t)
        d["ready"] = t.ready
        return json.loads(json.dumps(d, default=str))
    (out / "results.json").write_text(json.dumps({"today": today.isoformat(), "transactions": [plain(t) for t in ts]},
                                                 indent=1))
    return {"review_items": len(review), "deadlines": len(rows)}
