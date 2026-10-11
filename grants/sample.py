"""A synthetic nonprofit (a youth literacy program in a fictional Ohio county), 8 funders and the answer key."""

from __future__ import annotations

import json
from pathlib import Path

TODAY = "2026-10-12"

ORG_TOML = """# A fictional nonprofit for the grants example.
[org]
name = "Riverbend Youth Literacy Project"
mission = "Helps children in Millbrook County read at grade level by third grade, through tutoring, summer learning and family literacy events."
city = "Millbrook"
state = "OH"
service_area = ["Millbrook County"]
annual_budget = 640000
founded = 2012
ask_min = 10000
ask_max = 50000
focus = ["early literacy", "reading", "summer learning", "family literacy", "education"]
populations = ["children in grades 1 to 4", "low-income families"]

[[program]]
name = "Reading Buddies"
summary = "One-to-one tutoring for first to third graders reading below grade level"
budget = 210000

[[program]]
name = "Summer Reading Bridge"
summary = "A six-week summer reading program at 4 library sites"
budget = 95000

[[program]]
name = "Family Literacy Nights"
summary = "Evening family reading events with take-home books"
budget = 40000
"""

DOCS = {
    "about.md": """# About Riverbend Youth Literacy Project

## Mission
Riverbend Youth Literacy Project helps children in Millbrook County read at grade level by third grade, through tutoring, summer learning and family literacy events.

## History
Founded in 2012 by two retired teachers, Riverbend began with 14 volunteers at one elementary school. Today it works in 9 schools and 4 public library branches.

## People
A staff of 8 and about 120 trained volunteers. Executive Director Ana Torres has led the organization since 2019.

## Finances
Our annual budget is $640,000. In 2025, 45% of revenue came from foundations, 30% from individuals, 15% from government contracts and 10% from events.
""",
    "programs.md": """# Programs

## Reading Buddies
One-to-one tutoring for first to third graders who read below grade level, twice a week during the school year. In 2025-26, 340 students took part and 78% gained at least one full grade level in reading.

## Summer Reading Bridge
A six-week summer program at 4 library sites for children finishing grades 1 to 4. Each day includes small-group reading instruction, free books to take home and a healthy lunch. In summer 2026, 160 children attended and 82% kept or improved their spring reading level.

## Family Literacy Nights
Evening events at partner schools where families read together and take home book bundles. In 2025-26 we held 12 events with 450 family members.
""",
    "need.md": """# Community need

## Reading proficiency
In Millbrook County, 41% of third graders scored below proficient on the 2025 Ohio state reading test, compared with 35% statewide.

## Poverty
19% of Millbrook County children live below the poverty line, and 6 of the county's 11 elementary schools qualify for free meals for all students.

## Summer learning loss
Teachers in our partner schools report that many students lose two to three months of reading progress over the summer when they have no books or programs.
""",
    "evaluation.md": """# How we measure results

## Assessments
Every child is assessed with a standard reading inventory at enrollment and at the end of each program. Results are shared with families and partner schools.

## Summer program measures
For Summer Reading Bridge we compare each child's June and August reading levels and track attendance; children who attend at least 20 of the 30 program days count as completers.
""",
}

BUDGET_CSV = """Program,Item,Amount
Summer Reading Bridge,Site coordinator stipends,48000
Summer Reading Bridge,Books and materials,18000
Summer Reading Bridge,Transportation,14000
Summer Reading Bridge,Meals,9000
Summer Reading Bridge,Evaluation,6000
Reading Buddies,Program staff,150000
Reading Buddies,Volunteer training,25000
Reading Buddies,Materials,35000
"""

FUNDERS_CSV = """Funder,Website,Focus,Geography,Typical Grant,Deadline,Application,Contact
Riverbend Community Foundation,riverbendcf.example,"Education, youth development and literacy",Millbrook County,"$10,000 - $30,000",2026-11-15,Letter of inquiry,Maria Gomez
Great Lakes Literacy Fund,greatlakesliteracy.example,Early literacy and K-3 reading,"Ohio, Michigan and Indiana","$20,000 - $75,000",Rolling,Letter of inquiry,
Harmon Family Foundation,harmonff.example,Children and families,Ohio,"$25,000 - $100,000",2026-12-01,See guidelines,
Pacific Coast Youth Trust,pcyt.example,Youth education,"California, Oregon and Washington","$10,000 - $40,000",2026-11-30,Online application,
Ohio Arts Access Fund,ohioartsaccess.example,Arts education and access to the arts for underserved communities,Ohio,"$5,000 - $15,000",2026-12-01,Letter of inquiry,
Midwest Capital Projects Fund,mwcapital.example,"Capital projects: buildings and renovations",Midwest,"$75,000 - $250,000",Rolling,Full proposal,
Millbrook Rotary Charitable Fund,millbrookrotary.example,Youth and community projects,Millbrook County,"Up to $10,000",2026-10-01,Short application,
Buckeye Kids Reading Initiative,buckeyekidsreading.example,Summer learning and out-of-school reading programs,Ohio,"$15,000 - $40,000",2026-10-20,Full proposal,
"""

