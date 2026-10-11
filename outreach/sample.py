"""A synthetic campaign for a fictional managed-IT firm: leads, replies, meetings and the answer key, plus scoring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from agentkit.checks import quote_found

CENTRAL = timezone(timedelta(hours=-5))
SENDER = ("Sam Ortiz", "sam@trybrightline.example")

CAMPAIGN_TOML = """# Campaign settings for one client of the meetings service (fictional example).
[client]
name = "Brightline IT"
website = "brightline-it.example"
sending_domains = ["brightline-it.example", "trybrightline.example"]
sender_name = "Sam Ortiz"
sender_title = "Partner"
postal_address = "400 Market St, Suite 210, Springfield, IL 62701"
booking_link = "https://cal.example/brightline/intro"
offer = "Managed IT and cybersecurity for professional-services firms with 20-200 staff: help desk, Microsoft 365, backups and security training, for a flat monthly fee per user."
proof = [
  "Cut help-desk tickets by 38% in six months for a 60-person accounting firm",
  "Average first response time of 12 minutes during business hours",
  "Helped 14 clients pass cyber-insurance security questionnaires in 2025",
]
cta = "Worth a 20-minute call next week?"

[sequence]
days = [0, 3, 8]
max_words = [110, 60, 50]

[bar]
titles = ["owner", "partner", "ceo", "coo", "cfo", "president", "office manager", "operations", "it manager", "practice manager"]
min_employees = 20
max_employees = 200
countries = ["US"]

