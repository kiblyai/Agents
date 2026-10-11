"""Three synthetic purchase contracts (fictional parties) with the answer key, plus scoring."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentkit.pdfgen import text_pdf, wrap_page

TODAY = "2026-10-12"

TC_TOML = """# Transaction coordinator settings (fictional example).
[tc]
name = "Lakeview Transaction Services"
coordinator = "Jamie Lee"
email = "jamie@lakeviewtc.example"
days_mean = "calendar"          # what "days" means when a contract doesn't say
roll_to_business_day = true     # when a contract doesn't say: weekend or holiday deadlines move to the next business day
federal_holidays = true
remind_days = [3, 1]            # reminder emails this many days before each deadline
soon_days = 5
"""

HEAD = ["## RESIDENTIAL PURCHASE AGREEMENT", "(Sample form for testing. Fictional parties and property.)", ""]
TITLE_CO = "Title: Northcoast Title Agency, closings@northcoasttitle.example"
RACHEL = "Buyer's agent: Rachel Moore, Harbor Realty, rachel.moore@harborrealty.example"
TOM = "Listing agent: Tom Reyes, Lakeview Homes Group, tom.reyes@lakeviewhomes.example"
LENDER = "Lender: Erie Shore Bank, loans@erieshorebank.example"
ROLL = ["\"Days\" means calendar days. A deadline that falls on a Saturday, Sunday or",
        "legal holiday is extended to the next business day."]

MAPLE = HEAD + [
    "1. PARTIES. Buyers: Daniel Kim and Sofia Kim. Seller: Patricia Owens.",
    "2. PROPERTY. 101 Maple Avenue, Lakeview, OH 44095.",
    "3. PURCHASE PRICE. The purchase price is Four Hundred Twenty-Five Thousand Dollars",
    "($425,000).",
    "4. EARNEST MONEY. Buyer shall deliver earnest money of $10,000 to Northcoast Title Agency",
    "within three (3) business days after the Acceptance Date.",
    "5. FINANCING. This offer is contingent on Buyer obtaining a conventional loan of $340,000.",
    "Buyer shall deliver a written loan commitment no later than thirty (30) days after the",
    "Acceptance Date.",
    "6. APPRAISAL. The property must appraise at or above the purchase price. The appraisal",
    "contingency expires twenty-one (21) days after the Acceptance Date.",
    "7. INSPECTIONS. Buyer may inspect the property and shall deliver any written objections",
    "within ten (10) days after the Acceptance Date.",
    "8. HOMEOWNERS ASSOCIATION. Seller shall deliver the HOA documents within five (5) days after",
    "the Acceptance Date.",
    "9. TITLE. Buyer shall deliver any title objections within seven (7) days after receipt of the",
    "title commitment.",
    "10. CLOSING. Closing shall take place on or before November 20, 2026, at the offices of",
    "Northcoast Title Agency. Possession shall be delivered at closing.",
    "11. FINAL WALK-THROUGH. Buyer may conduct a final walk-through within 24 hours before closing.",
    "12. TIME PERIODS. Unless stated otherwise, \"days\" means calendar days. If a deadline falls on",
    "a Saturday, Sunday or legal holiday, it is extended to the next business day.",
    "13. ACCEPTANCE. This agreement was accepted and became binding on October 2, 2026 (the",
    "Acceptance Date).", "", RACHEL, TOM, LENDER, TITLE_CO,
]
MAPLE_ADDENDUM = """ADDENDUM 1 to the Residential Purchase Agreement for 101 Maple Avenue, Lakeview, OH 44095,
dated October 9, 2026.

The Closing Date in Section 10 is changed to November 25, 2026. All other terms remain the same.