GUIDELINES = {
    "riverbend-community-foundation.md": """# Riverbend Community Foundation: Education Grants

The Foundation supports programs that help Millbrook County children succeed in school, with a priority on early literacy and summer learning. We fund nonprofit organizations with 501(c)(3) status serving Millbrook County residents.

Grants typically range from $10,000 to $30,000 for one year. Applicants should describe measurable outcomes and how results will be tracked.

Begin with a letter of inquiry of no more than 500 words. Letters are due November 15, 2026. Invited applicants submit a full proposal in January.
""",
    "great-lakes-literacy-fund.md": """# Great Lakes Literacy Fund

We invest in programs that get children reading on grade level by the end of third grade. We prioritize one-to-one and small-group tutoring in kindergarten through grade 3, especially in communities with high child poverty.

Organizations must have operated for at least three years. Grants of $20,000 to $75,000 support program costs; we do not fund capital projects. Letters of inquiry are reviewed on a rolling basis.
""",
    "harmon-family-foundation.md": """# Harmon Family Foundation

The Harmon Family Foundation makes grants by invitation only. The foundation does not accept unsolicited proposals or letters of inquiry.
""",
    "ohio-arts-access-fund.md": """# Ohio Arts Access Fund

The Fund supports arts education and access to the arts for underserved communities across Ohio: music, theater, dance and visual arts programs led by teaching artists. Programs must include a public performance or exhibition. Grants range from $5,000 to $15,000.
""",
    "buckeye-kids-reading-initiative.md": """# Buckeye Kids Reading Initiative: Summer Learning Grants

We fund summer and out-of-school reading programs for Ohio children in grades K-5. Strong applicants serve children from low-income families, provide free books, and measure reading levels at the start and end of summer.

Requests of $15,000 to $40,000 are considered. Full proposals are due October 20, 2026. Programs must run for at least five weeks.
""",
}

REQUEST_TOML = """# What to draft: one letter of inquiry (fictional example).
[request]
funder = "Riverbend Community Foundation"
program = "Summer Reading Bridge"
amount = 25000
type = "Letter of inquiry"
word_limit = 500
signer = "Ana Torres, Executive Director"
salutation = "Dear Ms. Gomez,"
sections = [
  {name = "Introduction and request", words = 80, hint = "who we are and the amount we ask for"},
  {name = "The need", words = 100, hint = "reading proficiency and summer learning loss in Millbrook County"},
  {name = "The program", words = 120, hint = "what Summer Reading Bridge does, for whom, where and when"},
  {name = "Outcomes and evaluation", words = 100, hint = "results so far and how we measure them"},
  {name = "Organization and budget", words = 80, hint = "our track record and how the grant will be used"},
]
"""


def _q(point: str, quote: str) -> dict:
    return {"point": point, "quote": quote}


# What a perfect model answers for each funder the rules leave in, and what the rules should rule out.
FUNDERS = [
    dict(funder="Riverbend Community Foundation", ruled_out="", status="strong", reply=dict(
        fit=88, best_program="Summer Reading Bridge",
        why="A local funder whose stated priorities are early literacy and summer learning in Millbrook County.",
        priorities_matched=[_q("early literacy and summer learning", "with a priority on early literacy and summer "
                                                                     "learning")],
        concerns=[], requirements=[_q("501(c)(3) serving Millbrook County", "We fund nonprofit organizations with "
                                  "501(c)(3) status serving Millbrook County residents."),
                                   _q("letter of inquiry under 500 words", "Begin with a letter of inquiry of no "
                                                                           "more than 500 words.")],
        suggested_ask=25000, application="letter of inquiry", deadline="2026-11-15")),
    dict(funder="Great Lakes Literacy Fund", ruled_out="", status="strong", reply=dict(
        fit=85, best_program="Reading Buddies",
        why="Funds K-3 tutoring that gets children reading on grade level by third grade, which is Reading Buddies.",
        priorities_matched=[_q("one-to-one tutoring in K-3", "We prioritize one-to-one and small-group tutoring in "
                                                             "kindergarten through grade 3")],
        concerns=[_q("no capital projects", "we do not fund capital projects")],
        requirements=[_q("three years in operation", "Organizations must have operated for at least three years.")],
        suggested_ask=40000, application="letter of inquiry", deadline="rolling")),
    dict(funder="Harmon Family Foundation", ruled_out="invitation only", status="ruled out", reply=None),
    dict(funder="Pacific Coast Youth Trust", ruled_out="outside its geography", status="ruled out", reply=None),
    dict(funder="Ohio Arts Access Fund", ruled_out="", status="weak", reply=dict(
        fit=20, best_program="", why="Funds arts programs led by teaching artists, not reading programs.",
        priorities_matched=[], concerns=[_q("needs a public performance", "Programs must include a public "
                                                                           "performance or exhibition.")],
        requirements=[], suggested_ask=None, application="letter of inquiry", deadline="2026-12-01")),
    dict(funder="Midwest Capital Projects Fund", ruled_out="grants start at", status="ruled out", reply=None),
    dict(funder="Millbrook Rotary Charitable Fund", ruled_out="has passed", status="ruled out", reply=None),
    dict(funder="Buckeye Kids Reading Initiative", ruled_out="", status="strong", reply=dict(
        fit=90, best_program="Summer Reading Bridge",
        why="A summer reading funder for Ohio children in grades K-5 that values free books and reading assessments.",
        priorities_matched=[_q("summer reading for grades K-5", "We fund summer and out-of-school reading programs for "
                                                                "Ohio children in grades K-5."),
                            _q("free books and reading levels", "provide free books, and measure reading levels at the "
                                                                "start and end of summer")],
        concerns=[], requirements=[_q("at least five weeks", "Programs must run for at least five weeks.")],
        suggested_ask=30000, application="full proposal", deadline="2026-10-20")),
]


