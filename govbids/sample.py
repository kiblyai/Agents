"""A synthetic small contractor, 12 SAM.gov-style notices (a fictional agency) and an RFP, with the answer key."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from agentkit.pdfgen import text_pdf, wrap_page

TODAY = "2026-10-12"

COMPANY_TOML = """# A fictional small contractor for the govbids example.
[company]
name = "Cobalt Ridge Systems LLC"
naics = ["541512", "541519", "541330"]
certifications = ["SB", "SDVOSB"]
capabilities = ["IT help desk and end user support", "ServiceNow administration", "network modernization", "zero trust architecture", "cybersecurity operations", "cloud migration"]
past_performance = ["Tier 1-2 help desk for a 1,800-user federal agency office (2021-2025)", "ServiceNow ITSM rollout for a state transportation agency (2023)"]
places = ["VA", "MD", "DC"]
clearance = "secret"
min_days = 7
exclude_words = ["construction"]
"""

HEADER = ["NoticeId", "Title", "Sol#", "Department/Ind.Agency", "Office", "PostedDate", "Type", "SetASideCode",
          "SetASide", "ResponseDeadLine", "NaicsCode", "PopCity", "PopState", "Link", "Description"]
AGENCY = "Department of Example"
SB = ("SBA", "Total Small Business Set-Aside (FAR 19.5)")
NOTICES = [  # (id, title, office, posted, type, set-aside, deadline, naics, city, state, description), expected
    (("ELC-HD-2026-014", "IT Help Desk and End User Support Services", "Example Logistics Command", "2026-10-02",
      "Solicitation", ("SDVOSBC", "Service-Disabled Veteran-Owned Small Business (SDVOSB) Set-Aside (FAR 19.14)"),
      "2026-11-06T14:00:00-05:00", "541512", "Alexandria", "VA",
      "The Example Logistics Command needs Tier 1 and Tier 2 help desk support for about 2,400 users, including ticket "
      "triage, escalation, knowledge management and ServiceNow administration. Staff work on site in Alexandria, "
      "Virginia and must hold a Secret clearance. See the attached RFP."), ("bid", "")),
    (("EXA-ZT-0931", "Network Modernization - Zero Trust Architecture Support", "Example Cyber Directorate",
      "2026-10-05", "Combined Synopsis/Solicitation", SB, "2026-10-30T12:00:00-04:00", "541512", "Columbia", "MD",
      "Support the design and rollout of a zero trust architecture across 14 sites, including identity, network "
      "segmentation and continuous monitoring. Contractor staff must hold a Secret clearance. Prior experience with "
      "network modernization for a federal agency is required."), ("bid", "")),
    (("EXA-CN-2207", "Construction of Vehicle Maintenance Facility", "Example Facilities Office", "2026-10-01",
      "Solicitation", SB, "2026-11-15T14:00:00-05:00", "236220", "Springfield", "VA",
      "Design-build construction of a 20,000 square foot vehicle maintenance facility."), ("filtered out", "NAICS")),
    (("EXA-CY-4410", "Cybersecurity Assessment Services", "Example Cyber Directorate", "2026-10-06", "Solicitation",
      ("8A", "8(a) Set-Aside (FAR 19.8)"), "2026-11-10T14:00:00-05:00", "541519", "Arlington", "VA",
      "Independent security control assessments for 30 systems under the Risk Management Framework."),
     ("filtered out", "set aside")),
    (("EXA-CL-7714", "Cloud Migration Support for Financial Systems", "Example Finance Center", "2026-09-28",
      "Solicitation", SB, "2026-10-14T14:00:00-04:00", "541512", "Washington", "DC",
      "Migrate three financial applications to a government cloud environment."), ("filtered out", "days away")),
    (("EXA-AW-1123", "Data Center Maintenance", "Example Logistics Command", "2026-09-30", "Award Notice", ("", ""), "",
      "541512", "Alexandria", "VA", "Award of a data center maintenance contract."), ("filtered out", "notice type")),
    (("EXA-SS-0555", "Sources Sought: ServiceNow Platform Administration", "Example Headquarters", "2026-10-07",
      "Sources Sought", ("", ""), "2026-10-27T17:00:00-04:00", "541512", "Washington", "DC",
      "The agency seeks capability statements from firms that administer ServiceNow for federal customers: platform "
      "upgrades, workflow development and help desk integration. Responses will shape a future solicitation."),
     ("respond", "")),
    (("EXA-JN-3301", "Janitorial Services", "Example Facilities Office", "2026-10-03", "Solicitation", SB,
      "2026-11-01T14:00:00-04:00", "561720", "Alexandria", "VA", "Daily janitorial services for 3 office buildings."),
     ("filtered out", "NAICS")),
    (("EXA-GS-6108", "Geospatial Data Analytics Platform", "Example Land Office", "2026-10-04", "Solicitation", SB,
      "2026-11-12T14:00:00-07:00", "541512", "Denver", "CO", "Build and host a geospatial analytics platform for land "
      "records."), ("filtered out", "work is in CO")),
    (("EXA-EN-8802", "Engineering Support Services for Shipboard Systems", "Example Fleet Office", "2026-10-08",
      "Solicitation", ("SDVOSBC", "Service-Disabled Veteran-Owned Small Business (SDVOSB) Set-Aside (FAR 19.14)"),
      "2026-11-20T14:00:00-05:00", "541330", "Norfolk", "VA",
      "Mechanical and electrical engineering support for shipboard HVAC and power systems, including drawings, "
      "technical manuals and shipboard inspections."), ("pass", "")),
    (("EXA-HD-9920", "Help Desk Support - Classified Networks", "Example Signals Office", "2026-10-08", "Solicitation",
      SB, "2026-11-02T14:00:00-04:00", "541512", "Laurel", "MD",
      "Tier 1 help desk support for classified networks. All staff must hold an active TS/SCI clearance. On-site "
      "work, 24 hours a day."), ("pass", "")),
    (("EXA-RD-4471", "Records Digitization", "Example Records Office", "2026-10-02", "Solicitation",
      ("WOSB", "Women-Owned Small Business (WOSB) Program Set-Aside (FAR 19.15)"), "2026-11-09T14:00:00-04:00",
      "541519", "Richmond", "VA", "Scan and index 2 million paper records."), ("filtered out", "set aside")),
]


def _q(point: str, quote: str) -> dict:
    return {"point": point, "quote": quote}


SCORES = {  # what a perfect model answers for the notices the rules leave in
    "ELC-HD-2026-014": dict(fit=92, decision="bid", why="Help desk and ServiceNow work for 2,400 users is exactly our "
                            "core business, and the SDVOSB set-aside fits.",
                            matches=[{"capability": "IT help desk and end user support",
                                      "quote": "Tier 1 and Tier 2 help desk support for about 2,400 users"},
                                     {"capability": "ServiceNow administration", "quote": "ServiceNow administration"}],
                            risks=[{"risk": "on-site staff", "quote": "Staff work on site in Alexandria, Virginia"}],
                            questions=["Is there an incumbent, and when does the current contract end?"]),
    "EXA-ZT-0931": dict(fit=80, decision="bid", why="Zero trust and network modernization are core capabilities.",
                        matches=[{"capability": "zero trust architecture",
                                  "quote": "design and rollout of a zero trust architecture"},
                                 {"capability": "network modernization",
                                  "quote": "network modernization for a federal agency"}],
                        risks=[{"risk": "needs federal network modernization past performance",
                                "quote": "Prior experience with network modernization for a federal agency is required."}],
                        questions=[]),
    "EXA-SS-0555": dict(fit=85, decision="respond", why="A sources sought for ServiceNow administration: worth "
                        "answering to shape the solicitation.",
                        matches=[{"capability": "ServiceNow administration",
                                  "quote": "firms that administer ServiceNow for federal customers"}],
                        risks=[], questions=["Which ServiceNow modules are in scope?"]),
    "EXA-EN-8802": dict(fit=25, decision="pass", why="Mechanical and electrical shipboard engineering, not IT.",
                        matches=[], risks=[{"risk": "not our field", "quote": "Mechanical and electrical engineering "
                                                                              "support for shipboard HVAC and power "
                                                                              "systems"}], questions=[]),
    "EXA-HD-9920": dict(fit=60, decision="pass", why="Help desk work we do, but the TS/SCI requirement is above our "
                        "clearance.",
                        matches=[{"capability": "IT help desk", "quote": "Tier 1 help desk support for classified "
                                                                         "networks"}],
                        risks=[{"risk": "TS/SCI clearance above ours", "quote": "All staff must hold an active TS/SCI "
                                                                                "clearance."}], questions=[]),
}

RFP_PAGES = [[
    "## REQUEST FOR PROPOSALS ELC-HD-2026-014 (fictional sample)",
    "Department of Example, Example Logistics Command, Alexandria, VA",
    "IT Help Desk and End User Support Services",
    "Set-aside: Service-Disabled Veteran-Owned Small Business. NAICS 541512.",
    "Contract type: Firm-fixed-price.",
    "Period of performance: one base year and four option years.", "",
    "SECTION C - PERFORMANCE WORK STATEMENT",
    "C.1 Scope. The Contractor shall provide Tier 1 and Tier 2 help desk support for about 2,400",
    "users in Alexandria, Virginia.",
    "C.2 Hours. The help desk shall operate from 0600 to 1800 Eastern Time, Monday through Friday,",
    "excluding federal holidays.",
    "C.3 Response times. The Contractor shall respond to priority 1 tickets within 15 minutes and",
    "resolve them within 4 hours.",
    "C.4 Staffing. The Contractor shall provide a Project Manager with a current PMP certification.",
    "C.5 Reporting. The Contractor shall deliver a monthly status report by the fifth business day of",
    "each month.",
    "C.6 Security. All Contractor personnel shall hold a Secret clearance before starting work.",
    "C.7 Transition. The Contractor shall complete phase-in within 30 days after contract award.",
    "The Government will provide office space and equipment for on-site staff.",
], [
    "SECTION L - INSTRUCTIONS TO OFFERORS",
    "L.1 Proposals are due no later than November 6, 2026 at 2:00 PM Eastern Time.",
    "L.2 Questions must be submitted by email to the Contracting Officer no later than October 23,",
    "2026.",
    "L.3 Offerors shall submit proposals through the agency's electronic proposal portal. Email",
    "submissions will not be accepted.",
    "L.4 Volume I - Technical shall not exceed 20 pages. Volume II - Past Performance shall not",
    "exceed 10 pages. Volume III - Price has no page limit.",
    "L.5 Pages shall be 8.5 x 11 inches with 1-inch margins and 12-point Times New Roman font.",
    "L.6 The Technical Volume shall describe the offeror's approach to ticket triage, escalation and",
    "knowledge management.",
    "L.7 Offerors shall provide three past performance references for similar work within the last",
    "five years.",
    "L.8 The Price Volume shall include fully burdened labor rates for each labor category.", "",
    "SECTION M - EVALUATION FACTORS FOR AWARD",
    "M.1 Award will be made on a best value tradeoff basis. Technical is more important than Past",
    "Performance, which is more important than Price.",
    "M.2 The Government will evaluate the offeror's staffing plan and approach to meeting the",
    "response times in C.3.",
    "M.3 Proposals that exceed the page limits will not be evaluated beyond the limit.",
]]

TECH, PAST, PRICE = "Volume I - Technical", "Volume II - Past Performance", "Volume III - Price"
LABELS = [  # (a phrase that identifies the requirement, type, volume, offeror must)
    ("Tier 1 and Tier 2 help desk", "technical", TECH, True), ("help desk shall operate", "technical", TECH, True),
    ("priority 1 tickets", "technical", TECH, True), ("PMP certification", "staffing", TECH, True),
    ("monthly status report", "reporting", TECH, True), ("hold a Secret clearance", "security", TECH, True),
    ("complete phase-in", "schedule", TECH, True), ("Government will provide office space", "government", "", False),
    ("Questions must be submitted", "instruction", "", True), ("electronic proposal portal", "instruction", "", True),
    ("Email submissions will not be accepted", "instruction", "", True),
    ("Technical shall not exceed 20 pages", "format", TECH, True),
    ("Past Performance shall not exceed 10 pages", "format", PAST, True),
    ("Pages shall be 8.5 x 11", "format", "", True), ("ticket triage", "technical", TECH, True),
    ("three past performance references", "past performance", PAST, True),
    ("fully burdened labor rates", "pricing", PRICE, True), ("best value tradeoff", "evaluation", "", False),
    ("evaluate the offeror's staffing plan", "evaluation", TECH, False),
    ("exceed the page limits will not be evaluated", "evaluation", "", False),
]
FACTS = {
    "due": {"date": "2026-11-06", "time": "2:00 PM Eastern Time",
            "quote": "Proposals are due no later than November 6, 2026 at 2:00 PM Eastern Time."},
    "questions_due": {"date": "2026-10-23", "quote": "Questions must be submitted by email to the Contracting Officer "
                                                     "no later than October 23, 2026."},
    "submission": {"value": "the agency's electronic proposal portal; email is not accepted",
                   "quote": "Offerors shall submit proposals through the agency's electronic proposal portal."},
    "page_limits": [{"volume": TECH, "pages": 20, "quote": "Volume I - Technical shall not exceed 20 pages."},
                    {"volume": PAST, "pages": 10, "quote": "Volume II - Past Performance shall not exceed 10 pages."}],
    "format": {"value": "8.5 x 11 pages, 1-inch margins, 12-point Times New Roman",
               "quote": "Pages shall be 8.5 x 11 inches with 1-inch margins and 12-point Times New Roman font."},
    "evaluation": {"value": "best value tradeoff", "quote": "Award will be made on a best value tradeoff basis."},
    "contract_type": {"value": "Firm-fixed-price", "quote": "Contract type: Firm-fixed-price."},
    "period_of_performance": {"value": "one base year and four option years",
                              "quote": "Period of performance: one base year and four option years."},
}


def notices_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for (nid, title, office, posted, kind, (code, sa), due, naics, city, state, desc), _ in NOTICES:
        w.writerow([nid, title, nid, AGENCY, office, posted, kind, code, sa, due, naics, city, state,
                    f"https://sam.example/opp/{nid}", desc])
    return buf.getvalue()


def make_sample(out: Path) -> dict:
    """Writes company.toml, notices.csv, rfp/ and truth.json; returns the answer key."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "company.toml").write_text(COMPANY_TOML, encoding="utf-8")
    (out / "notices.csv").write_text(notices_csv(), encoding="utf-8")
    (out / "rfp").mkdir(exist_ok=True)
    pages = [p for lines in RFP_PAGES for p in wrap_page(lines)]
    (out / "rfp" / "RFP_ELC-HD-2026-014.pdf").write_bytes(text_pdf(pages))
    truth = {"note": "Answer key for the synthetic govbids example made by `python -m govbids sample`. Fictional "
                     f"agency and companies. Run with --today {TODAY}.",
             "today": TODAY,
             "notices": [{"id": n[0], "status": e[0], "filtered_reason": e[1]} for n, e in NOTICES],
             "scores": SCORES,
             "requirements": [{"phrase": p, "type": t, "volume": v, "offeror": o} for p, t, v, o in LABELS],
             "facts": FACTS,
             "fact_rows": ["Proposal due", "Questions due", "Submission", "Format", "Evaluation", "Contract type",
                           "Period of performance", f"Page limit: {TECH}", f"Page limit: {PAST}"]}
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(matches: dict | None, shred: dict | None, truth: dict) -> dict:
    out = {"notices": 0, "status_right": 0, "wrong_status": [], "reason_right": 0, "reason_expected": 0,
           "requirements": 0, "found": 0, "missed": [], "extra": 0, "labels_right": 0, "wrong_labels": [],
           "facts_expected": 0, "facts_found": 0, "missed_facts": []}
    if matches is not None:
        pred = {m["notice"]: m for m in matches["matches"]}
        for t in truth["notices"]:
            p = pred.get(t["id"], {})
            out["notices"] += 1
            if p.get("status") == t["status"]:
                out["status_right"] += 1
            else:
                out["wrong_status"].append(f"{t['id']}: {p.get('status', 'missing')} (expected {t['status']})")
            if t["filtered_reason"]:
                out["reason_expected"] += 1
                out["reason_right"] += t["filtered_reason"] in p.get("filtered_out", "")
    if shred is not None:
        reqs = shred["requirements"]
        used = set()
        for t in truth["requirements"]:
            out["requirements"] += 1
            r = next((r for r in reqs if t["phrase"].lower() in r["text"].lower()), None)
            if r is None:
                out["missed"].append(t["phrase"])
                continue
            used.add(r["id"])
            out["found"] += 1
            if r["type"] == t["type"] and r["offeror"] == t["offeror"]:
                out["labels_right"] += 1
            else:
                out["wrong_labels"].append(f"{t['phrase']}: {r['type']}/{r['offeror']} (expected {t['type']}/"
                                           f"{t['offeror']})")
        out["extra"] = len([r for r in reqs if r["id"] not in used])
        got = {f["fact"] for f in shred["facts"] if f["value"]}
        out["facts_expected"] = len(truth["fact_rows"])
        out["facts_found"] = len(got & set(truth["fact_rows"]))
        out["missed_facts"] = [f for f in truth["fact_rows"] if f not in got]
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = []
    if s["notices"]:
        lines.append(f"Notices sorted right (bid / respond / pass / filtered out): {_pct(s['status_right'], s['notices'])}")
        lines += [f"  {w}" for w in s["wrong_status"]]
        lines.append(f"Filtered out for the right reason: {_pct(s['reason_right'], s['reason_expected'])}")
    if s["requirements"]:
        lines.append(f"RFP requirements found: {_pct(s['found'], s['requirements'])}; other sentences: {s['extra']}")
        lines += [f"  missed: {m}" for m in s["missed"]]
        lines.append(f"Requirements labeled right: {_pct(s['labels_right'], s['found'])}")
        lines += [f"  {w}" for w in s["wrong_labels"]]
        lines.append(f"Key facts found: {_pct(s['facts_found'], s['facts_expected'])}")
        lines += [f"  missed: {m}" for m in s["missed_facts"]]
    return "\n".join(lines) or "Nothing to score: run `match` and `shred` first."