Signed by Buyers and Seller on October 9, 2026.
"""
BIRCH = HEAD + [
    "1. PARTIES. Buyer: Morgan Ellis. Seller: Westbrook Holdings LLC.",
    "2. PROPERTY. 22 Birch Court, Lakeview, OH 44095.",
    "3. PURCHASE PRICE. Three Hundred Ten Thousand Dollars ($310,000), paid in cash at closing.",
    "This offer is not contingent on financing or appraisal.",
    "4. EARNEST MONEY. Buyer shall deliver $5,000 to Northcoast Title Agency within two (2) days",
    "after the Acceptance Date.",
    "5. INSPECTIONS. Buyer's inspection period ends seven (7) days after the Acceptance Date.",
    "6. CLOSING. Closing shall take place on or before October 30, 2026. Possession at closing.",
    "7. TIME PERIODS. " + ROLL[0], ROLL[1],
    "8. ACCEPTANCE. Accepted by both parties on September 28, 2026 (the Acceptance Date).", "",
    RACHEL, "Listing agent: Ana Brooks, Brooks Realty, ana@brooksrealty.example", TITLE_CO,
]
CEDAR = HEAD + [
    "1. PARTIES. Buyer: Grace Park. Sellers: Henry Adams and Lucy Adams.",
    "2. PROPERTY. 9 Cedar Lane, Lakeview, OH 44095.",
    "3. PURCHASE PRICE. Three Hundred Eighty-Nine Thousand Five Hundred Dollars ($389,000).",
    "4. EARNEST MONEY. Buyer shall deliver $50,000 to Northcoast Title Agency within five (5)",
    "business days after the Acceptance Date.",
    "5. FINANCING. FHA loan of $311,200 with a down payment of $77,800. Buyer shall deliver a",
    "written loan commitment within thirty-five (35) days after the Acceptance Date.",
    "6. INSPECTIONS. Buyer's inspection period ends forty-five (45) days after the Acceptance Date.",
    "7. CLOSING. Closing shall take place on or before November 10, 2026.",
    "8. TIME PERIODS. " + ROLL[0], ROLL[1],
    "9. ACCEPTANCE. Accepted by both parties on October 1, 2026 (the Acceptance Date).", "",
    "Buyer's agent: Luis Ortega, Summit Realty, luis@summitrealty.example", TOM, LENDER,
]


def _d(name, term, quote, party) -> dict:
    return {"name": name, "term": term, "quote": quote, "party": party}


def _c(role, name, company, email) -> dict:
    return {"role": role, "name": name, "company": company, "email": email}


RACHEL_C = _c("buyer's agent", "Rachel Moore", "Harbor Realty", "rachel.moore@harborrealty.example")
TOM_C = _c("listing agent", "Tom Reyes", "Lakeview Homes Group", "tom.reyes@lakeviewhomes.example")
LENDER_C = _c("lender", "", "Erie Shore Bank", "loans@erieshorebank.example")
TITLE_C = _c("title", "", "Northcoast Title Agency", "closings@northcoasttitle.example")

TRANSACTIONS = [
    dict(folder="101_maple_ave", files={"contract.pdf": MAPLE, "addendum_1.txt": MAPLE_ADDENDUM}, reading=dict(
        property="101 Maple Avenue, Lakeview, OH 44095", buyers=["Daniel Kim", "Sofia Kim"], sellers=["Patricia Owens"],
        price=425000, price_in_words="Four Hundred Twenty-Five Thousand Dollars", earnest_money=10000,
        acceptance_date="2026-10-02", closing_date="2026-11-25", financing="conventional", loan_amount=340000,
        down_payment=None,
        deadlines=[
            _d("Earnest money", "within three (3) business days after the Acceptance Date", "Buyer shall deliver "
               "earnest money of $10,000 to Northcoast Title Agency within three (3) business days after the "
               "Acceptance Date.", "buyer"),
            _d("Loan commitment", "no later than thirty (30) days after the Acceptance Date", "Buyer shall deliver a "
               "written loan commitment no later than thirty (30) days after the Acceptance Date.", "lender"),
            _d("Appraisal contingency", "twenty-one (21) days after the Acceptance Date", "The appraisal contingency "
               "expires twenty-one (21) days after the Acceptance Date.", "buyer"),
            _d("Inspection objections", "within ten (10) days after the Acceptance Date", "Buyer may inspect the "
               "property and shall deliver any written objections within ten (10) days after the Acceptance Date.",
               "buyer"),
            _d("HOA documents", "within five (5) days after the Acceptance Date", "Seller shall deliver the HOA "
               "documents within five (5) days after the Acceptance Date.", "seller"),
            _d("Title objections", "within seven (7) days after receipt of the title commitment", "Buyer shall deliver "
               "any title objections within seven (7) days after receipt of the title commitment.", "buyer"),
            _d("Final walk-through", "within 24 hours before closing", "Buyer may conduct a final walk-through within "
               "24 hours before closing.", "buyer"),
            _d("Possession", "at closing", "Possession shall be delivered at closing.", "seller")],
        changes=[{"document": "addendum_1.txt", "term": "closing date", "new_value": "2026-11-25",
                  "quote": "The Closing Date in Section 10 is changed to November 25, 2026."}],
        contacts=[RACHEL_C, TOM_C, LENDER_C, TITLE_C]),
        expect=dict(acceptance="2026-10-02", closing="2026-11-25", ready=True, flags=["depends_on_event"],
                    deadlines={"earnest": "2026-10-07", "loan": "2026-11-02", "apprais": "2026-10-23",
                               "inspect": "2026-10-13", "hoa": "2026-10-07", "title": None, "walk": "2026-11-24",
                               "possession": "2026-11-25", "closing": "2026-11-25"})),
    dict(folder="22_birch_ct", files={"contract.pdf": BIRCH}, reading=dict(
        property="22 Birch Court, Lakeview, OH 44095", buyers=["Morgan Ellis"], sellers=["Westbrook Holdings LLC"],
        price=310000, price_in_words="Three Hundred Ten Thousand Dollars", earnest_money=5000,
        acceptance_date="2026-09-28", closing_date="2026-10-30", financing="cash", loan_amount=None, down_payment=None,
        deadlines=[
            _d("Earnest money", "within two (2) days after the Acceptance Date", "Buyer shall deliver $5,000 to "
               "Northcoast Title Agency within two (2) days after the Acceptance Date.", "buyer"),
            _d("Inspection period", "seven (7) days after the Acceptance Date", "Buyer's inspection period ends seven "
               "(7) days after the Acceptance Date.", "buyer"),
            _d("Possession", "at closing", "Closing shall take place on or before October 30, 2026. Possession at "
               "closing.", "seller")],
        changes=[], contacts=[RACHEL_C, _c("listing agent", "Ana Brooks", "Brooks Realty", "ana@brooksrealty.example"),
                              TITLE_C]),
        expect=dict(acceptance="2026-09-28", closing="2026-10-30", ready=True, flags=[],
                    deadlines={"earnest": "2026-09-30", "inspect": "2026-10-05", "possession": "2026-10-30",
                               "closing": "2026-10-30"})),
    dict(folder="9_cedar_ln", files={"contract.pdf": CEDAR}, reading=dict(
        property="9 Cedar Lane, Lakeview, OH 44095", buyers=["Grace Park"], sellers=["Henry Adams", "Lucy Adams"],
        price=389000, price_in_words="Three Hundred Eighty-Nine Thousand Five Hundred Dollars", earnest_money=50000,
        acceptance_date="2026-10-01", closing_date="2026-11-10", financing="FHA", loan_amount=311200,
        down_payment=77800,
        deadlines=[
            _d("Earnest money", "within five (5) business days after the Acceptance Date", "Buyer shall deliver "
               "$50,000 to Northcoast Title Agency within five (5) business days after the Acceptance Date.", "buyer"),
            _d("Loan commitment", "within thirty-five (35) days after the Acceptance Date", "Buyer shall deliver a "
               "written loan commitment within thirty-five (35) days after the Acceptance Date.", "lender"),
            _d("Inspection period", "forty-five (45) days after the Acceptance Date", "Buyer's inspection period ends "
               "forty-five (45) days after the Acceptance Date.", "buyer")],
        changes=[], contacts=[_c("buyer's agent", "Luis Ortega", "Summit Realty", "luis@summitrealty.example"), TOM_C,
                              LENDER_C]),
        expect=dict(acceptance="2026-10-01", closing="2026-11-10", ready=False,
                    flags=["price_words", "after_closing", "earnest_unusual"],
                    deadlines={"earnest": "2026-10-08", "loan": "2026-11-05", "inspect": "2026-11-16",
                               "closing": "2026-11-10"})),
]


def make_sample(out: Path) -> dict:
    """Writes transactions/<folder>/ (contract PDFs and an addendum), tc.toml and truth.json."""
    for t in TRANSACTIONS:
        folder = out / "transactions" / t["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in t["files"].items():
            if name.endswith(".pdf"):
                (folder / name).write_bytes(text_pdf(wrap_page(content)))
            else:
                (folder / name).write_text(content, encoding="utf-8")
    (out / "tc.toml").write_text(TC_TOML, encoding="utf-8")
    truth = {"note": "Answer key for the synthetic transactions made by `python -m txcoord sample`. Fictional parties. "
                     f"Run with --today {TODAY}. `reading` is what a perfect model returns; `expect` is what the code "
                     "should then work out.",
             "today": TODAY,
             "transactions": [{"folder": t["folder"], "reading": t["reading"], "expect": t["expect"]}
                              for t in TRANSACTIONS]}
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(results: dict, truth: dict) -> dict:
    pred = {t["folder"]: t for t in results["transactions"]}
    out = {"files": 0, "anchors_right": 0, "wrong_anchors": [], "deadlines": 0, "deadlines_right": 0,
           "wrong_deadlines": [], "ready_right": 0, "wrong_ready": [], "flags_expected": 0, "flags_found": 0,
           "missed_flags": [], "extra_holds": []}
    for t in truth["transactions"]:
        p = pred.get(t["folder"], {"deadlines": [], "flags": [], "ready": False})
        e = t["expect"]
        out["files"] += 1
        got = (str(p.get("acceptance") or ""), str(p.get("closing") or ""))
        if got == (e["acceptance"], e["closing"]):
            out["anchors_right"] += 1
        else:
            out["wrong_anchors"].append(f"{t['folder']}: acceptance {got[0] or '?'}, closing {got[1] or '?'} "
                                        f"(expected {e['acceptance']}, {e['closing']})")
        for key, due in e["deadlines"].items():
            out["deadlines"] += 1
            match = [d for d in p["deadlines"] if re.search(key, d["name"], re.I)]
            if key == "closing":
                match = [d for d in match if d["name"] == "Closing"]
            found = str(match[0]["due"]) if match and match[0]["due"] else None
            if match and found == due:
                out["deadlines_right"] += 1
            else:
                out["wrong_deadlines"].append(f"{t['folder']} {key}: {found if match else 'missing'} "
                                              f"(expected {due or 'no date'})")
        if bool(p["ready"]) == e["ready"]:
            out["ready_right"] += 1
        else:
            out["wrong_ready"].append(f"{t['folder']}: {'ready' if p['ready'] else 'held'}")
        flags = p["flags"] + [f for d in p["deadlines"] for f in d["flags"]]
        codes = {f["code"] for f in flags}
        out["flags_expected"] += len(e["flags"])
        out["flags_found"] += len(set(e["flags"]) & codes)
        out["missed_flags"] += [f"{t['folder']}: {c}" for c in e["flags"] if c not in codes]
        out["extra_holds"] += [f"{t['folder']}: {f['text']}" for f in flags if f["hold"] and f["code"] not in e["flags"]]
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = [f"Acceptance and closing dates right: {_pct(s['anchors_right'], s['files'])}"]
    lines += [f"  {w}" for w in s["wrong_anchors"]]
    lines.append(f"Deadlines worked out right: {_pct(s['deadlines_right'], s['deadlines'])}")
    lines += [f"  {w}" for w in s["wrong_deadlines"]]
    lines.append(f"Files ready / held as expected: {_pct(s['ready_right'], s['files'])}")
    lines += [f"  {w}" for w in s["wrong_ready"]]
    lines.append(f"Expected checks raised: {_pct(s['flags_found'], s['flags_expected'])}; other must-fix checks: "
                 f"{len(s['extra_holds'])}")
    lines += [f"  missed: {m}" for m in s["missed_flags"]] + [f"  other: {m}" for m in s["extra_holds"]]
    return "\n".join(lines)