def make_sample(out: Path) -> dict:
    """Writes org.toml, docs/, budget.csv, funders.csv, guidelines/, request.toml and truth.json."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "org.toml").write_text(ORG_TOML, encoding="utf-8")
    (out / "docs").mkdir(exist_ok=True)
    for name, text in DOCS.items():
        (out / "docs" / name).write_text(text, encoding="utf-8")
    (out / "guidelines").mkdir(exist_ok=True)
    for name, text in GUIDELINES.items():
        (out / "guidelines" / name).write_text(text, encoding="utf-8")
    (out / "budget.csv").write_text(BUDGET_CSV, encoding="utf-8")
    (out / "funders.csv").write_text(FUNDERS_CSV, encoding="utf-8")
    (out / "request.toml").write_text(REQUEST_TOML, encoding="utf-8")
    truth = {"note": "Answer key for the synthetic grants example made by `python -m grants sample`. Fictional "
                     f"organizations. Run with --today {TODAY}.",
             "today": TODAY, "funders": FUNDERS,
             "draft": {"funder": "Riverbend Community Foundation", "amount": 25000, "sections": 5}}
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(prospects: dict | None, draft: dict | None, truth: dict) -> dict:
    out = {"funders": 0, "status_right": 0, "wrong_status": [], "ruled_out_right": 0, "ruled_out_expected": 0,
           "program_right": 0, "program_expected": 0, "wrong_program": [], "draft": None}
    if prospects is not None:
        pred = {p["funder"]: p for p in prospects["prospects"]}
        for t in truth["funders"]:
            p = pred.get(t["funder"], {})
            out["funders"] += 1
            if p.get("status") == t["status"]:
                out["status_right"] += 1
            else:
                out["wrong_status"].append(f"{t['funder']}: {p.get('status', 'missing')} (expected {t['status']})")
            if t["ruled_out"]:
                out["ruled_out_expected"] += 1
                out["ruled_out_right"] += t["ruled_out"] in p.get("ruled_out", "")
            if t["reply"] and t["reply"]["best_program"]:
                out["program_expected"] += 1
                if p.get("best_program") == t["reply"]["best_program"]:
                    out["program_right"] += 1
                else:
                    out["wrong_program"].append(f"{t['funder']}: {p.get('best_program') or 'none'} "
                                                f"(expected {t['reply']['best_program']})")
    if draft is not None:
        t = truth["draft"]
        out["draft"] = {"funder_right": draft["funder"] == t["funder"], "sections": len(draft["sections"]),
                        "sections_expected": t["sections"], "ready": draft["ready"], "words": draft["words"],
                        "must_fix": [f["text"] for f in draft["flags"] if f["hold"]],
                        "checks": [f["text"] for f in draft["flags"] if not f["hold"]]}
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = []
    if s["funders"]:
        lines.append(f"Funders sorted right (strong / possible / weak / ruled out): {_pct(s['status_right'], s['funders'])}")
        lines += [f"  {w}" for w in s["wrong_status"]]
        lines.append(f"Ruled out for the right reason: {_pct(s['ruled_out_right'], s['ruled_out_expected'])}")
        lines.append(f"Best program right: {_pct(s['program_right'], s['program_expected'])}")
        lines += [f"  {w}" for w in s["wrong_program"]]
    d = s["draft"]
    if d:
        lines.append(f"Draft: {d['sections']} of {d['sections_expected']} sections, {d['words']} words, "
                     + ("ready to edit" if d["ready"] else f"{len(d['must_fix'])} to fix"))
        lines += [f"  MUST FIX: {w}" for w in d["must_fix"]] + [f"  check: {w}" for w in d["checks"]]
    return "\n".join(lines) or "Nothing to score: run `prospect` and `draft` first."