[billing]
platform_fee = 500
per_meeting = 250
"""

LEAD_HEADER = ["First Name", "Last Name", "Title", "Company", "Email", "Website", "# Employees", "Country", "City",
               "Industry", "Why Now", "Signal Source"]
# (lead fields..., expected: write?, why held)
LEADS = [
    (["Maya", "Chen", "Managing Partner", "Harlow & Pike CPAs", "maya.chen@harlowpike.example", "harlowpike.example",
      "60", "United States", "Springfield", "Accounting",
      "Hiring an IT support specialist (careers page, posted September 2026)", "https://harlowpike.example/careers"],
     True, ""),
    (["Derek", "Osei", "COO", "Ridgeline Engineering Group", "derek.osei@ridgeline-eng.example", "ridgeline-eng.example",
      "140", "United States", "Peoria", "Civil Engineering", "Opened a second office in Bloomington in August 2026",
      "https://ridgeline-eng.example/news"], True, ""),
    (["Priya", "Natarajan", "Office Manager", "Castle Rock Family Dental", "priya@castlerockdental.example",
      "castlerockdental.example", "25", "United States", "Decatur", "Dental Practice", "", ""], True, ""),
    (["Tom", "Becker", "Owner", "Becker Law PLLC", "tom@beckerlaw.example", "beckerlaw.example", "12", "United States",
      "Springfield", "Law Practice", "Added a second attorney in July 2026", "https://beckerlaw.example/team"],
     False, "outside the meeting bar"),
    (["Alicia", "Romero", "Partner", "Summit Ridge Financial Advisors", "alicia.romero@summitridgefa.example",
      "summitridgefa.example", "45", "United States", "Champaign", "Financial Services",
      "Announced a merger with Pine Street Wealth in September 2026", "https://summitridgefa.example/news"], True, ""),
    (["Grace", "Liu", "CEO", "Northstar Architects", "grace.liu@northstar-arch.example", "northstar-arch.example", "85",
      "Canada", "Windsor", "Architecture", "Won a design award for the Riverside Library in 2026",
      "https://northstar-arch.example/news"], False, "outside the meeting bar"),
    (["Jordan", "Ellis", "Operations Director", "Ellis & Grant Insurance", "jordan.ellis@", "ellisgrant.example", "30",
      "United States", "Springfield", "Insurance", "", ""], False, "no valid email"),
    (["Kevin", "Tran", "Owner", "Tran Physical Therapy", "kevin.tran@tranpt.example", "tranpt.example", "22",
      "United States", "Normal", "Physical Therapy", "Opened a third clinic in Normal in June 2026",
      "https://tranpt.example/locations"], True, ""),
    (["Marcus", "Hale", "IT Manager", "Lakeside Logistics", "marcus.hale@lakeside-logistics.example",
      "lakeside-logistics.example", "110", "United States", "Joliet", "Logistics",
      "Posted a job for a network administrator in September 2026", "https://lakeside-logistics.example/careers"],
     True, ""),
    (["Ben", "Carter", "CFO", "Westfield Dental Group", "ben.carter@westfielddental.example", "westfielddental.example",
      "70", "United States", "Bloomington", "Dental Practice", "Acquired two practices in Bloomington in 2026",
      "https://westfielddental.example/news"], True, ""),
    (["Sofia", "Marin", "Owner", "Marin Insurance Agency", "sofia@marininsurance.example", "marininsurance.example",
      "32", "United States", "Springfield", "Insurance", "Moved into a new office on Elm Street in July 2026",
      "https://marininsurance.example/about"], True, ""),
]


def _first_email(first: str, subject: str) -> str:
    return (f"On Mon, Oct 5, 2026 at 9:02 AM {SENDER[0]} <{SENDER[1]}> wrote:\n"
            f"> Hi {first}, a quick note about {subject.lower()}.\n> Worth a 20-minute call next week?")


def build_replies() -> list[dict]:
    t = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=CENTRAL)  # noqa: E731
    return [
        dict(file="01_maya_interested.eml", sender=("Maya Chen", "maya.chen@harlowpike.example"),
             subject="Re: help desk at Harlow & Pike", sent=t(6, 10, 14),
             body="Hi Sam,\n\nGood timing, honestly. Our one IT person is buried and we keep losing afternoons to "
                  "printer and Outlook problems. Thursday afternoon works for a call.\n\nMaya\n\n"
                  "Maya Chen | Managing Partner | Harlow & Pike CPAs\n"
                  "To unsubscribe from the Harlow & Pike tax newsletter, visit harlowpike.example/news.\n\n"
                  + _first_email("Maya", "help desk at Harlow & Pike"),
             truth=dict(category="interested", draft=True)),
        dict(file="02_derek_question.txt", sender=("Derek Osei", "derek.osei@ridgeline-eng.example"),
             subject="Re: Ridgeline's Bloomington office", sent=t(6, 14, 40),
             body="Sam - maybe. What does this cost per user per month? And do you support Autodesk Revit "
                  "licensing? Our engineers live in it.\n\nDerek",
             truth=dict(category="question", draft=True, needs_client=True)),
        dict(file="03_alicia_later.eml", sender=("Alicia Romero", "alicia.romero@summitridgefa.example"),
             subject="Re: Summit Ridge and Pine Street", sent=t(7, 8, 31),
             body="Hi Sam,\n\nThanks for the note. We're in the middle of the Pine Street merger, so this isn't a good "
                  "time. Reach out again in January and I'd be glad to talk then.\n\nAlicia\n\n"
                  + _first_email("Alicia", "Summit Ridge and Pine Street"),
             truth=dict(category="not_now", follow_up="2027-01-01", draft=True)),
        dict(file="04_priya_remove.txt", sender=("Priya Natarajan", "priya@castlerockdental.example"),
             subject="Re: IT for Castle Rock Family Dental", sent=t(7, 9, 5),
             body="Please remove me from your list.",
             truth=dict(category="unsubscribe", suppress="priya@castlerockdental.example")),
        dict(file="05_bounce_kevin.eml", sender=("Mail Delivery Subsystem", "mailer-daemon@trybrightline.example"),
             subject="Delivery Status Notification (Failure)", sent=t(5, 9, 3),
             body="Address not found\n\nYour message wasn't delivered to kevin.tran@tranpt.example because the address "
                  "couldn't be found, or is unable to receive mail.\n\nThe response from the remote server was:\n"
                  "550 5.1.1 The email account that you tried to reach does not exist.\n\n"
                  f"----- Original message -----\nFrom: {SENDER[0]} <{SENDER[1]}>\nTo: kevin.tran@tranpt.example\n"
                  "Subject: Tran PT's third clinic",
             truth=dict(category="bounce", suppress="kevin.tran@tranpt.example")),
        dict(file="06_marcus_away.eml", sender=("Marcus Hale", "marcus.hale@lakeside-logistics.example"),
             subject="Automatic reply: Lakeside's network admin search", sent=t(8, 7, 0),
             body="I am out of the office until Monday, October 19, with limited access to email. For anything "
                  "urgent, please contact Jen Walsh at jen.walsh@lakeside-logistics.example.",
             truth=dict(category="out_of_office", follow_up="2026-10-19")),
        dict(file="07_ben_referral.eml", sender=("Ben Carter", "ben.carter@westfielddental.example"),
             subject="Re: Westfield's two new practices", sent=t(8, 11, 20),
             body="Hi Sam,\n\nI don't handle IT here. Hannah Brooks, our practice operations manager, does - you can "
                  "reach her at hannah.brooks@westfielddental.example. Feel free to mention I sent you.\n\nBen\n\n"
                  + _first_email("Ben", "Westfield's two new practices"),
             truth=dict(category="referral", referral_email="hannah.brooks@westfielddental.example",
                        referral_name="Hannah Brooks", draft=True)),
        dict(file="08_sofia_happy.txt", sender=("Sofia Marin", "sofia@marininsurance.example"),
             subject="Re: Marin Insurance's new office", sent=t(8, 15, 2),
             body="Thanks, but we're happy with our current IT company.",
             truth=dict(category="objection", draft=True)),
    ]


MEETINGS_CSV = """Date,Email,Name,Title,Company,Status,Notes
2026-10-08,maya.chen@harlowpike.example,Maya Chen,Managing Partner,Harlow & Pike CPAs,held,30 minutes; wants a proposal
2026-10-09,derek.osei@ridgeline-eng.example,Derek Osei,COO,Ridgeline Engineering Group,no-show,
2026-10-13,hannah.brooks@westfielddental.example,Hannah Brooks,Practice Operations Manager,Westfield Dental Group,held,referred by Ben Carter
2026-10-14,tom@beckerlaw.example,Tom Becker,Owner,Becker Law PLLC,held,booked through the website form
"""


def _csv_line(values: list[str]) -> str:
    return ",".join(f'"{v}"' if ("," in v or '"' in v) else v for v in values)


def _eml(m: dict) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{m['sender'][0]} <{m['sender'][1]}>"
    msg["To"] = f"{SENDER[0]} <{SENDER[1]}>"
    msg["Subject"] = m["subject"]
    msg["Date"] = format_datetime(m["sent"])
    msg["Message-ID"] = f"<{Path(m['file']).stem}@{m['sender'][1].split('@')[1]}>"
    msg.set_content(m["body"] + "\n")
    return msg.as_bytes()


def _txt(m: dict) -> str:
    return (f"From: {m['sender'][0]} <{m['sender'][1]}>\nTo: {SENDER[0]} <{SENDER[1]}>\nSubject: {m['subject']}\n"
            f"Date: {format_datetime(m['sent'])}\n\n{m['body']}\n")


def make_sample(out: Path) -> dict:
    """Writes campaign.toml, leads.csv, replies/, meetings.csv and truth.json; returns the answer key."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "campaign.toml").write_text(CAMPAIGN_TOML, encoding="utf-8")
    lines = [_csv_line(LEAD_HEADER)] + [_csv_line(row) for row, _, _ in LEADS]
    (out / "leads.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    replies = build_replies()
    folder = out / "replies"
    folder.mkdir(exist_ok=True)
    for m in replies:
        path = folder / m["file"]
        if path.suffix == ".eml":
            path.write_bytes(_eml(m))
        else:
            path.write_text(_txt(m), encoding="utf-8")
    (out / "meetings.csv").write_text(MEETINGS_CSV, encoding="utf-8")
    blank = dict(category="", follow_up="", referral_email="", referral_name="", suppress="", draft=False,
                 needs_client=False)
    truth = {
        "note": "Answer key for the synthetic campaign made by `python -m outreach sample`. Fictional companies.",
        "leads": [{"email": row[4], "name": f"{row[0]} {row[1]}", "write": write, "held": held, "signal": row[10]}
                  for row, write, held in LEADS],
        "replies": [{"file": m["file"], **blank, **m["truth"]} for m in replies],
        "invoice": {"billable": ["maya.chen@harlowpike.example", "hannah.brooks@westfielddental.example"],
                    "total": 1000.0},
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(sequences: dict | None, replies: dict | None, truth: dict) -> dict:
    out: dict = {"leads": 0, "written_right": 0, "wrong_written": [], "ready": 0, "should_write": 0,
                 "personal_right": 0, "with_signal": 0, "not_personal": [], "replies": 0, "category_right": 0,
                 "wrong_category": [], "follow_up_right": 0, "follow_up_expected": 0, "wrong_follow_up": [],
                 "referral_right": 0, "referral_expected": 0, "suppress_right": 0, "suppress_expected": 0,
                 "missed_opt_outs": [], "wrong_suppressions": [], "draft_right": 0, "wrong_drafts": [],
                 "needs_client_right": 0, "needs_client_expected": 0}
    if sequences is not None:
        pred = {s["email"]: s for s in sequences["sequences"]}
        for t in truth["leads"]:
            p = pred.get(t["email"], {})
            out["leads"] += 1
            if bool(p.get("written")) == t["write"]:
                out["written_right"] += 1
            else:
                out["wrong_written"].append(f"{t['name']}: {'written' if p.get('written') else 'not written'} "
                                            f"(expected {'written' if t['write'] else 'held: ' + t['held']})")
            if t["write"]:
                out["should_write"] += 1
                out["ready"] += bool(p.get("ready"))
                if t["signal"]:
                    out["with_signal"] += 1
                    if p.get("personal_fact") and quote_found(p["personal_fact"], t["signal"], 0.75):
                        out["personal_right"] += 1
                    else:
                        out["not_personal"].append(f"{t['name']}: opened with '{p.get('personal_fact', '')}'")
    if replies is not None:
        pred = {r["file"]: r for r in replies["replies"]}
        for t in truth["replies"]:
            p = pred.get(t["file"], {})
            out["replies"] += 1
            cat = p.get("category", "")
            if cat == t["category"]:
                out["category_right"] += 1
            else:
                out["wrong_category"].append(f"{t['file']}: {cat or 'not read'} (expected {t['category']})")
            if t["follow_up"]:
                out["follow_up_expected"] += 1
                if p.get("follow_up") == t["follow_up"]:
                    out["follow_up_right"] += 1
                else:
                    out["wrong_follow_up"].append(f"{t['file']}: {p.get('follow_up') or 'none'} "
                                                  f"(expected {t['follow_up']})")
            if t["referral_email"]:
                out["referral_expected"] += 1
                out["referral_right"] += p.get("referral_email") == t["referral_email"]
            if t["suppress"]:
                out["suppress_expected"] += 1
                if p.get("suppress") == t["suppress"]:
                    out["suppress_right"] += 1
                else:
                    out["missed_opt_outs"].append(f"{t['file']}: {t['suppress']}")
            elif p.get("suppress"):
                out["wrong_suppressions"].append(f"{t['file']}: {p['suppress']}")
            if bool(p.get("draft")) == t["draft"]:
                out["draft_right"] += 1
            else:
                out["wrong_drafts"].append(f"{t['file']}: {'a draft' if p.get('draft') else 'no draft'} "
                                           f"(expected {'a draft' if t['draft'] else 'none'})")
            if t["needs_client"]:
                out["needs_client_expected"] += 1
                out["needs_client_right"] += bool(p.get("needs_client"))
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = []
    if s["leads"]:
        lines.append(f"Leads written or held back as expected: {_pct(s['written_right'], s['leads'])}")
        lines += [f"  {w}" for w in s["wrong_written"]]
        lines.append(f"Sequences ready to send (nothing to fix): {_pct(s['ready'], s['should_write'])}")
        lines.append(f"First lines built on the lead's signal: {_pct(s['personal_right'], s['with_signal'])}")
        lines += [f"  {w}" for w in s["not_personal"]]
    if s["replies"]:
        lines.append(f"Replies sorted right: {_pct(s['category_right'], s['replies'])}")
        lines += [f"  {w}" for w in s["wrong_category"]]
        lines.append(f"Opt-outs and bounces suppressed: {_pct(s['suppress_right'], s['suppress_expected'])}"
                     + (f"; wrongly suppressed: {len(s['wrong_suppressions'])}" if s["wrong_suppressions"] else ""))
        lines += [f"  MISSED: {w}" for w in s["missed_opt_outs"]] + [f"  wrong: {w}" for w in s["wrong_suppressions"]]
        lines.append(f"Follow-up dates right: {_pct(s['follow_up_right'], s['follow_up_expected'])}")
        lines += [f"  {w}" for w in s["wrong_follow_up"]]
        lines.append(f"Referrals captured: {_pct(s['referral_right'], s['referral_expected'])}")
        lines.append(f"Drafted (or not) as expected: {_pct(s['draft_right'], s['replies'])}")
        lines += [f"  {w}" for w in s["wrong_drafts"]]
        lines.append(f"Questions passed to the client: {_pct(s['needs_client_right'], s['needs_client_expected'])}")
    return "\n".join(lines) or "Nothing to score: run `write` and `replies` first."
